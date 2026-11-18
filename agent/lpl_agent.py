#!/usr/bin/env python3
"""LightPhotoLibrary PC agent — hands photos from the NAS library to darktable.

Runs in the background on the PC where darktable is installed (Windows first, Linux too).
Only needs Python 3.9+ standard library.

  python lpl_agent.py --setup            first-time setup (server address, darktable, bridge script)
  python lpl_agent.py                    run (Windows: use pythonw.exe so no console window shows)
  python lpl_agent.py --status           show configuration and the working set
  python lpl_agent.py --install-bridge   (re)install the darktable Lua bridge script

What it does, in a loop every couple of seconds:
  1. heartbeat to the server: reports its working set; receives jobs ("edit", "sync", "release", "open")
  2. edit: downloads RAW + XMP from the server into the local working set and opens it in darktable
  3. watches the local XMP files; when darktable changes one (or the bridge says you left the image),
     uploads it to the server, which writes it next to the original on the NAS
"""
import argparse
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Queue

VERSION = "0.1.0"
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW


# ------------------------------------------------------------------ paths
def data_dir() -> Path:
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "LightPhotoLibrary"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "lightphotolibrary"


def config_path() -> Path:
    if IS_WIN:
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "LightPhotoLibrary" / "agent.json"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "lightphotolibrary" / "agent.json"


def darktable_config_dir(cfg: dict) -> Path:
    if cfg.get("darktable_configdir"):
        return Path(cfg["darktable_configdir"])
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA", "")) / "darktable"
    if cfg.get("darktable") == "flatpak":
        return Path.home() / ".var" / "app" / "org.darktable.Darktable" / "config" / "darktable"
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")) / "darktable"


BRIDGE = data_dir() / "bridge"
WORK = data_dir() / "work"
STATE_FILE = data_dir() / "state.json"
LOG_FILE = data_dir() / "agent.log"
log = logging.getLogger("lpl-agent")


# ------------------------------------------------------------------ config
def find_darktable() -> str:
    cands = []
    if IS_WIN:
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands.append(Path(base) / "darktable" / "bin" / "darktable.exe")
                cands.append(Path(base) / "Programs" / "darktable" / "bin" / "darktable.exe")
    w = shutil.which("darktable")
    if w:
        cands.insert(0, Path(w))
    for c in cands:
        if c.is_file():
            return str(c)
    if not IS_WIN and shutil.which("flatpak"):
        try:
            out = subprocess.run(["flatpak", "info", "org.darktable.Darktable"], capture_output=True)
            if out.returncode == 0:
                return "flatpak"
        except Exception:
            pass
    return ""


def load_config() -> dict:
    p = config_path()
    cfg = {}
    if p.is_file():
        cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg.setdefault("id", str(uuid.uuid4()))
    cfg.setdefault("name", socket.gethostname())
    cfg.setdefault("server", "http://nas:8080")
    cfg.setdefault("password", "")
    cfg.setdefault("darktable", "")
    cfg.setdefault("library", "dedicated")   # dedicated | memory
    cfg.setdefault("auto_sync_seconds", 8)
    cfg.setdefault("poll_seconds", 2)
    return cfg


def save_config(cfg: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ http
class Server:
    def __init__(self, cfg: dict):
        self.base = cfg["server"].rstrip("/")
        self.headers = {"x-lpl-token": cfg["password"]} if cfg.get("password") else {}

    def _req(self, method, path, data=None, ctype=None, timeout=30):
        h = dict(self.headers)
        if ctype:
            h["content-type"] = ctype
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        return urllib.request.urlopen(req, timeout=timeout)

    def json(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        with self._req(method, path, data, "application/json" if data else None, timeout) as r:
            return json.loads(r.read().decode() or "{}")

    def download(self, path, dest: Path):
        tmp = dest.with_name(dest.name + ".part")
        with self._req("GET", path, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1024 * 1024)
        os.replace(tmp, dest)

    def upload(self, path, data: bytes, ctype: str):
        with self._req("POST", path, data, ctype, timeout=120) as r:
            return r.read(), dict(r.headers)


# ------------------------------------------------------------------ darktable
def darktable_cmd(cfg: dict) -> list:
    dtp = cfg.get("darktable") or find_darktable()
    if not dtp:
        raise RuntimeError("darktable not found. Set its path with --setup.")
    if dtp == "flatpak":
        return ["flatpak", "run", "org.darktable.Darktable"]
    return [dtp]


def darktable_running() -> bool:
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq darktable.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, creationflags=NO_WINDOW)
            return "darktable.exe" in out.stdout.lower()
        out = subprocess.run(["pgrep", "-x", "darktable"], capture_output=True)
        return out.returncode == 0
    except Exception:
        return False


def bridge_alive() -> bool:
    try:
        return time.time() - (BRIDGE / "alive").stat().st_mtime < 12
    except OSError:
        return False


def bridge_status(running: bool) -> str:
    """What lpl_bridge.lua wrote at darktable's startup (shown on the Working set page)."""
    p = BRIDGE / "status.txt"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        return "\n".join(lines[-6:])
    except OSError:
        if running:
            return ("No status from the bridge script: darktable did not start it. In darktable: lighttable > "
                    "'scripts' module > folder 'lightphotolibrary' > start 'lpl_bridge', then wait a few seconds.")
        return ""


def launch_darktable(cfg: dict, image: Path) -> None:
    lib = ":memory:" if cfg.get("library") == "memory" else str(data_dir() / "darktable-working-set.db")
    cmd = darktable_cmd(cfg) + ["--library", lib, str(image)]
    log.info("starting darktable: %s", cmd)
    kw = {}
    if IS_WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **kw)


class BridgeQueue:
    """Commands for the Lua bridge. Written to request.txt only when the previous batch was consumed."""

    def __init__(self):
        self.pending = []
        self.lock = threading.Lock()

    def add(self, cmd: str, path: Path):
        with self.lock:
            self.pending.append("%s\t%s" % (cmd, path))

    def flush(self):
        req = BRIDGE / "request.txt"
        with self.lock:
            if not self.pending or req.exists():
                return
            tmp = BRIDGE / "request.tmp"
            tmp.write_text("\n".join(self.pending) + "\n", encoding="utf-8")
            os.replace(tmp, req)
            self.pending = []


def install_bridge(cfg: dict) -> Path:
    """Copy lpl_bridge.lua into <darktable config>/lua/lightphotolibrary/ and load it from luarc.
    (Own folder, so darktable's script manager lists it as 'lightphotolibrary'.)"""
    src = Path(__file__).resolve().parent / "darktable" / "lpl_bridge.lua"
    if not src.is_file():  # frozen exe: lua file shipped next to it
        src = Path(sys.executable).resolve().parent / "darktable" / "lpl_bridge.lua"
    conf = darktable_config_dir(cfg)
    folder = conf / "lua" / "lightphotolibrary"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, folder / "lpl_bridge.lua")
    old_copy = conf / "lua" / "lpl_bridge.lua"
    if old_copy.is_file():
        old_copy.unlink()
    luarc = conf / "luarc"
    lines = luarc.read_text(encoding="utf-8").splitlines() if luarc.is_file() else []
    lines = [l for l in lines if "lpl_bridge" not in l]
    lines.append('require "lightphotolibrary/lpl_bridge"')
    luarc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (BRIDGE / "outbox").mkdir(parents=True, exist_ok=True)
    return folder


# ------------------------------------------------------------------ working set
class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.srv = Server(cfg)
        self.items = {}           # photo id (str) -> dict
        self.jobs = Queue()
        self.bridge = BridgeQueue()
        self.message = ""
        self.lock = threading.RLock()
        for d in (WORK, BRIDGE / "outbox"):
            d.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.is_file():
            try:
                self.items = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.items = {}

    def save(self):
        with self.lock:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, indent=1), encoding="utf-8")
            os.replace(tmp, STATE_FILE)

    def state(self):
        with self.lock:
            ws = [{k: v for k, v in it.items() if k in ("id", "filename", "status", "checkout_ts", "synced_ts", "error")}
                  for it in sorted(self.items.values(), key=lambda x: x.get("checkout_ts", 0))]
        running = darktable_running()
        return {"working_set": ws, "darktable_running": running, "bridge_alive": bridge_alive(),
                "bridge_status": bridge_status(running), "version": VERSION, "message": self.message}

    # --- jobs
    def handle(self, job):
        kind, ids = job["kind"], [str(i) for i in (job.get("payload") or {}).get("ids") or []]
        if (job.get("payload") or {}).get("all"):
            ids = list(self.items.keys())
        log.info("job %s %s %s", job["id"], kind, ids)
        status = "done"
        try:
            if kind == "edit":
                self.edit(ids)
            elif kind == "open":
                self.open_local(ids)
            elif kind == "sync":
                for i in ids:
                    self.sync(i, force=True)
            elif kind == "release":
                for i in ids:
                    self.release(i)
        except Exception as e:
            log.exception("job failed")
            self.message = "Last job failed: %s" % e
            status = "failed"
        try:
            self.srv.json("POST", "/api/jobs/%s/done" % job["id"], {"status": status})
        except Exception:
            pass

    def local_path(self, pid: str, filename: str) -> Path:
        return WORK / ("%s_%s" % (pid, filename))

    def edit(self, ids):
        opened = []
        for pid in ids:
            with self.lock:
                it = self.items.get(pid)
            if it and Path(it["local_path"]).is_file():
                opened.append(Path(it["local_path"]))
                continue
            info = self.srv.json("GET", "/api/photos/%s/checkout" % pid)
            dest = self.local_path(pid, info["filename"])
            it = {"id": int(pid), "filename": info["filename"], "local_path": str(dest), "status": "downloading",
                  "checkout_ts": info["server_time"], "synced_mtime": 0, "synced_ts": 0, "error": ""}
            with self.lock:
                self.items[pid] = it
            try:
                if not (dest.is_file() and dest.stat().st_size == info.get("size")):
                    self.srv.download("/api/photos/%s/original" % pid, dest)
                xmp = Path(str(dest) + ".xmp")
                self.srv.download("/api/photos/%s/xmp" % pid, xmp)
                it["synced_mtime"] = xmp.stat().st_mtime
                it["status"] = "opening"
                opened.append(dest)
            except Exception as e:
                it.update(status="error", error=str(e))
            self.save()
        self.open_paths(opened)

    def open_local(self, ids):
        paths = []
        for pid in ids:
            it = self.items.get(pid)
            if it and Path(it["local_path"]).is_file():
                paths.append(Path(it["local_path"]))
            else:
                self.edit([pid])
        self.open_paths(paths)

    def open_paths(self, paths):
        if not paths:
            return
        running = darktable_running()
        if running and bridge_alive():
            self.bridge.add("open", paths[0])
            for p in paths[1:]:
                self.bridge.add("import", p)
            self.bridge.flush()
        elif running and not IS_WIN:
            # Linux: a second 'darktable <file>' hands the file to the running instance over D-Bus
            launch_darktable(self.cfg, paths[0])
            for p in paths[1:]:
                self.bridge.add("import", p)
        elif running:
            self.message = ("darktable is open but the LightPhotoLibrary bridge script is not active. "
                            "Run the agent with --install-bridge, then restart darktable.")
            for p in paths:
                self.bridge.add("import", p)
            self.bridge.add("open", paths[0])
        else:
            launch_darktable(self.cfg, paths[0])
            for p in paths[1:]:
                self.bridge.add("import", p)
        with self.lock:
            for it in self.items.values():
                if it["status"] == "opening":
                    it["status"] = "editing"
        self.save()

    def sync(self, pid: str, force=False, preview: Path = None):
        it = self.items.get(pid)
        if not it:
            return
        xmp = Path(it["local_path"] + ".xmp")
        if preview is not None and preview.is_file():
            try:
                self.srv.upload("/api/photos/%s/preview" % pid, preview.read_bytes(), "image/jpeg")
            except Exception as e:
                log.warning("preview upload failed: %s", e)
        if not xmp.is_file():
            return
        mtime = xmp.stat().st_mtime
        if not force and abs(mtime - it.get("synced_mtime", 0)) < 0.01:
            return
        it["status"] = "syncing"
        try:
            body, headers = self.srv.upload("/api/photos/%s/xmp?checkout_ts=%s" % (pid, it["checkout_ts"]), xmp.read_bytes(),
                                            "application/rdf+xml")
            merged = (headers.get("X-LPL-Merged") or headers.get("x-lpl-merged")) == "1"
            if merged:
                # LPL had newer rating/labels/tags: put them into the local file and tell darktable to re-read it
                xmp.write_bytes(body)
                self.bridge.add("reload", Path(it["local_path"]))
            it.update(status="synced", synced_mtime=xmp.stat().st_mtime, synced_ts=time.time(), error="")
            log.info("synced %s%s", it["filename"], " (merged)" if merged else "")
        except Exception as e:
            it.update(status="error", error="sync failed: %s" % e)
        self.save()

    def release(self, pid: str):
        it = self.items.get(pid)
        if not it:
            return
        self.sync(pid)
        if it["status"] == "error":
            return
        p = Path(it["local_path"])
        if darktable_running():
            self.bridge.add("forget", p)
            self.bridge.flush()
            time.sleep(1.5)
        try:
            for f in (Path(str(p) + ".xmp"), p):
                if f.exists():
                    f.unlink()
            with self.lock:
                self.items.pop(pid, None)
        except OSError as e:
            it.update(status="error", error="could not delete local copy (still open in darktable?): %s" % e)
        self.save()

    # --- watching
    def watch(self):
        outbox = BRIDGE / "outbox"
        for marker in list(outbox.glob("*.done")):
            try:
                target = marker.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            name = marker.name[:-5]
            pid = next((k for k, it in self.items.items() if Path(it["local_path"]).name == name or it["local_path"] == target), None)
            jpg = outbox / (name + ".jpg")
            if pid:
                self.sync(pid, force=True, preview=jpg)
            for f in (marker, jpg):
                try:
                    f.unlink()
                except OSError:
                    pass
        now = time.time()
        for pid, it in list(self.items.items()):
            xmp = Path(it["local_path"] + ".xmp")
            if it["status"] in ("downloading", "syncing", "error") or not xmp.is_file():
                continue
            m = xmp.stat().st_mtime
            if abs(m - it.get("synced_mtime", 0)) > 0.01:
                if it["status"] != "dirty":
                    it["status"] = "dirty"
                    self.save()
                if self.cfg.get("auto_sync_seconds", 8) > 0 and now - m >= self.cfg["auto_sync_seconds"]:
                    self.sync(pid)
        self.bridge.flush()

    # --- main loop
    def worker(self):
        while True:
            job = self.jobs.get()
            self.handle(job)

    def run(self, once=False):
        threading.Thread(target=self.worker, daemon=True).start()
        log.info("agent %s (%s) talking to %s", self.cfg["name"], self.cfg["id"], self.srv.base)
        backoff = self.cfg.get("poll_seconds", 2)
        while True:
            try:
                r = self.srv.json("POST", "/api/agents/heartbeat",
                                  {"id": self.cfg["id"], "name": self.cfg["name"], "os": platform.system().lower(), "state": self.state()},
                                  timeout=15)
                for j in r.get("jobs", []):
                    self.jobs.put(j)
                backoff = self.cfg.get("poll_seconds", 2)
                if self.message.startswith("Server unreachable"):
                    self.message = ""
            except Exception as e:
                self.message = "Server unreachable: %s" % e
                log.warning(self.message)
                backoff = min(30, backoff * 2)
            try:
                self.watch()
            except Exception:
                log.exception("watch failed")
            if once:
                time.sleep(0.5)
                while not self.jobs.empty():
                    time.sleep(0.2)
                time.sleep(0.5)
                try:  # report the final state
                    self.srv.json("POST", "/api/agents/heartbeat", {"id": self.cfg["id"], "name": self.cfg["name"],
                                                                     "os": platform.system().lower(), "state": self.state()}, timeout=15)
                except Exception:
                    pass
                return
            time.sleep(backoff)


# ------------------------------------------------------------------ cli
def normalize_server(v: str) -> str:
    """'192.168.1.50' -> 'http://192.168.1.50:8080' (LightPhotoLibrary's default port)."""
    v = v.strip().rstrip("/")
    if "://" not in v:
        v = "http://" + v
    parts = urllib.parse.urlsplit(v)
    if parts.port is None and parts.scheme == "http":
        v = "%s://%s:8080%s" % (parts.scheme, parts.hostname, parts.path)
    return v


def setup(cfg: dict) -> None:
    print("LightPhotoLibrary agent setup\n")
    v = input("Server address, e.g. http://192.168.1.50:8080 [%s]: " % cfg["server"]).strip()
    if v:
        cfg["server"] = normalize_server(v)
        print("Using %s" % cfg["server"])
    v = input("Password (empty if none) [%s]: " % ("*****" if cfg["password"] else "")).strip()
    if v:
        cfg["password"] = v
    v = input("Name of this PC [%s]: " % cfg["name"]).strip()
    if v:
        cfg["name"] = v
    found = cfg.get("darktable") or find_darktable()
    v = input("darktable program [%s]: " % (found or "not found")).strip().strip('"')
    cfg["darktable"] = v or found
    save_config(cfg)
    try:
        st = Server(cfg).json("GET", "/api/status")
        print("\nConnected: %s photos in the library." % st.get("photos"))
    except Exception as e:
        msg = str(e)
        if "CERTIFICATE" in msg or "SSL" in msg:
            msg = ("that address answered with HTTPS, so it is probably the NAS's own admin page, not LightPhotoLibrary. "
                   "LightPhotoLibrary listens on port 8080: use http://<nas-address>:8080")
        elif "refused" in msg.lower() or "timed out" in msg.lower():
            msg += " - is the LightPhotoLibrary container running? Try the address in a browser."
        print("\nCould not reach the server: %s\nRun the setup again, or fix the address in %s" % (msg, config_path()))
    try:
        conf = install_bridge(cfg)
        print("darktable bridge script installed in %s" % conf)
        print("Restart darktable. If you use darktable's script manager: lighttable > scripts > folder")
        print("'lightphotolibrary' > start 'lpl_bridge' (only once, it is remembered).")
    except Exception as e:
        print("Could not install the bridge script: %s" % e)
    print("\nSaved to %s" % config_path())


def main():
    ap = argparse.ArgumentParser(description="LightPhotoLibrary PC agent")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--install-bridge", action="store_true")
    ap.add_argument("--once", action="store_true", help="one heartbeat + jobs, then exit (testing)")
    ap.add_argument("--server")
    a = ap.parse_args()
    data_dir().mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stdout and sys.stdout.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    cfg = load_config()
    if a.server:
        cfg["server"] = a.server
    if not config_path().is_file():
        save_config(cfg)
    if a.setup:
        setup(cfg)
        return
    if a.install_bridge:
        print("installed in", install_bridge(cfg))
        return
    if a.status:
        print(json.dumps({"config": {k: v for k, v in cfg.items() if k != "password"}, "config_file": str(config_path()),
                          "data": str(data_dir()), "darktable": cfg.get("darktable") or find_darktable(),
                          "state": Agent(cfg).state()}, indent=2))
        return
    Agent(cfg).run(once=a.once)


if __name__ == "__main__":
    main()
