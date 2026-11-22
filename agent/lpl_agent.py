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
  2. edit: downloads RAW + XMP from the server into the local working set and opens it in darktable.
     An edit job is a batch: the agent reports how far it is with every heartbeat, and stops it (or leaves
     single photos out) when the heartbeat answer says so; see docs/devices-stop-estimate.md
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
# What this agent can do beyond the first protocol, announced in every heartbeat (docs/devices-stop-estimate.md):
# batch = reports how far each edit job is; cancel = stops a job or leaves out photos when the heartbeat answer
# says so; open = honours "open": false in an edit job (copy the photos, do not open darktable).
CAPS = ["batch", "cancel", "open"]
CHUNK = 256 * 1024  # most bytes taken from a download at once; a Stop is noticed between two of them
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
    cfg.setdefault("local_library", True)   # this PC's own library (works without the NAS), at http://localhost:<local_port>
    cfg.setdefault("local_port", 8090)
    return cfg


def save_config(cfg: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ http
class Cancelled(Exception):
    """A download was given up on request: Stop, or "Don't send" for that photo."""


class Server:
    def __init__(self, cfg: dict):
        self.base = cfg["server"].rstrip("/")
        self.headers = {"x-lpl-token": cfg["password"]} if cfg.get("password") else {}
        self.current = None  # (response, tag) of the download under way, for interrupt()

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

    def download(self, path, dest: Path, progress=None, cancel=None, tag=None):
        """progress(fraction, bytes) is called while the file arrives (fraction None when the server does not
        say how big it is). cancel() is asked between chunks: when it says yes the download ends with Cancelled.
        The file appears at dest only when it is complete; whatever ends the download early, the partial file
        (dest + ".part") is removed. tag names the download for interrupt()."""
        tmp = dest.with_name(dest.name + ".part")
        try:
            with self._req("GET", path, timeout=300) as r, open(tmp, "wb") as f:
                try:
                    total = int(r.headers.get("Content-Length") or 0)
                except ValueError:
                    total = 0
                done = 0
                self.current = (r, tag)
                read = getattr(r, "read1", None) or r.read  # read1: what has arrived, without waiting for a full chunk
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    try:
                        chunk = read(CHUNK)
                    except Exception:
                        if cancel and cancel():  # interrupt() cut the connection
                            raise Cancelled()
                        raise
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(min(1.0, done / total) if total else None, done)
                if cancel and cancel():
                    raise Cancelled()
                if total and done != total:
                    raise IOError("the connection ended after %d of %d bytes" % (done, total))
            os.replace(tmp, dest)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        finally:
            self.current = None

    def interrupt(self, wanted) -> None:
        """Wake the download under way if wanted(tag) says it is to be given up: a read that waits for data on a
        slow or stalled connection would otherwise notice a Stop only when the next bytes arrive. Best effort
        (http.client has no public handle to its socket): without it the Stop takes effect at the next chunk."""
        cur = self.current
        if not cur or not wanted(cur[1]):
            return
        try:
            cur[0].fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

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
        return "\n".join(lines[-10:])
    except OSError:
        if running:
            return ("No status from the bridge script: darktable did not start it. In darktable: lighttable > "
                    "'scripts' module > folder 'lightphotolibrary' > start 'lpl_bridge', then wait a few seconds.")
        return ""


_last_launch = [0.0]


def launch_darktable(cfg: dict, image: Path, guard: bool = True) -> bool:
    """Start darktable with this photo. Never twice within a minute (darktable takes a while to show up,
    and a second copy would open another window). Returns False if it didn't start one."""
    if guard and time.time() - _last_launch[0] < 60:
        log.info("darktable was just started; not starting another one")
        return False
    _last_launch[0] = time.time()
    lib = ":memory:" if cfg.get("library") == "memory" else str(data_dir() / "darktable-working-set.db")
    cmd = darktable_cmd(cfg) + ["--library", lib, str(image)]
    log.info("starting darktable: %s", cmd)
    kw = {}
    if IS_WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **kw)
    return True


class BridgeQueue:
    """Commands for the Lua bridge. Written to request.txt only when the previous batch was consumed."""

    def __init__(self):
        self.pending = []
        self.lock = threading.Lock()

    def add(self, cmd: str, path: Path):
        """Queue a command. Only the latest 'open' stays an open (earlier ones become imports),
        and repeated commands for the same file are merged, so a backlog doesn't open a pile of photos."""
        line = "%s\t%s" % (cmd, path)
        with self.lock:
            if cmd == "open":
                self.pending = [("import\t" + l.split("\t", 1)[1]) if l.startswith("open\t") else l for l in self.pending]
            self.pending = [l for l in self.pending if l.split("\t", 1)[1] != str(path) or l.split("\t", 1)[0] not in ("open", "import", cmd)]
            self.pending.append(line)

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
    if not any("script_manager" in l and not l.strip().startswith("--") for l in lines):
        # no script manager: load it directly from luarc
        lines.append('require "lightphotolibrary/lpl_bridge"')
    # with darktable's script manager, it must NOT also be in luarc: the script manager starts it
    # (lighttable > scripts > lightphotolibrary > lpl_bridge, remembered across restarts), and a copy
    # loaded from luarc can't be stopped or restarted there.
    luarc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (BRIDGE / "outbox").mkdir(parents=True, exist_ok=True)
    return folder


# ------------------------------------------------------------------ local library (LightPhotoLibrary server on this PC)
LOCAL = {"url": "", "error": ""}


def _bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _local_env(cfg: dict, port: int) -> dict:
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    exiftool = ""
    if exe_dir:
        for cand in (exe_dir / "exiftool" / "exiftool.exe", exe_dir.parent / "exiftool" / "exiftool.exe"):
            if cand.is_file():
                exiftool = str(cand)
                break
    web = _bundle_dir() / "web_dist"
    if not web.is_dir():
        for cand in (Path(__file__).resolve().parent.parent / "server" / "web_dist",
                     Path(__file__).resolve().parent.parent / "web" / "dist"):
            if cand.is_dir():
                web = cand
                break
    env = {
        "LPL_MODE": "local", "DATA_DIR": str(data_dir() / "local-library"), "HOST": "127.0.0.1", "PORT": str(port),
        "LPL_NAS_URL": cfg.get("server", ""), "LPL_NAS_PASSWORD": cfg.get("password", ""),
        "LPL_DARKTABLE": cfg.get("darktable") or find_darktable(), "LPL_DT_LIBRARY": str(data_dir() / "darktable-working-set.db"),
        "LPL_BRIDGE_DIR": str(BRIDGE), "SCAN_INTERVAL": "120", "WEB_DIR": str(web), "LPL_PASSWORD": "",
    }
    if exiftool:
        env["EXIFTOOL"] = exiftool
    return env


def start_local_library(cfg: dict) -> None:
    """Run the local library in this process (a background thread)."""
    if not cfg.get("local_library", True):
        return
    port = int(cfg.get("local_port", 8090))
    os.environ.update(_local_env(cfg, port))
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
    try:
        import uvicorn
        from lpl import main as lpl_main
        app = lpl_main.create_app()
    except Exception as e:
        LOCAL["error"] = "local library not available: %s" % e
        log.warning(LOCAL["error"])
        return

    def serve():
        try:
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
            server.run()
        except BaseException as e:  # e.g. port in use
            LOCAL.update(url="", error="local library stopped: %s" % e)
            log.warning(LOCAL["error"])
    threading.Thread(target=serve, name="local-library", daemon=True).start()
    LOCAL["url"] = "http://localhost:%d" % port
    log.info("local library at %s", LOCAL["url"])


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
        self.batches = {}          # edit job id -> how far it is (reported with every heartbeat, see batch())
        self.cancel_jobs = set()   # jobs the server said to stop, and photos (ids as text) it said not to send:
        self.skip_photos = set()   # both as in the last heartbeat answer
        self.quit = threading.Event()  # set: run() returns after the round it is in (tests)
        for d in (WORK, BRIDGE / "outbox"):
            d.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.is_file():
            try:
                self.items = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.items = {}
        for it in self.items.values():  # whatever was under way when the agent stopped is not any more
            it.pop("step", None)
            it.pop("progress", None)

    def save(self):
        with self.lock:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, indent=1), encoding="utf-8")
            os.replace(tmp, STATE_FILE)

    def state(self):
        with self.lock:
            ws = [{k: v for k, v in it.items() if k in ("id", "filename", "status", "checkout_ts", "synced_ts", "error", "step", "progress")}
                  for it in sorted(self.items.values(), key=lambda x: x.get("checkout_ts", 0))]
        running = darktable_running()
        return {"working_set": ws, "darktable_running": running, "bridge_alive": bridge_alive(),
                "bridge_status": bridge_status(running), "version": VERSION, "message": self.message,
                "local_url": LOCAL["url"], "local_error": LOCAL["error"]}

    def recover(self):
        """At start: whatever was being copied when the agent last stopped never arrived. Its partial file goes,
        and so does its entry, so the working set lists only photos that are really here. (The server sees that
        this agent no longer has the job and shows the batch as stopped, with what did arrive.)"""
        with self.lock:
            for pid, it in list(self.items.items()):
                if it.get("status") != "downloading":
                    continue
                p = Path(it["local_path"])
                if p.is_file() and not Path(str(p) + ".xmp").exists():
                    try:  # the photo without its XMP: nothing was edited, the copy is only half of a hand-off
                        p.unlink()
                    except OSError:
                        pass
                self.items.pop(pid, None)
        for part in WORK.glob("*.part"):
            try:
                part.unlink()
            except OSError:
                pass
        self.save()

    def heartbeat_body(self):
        return {"id": self.cfg["id"], "name": self.cfg["name"], "os": platform.system().lower(), "state": self.state(),
                "caps": CAPS, "transfers": self.transfers()}

    # --- batches: how far each edit job is
    def transfers(self):
        """Every edit job this agent holds, for the heartbeat: waiting for its turn ("queued"), being copied
        ("running"), or ended ("stopped", "done", "failed") but not yet confirmed to the server."""
        with self.lock:
            return [{"job": jid, "state": b["state"], "arrived": list(b["arrived"]), "present": list(b["present"]),
                     "failed": list(b["failed"]), "skipped": list(b["skipped"]), "photo": b["photo"], "bytes": b["bytes"]}
                    for jid, b in self.batches.items()]

    def _mark(self, b, key, pid):
        if b is not None:
            with self.lock:
                b[key].append(int(pid))

    def _copying(self, b, pid=None, nbytes=0):
        if b is not None:
            with self.lock:
                b["photo"], b["bytes"] = (int(pid) if pid is not None else None), int(nbytes)

    def _given_up(self, jid, pid) -> bool:
        """Did the server say to stop this job, or not to send this photo?"""
        return (jid is not None and jid in self.cancel_jobs) or str(pid) in self.skip_photos

    def take(self, answer: dict):
        """A heartbeat answer: new jobs, and what to stop. Stop does not wait in the job queue (the worker is
        busy with the very batch it is about): it is noted here and the copy loop looks at it between files and
        between chunks."""
        for j in answer.get("jobs", []):
            if j.get("kind") == "edit":
                with self.lock:
                    if j["id"] in self.batches:
                        continue  # handed out again while this agent still has it
                    self.batches[j["id"]] = {"state": "queued", "arrived": [], "present": [], "failed": [], "skipped": [],
                                             "photo": None, "bytes": 0, "unsaid": False}
            self.jobs.put(j)
        c = answer.get("cancel")
        if isinstance(c, dict):  # the whole list every time: what is not in it any more is not to be stopped
            self.cancel_jobs = set(c.get("jobs") or [])
            self.skip_photos = {str(i) for i in c.get("photos") or []}
            self.srv.interrupt(lambda tag: bool(tag) and self._given_up(tag[0], tag[1]))

    def report_done(self, jid, status) -> bool:
        """Tell the server a job ended. For an edit job with its lists, so the answer does not depend on which
        of this and the next heartbeat arrives first. False: not delivered (the main loop tries again)."""
        body = {"status": status}
        with self.lock:
            b = self.batches.get(jid)
            if b:
                body.update({k: list(b[k]) for k in ("arrived", "present", "failed", "skipped")})
        try:
            self.srv.json("POST", "/api/jobs/%s/done" % jid, body)
        except Exception:
            return False
        with self.lock:
            self.batches.pop(jid, None)
        return True

    # --- jobs
    def handle(self, job):
        kind, ids = job["kind"], [str(i) for i in (job.get("payload") or {}).get("ids") or []]
        if (job.get("payload") or {}).get("all"):
            ids = list(self.items.keys())
        log.info("job %s %s %s", job["id"], kind, ids)
        status = "done"
        try:
            if kind == "edit":
                status = self.edit(ids, job) or "done"
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
        with self.lock:
            b = self.batches.get(job["id"])
            if b:
                b.update(state=status, photo=None, bytes=0)
        if not self.report_done(job["id"], status) and b:
            b["unsaid"] = True  # the server could not be reached: the main loop says it after its next heartbeat

    def step(self, it: dict, step=None, progress=None):
        """What the agent is doing with a photo right now, reported with the next heartbeat (R16): step is
        "downloading", "sending edit" or "sending preview", progress 0..1 where known; None = nothing."""
        with self.lock:
            if step is None:
                it.pop("step", None)
            else:
                it["step"] = step
            if progress is None:
                it.pop("progress", None)
            else:
                it["progress"] = round(progress, 3)

    def local_path(self, pid: str, filename: str) -> Path:
        return WORK / ("%s_%s" % (pid, filename))

    def edit(self, ids, job=None):
        """Copy these photos (RAW + XMP) into the working set, then open them in darktable. job: the server's
        edit job, when this is one (a batch: reported with every heartbeat, and it can be stopped).
        Returns "stopped" when the server said Stop: what arrived stays, the photo that was being copied leaves
        no trace, and darktable is not opened. A photo is "ready" from the moment its RAW and XMP are both here."""
        jid = job["id"] if job else None
        with self.lock:
            b = self.batches.get(jid) if jid is not None else None
            if b:
                b["state"] = "running"
        want_open = (job.get("payload") or {}).get("open", True) is not False if job else True
        opened = []
        for pid in ids:
            if jid is not None and jid in self.cancel_jobs:
                return "stopped"
            if str(pid) in self.skip_photos:
                self._mark(b, "skipped", pid)
                continue
            with self.lock:
                it = self.items.get(pid)
            if it and Path(it["local_path"]).is_file() and it.get("status") != "downloading":
                opened.append(Path(it["local_path"]))
                self._mark(b, "present", pid)
                continue
            try:
                info = self.srv.json("GET", "/api/photos/%s/checkout" % pid)
            except Exception as e:
                if b is None:
                    raise  # (an "open" of a photo that is not here: as before, the job fails)
                log.warning("photo %s: %s", pid, e)
                self.message = "Could not copy photo %s: %s" % (pid, e)
                self._mark(b, "failed", pid)
                continue
            dest = self.local_path(pid, info["filename"])
            it = {"id": int(pid), "filename": info["filename"], "local_path": str(dest), "status": "downloading",
                  "checkout_ts": info["server_time"], "synced_mtime": 0, "synced_ts": 0, "error": ""}
            with self.lock:
                self.items[pid] = it
            made = False  # this call put the RAW there (a copy found complete from before is left alone)
            given_up = lambda jid=jid, pid=pid: self._given_up(jid, pid)

            def progress(f, n, it=it, pid=pid):
                self.step(it, "downloading", f)
                self._copying(b, pid, n)
            try:
                if not (dest.is_file() and dest.stat().st_size == info.get("size")):
                    self.step(it, "downloading", 0.0)
                    self._copying(b, pid, 0)
                    made = True
                    self.srv.download("/api/photos/%s/original" % pid, dest, progress, given_up, (jid, str(pid)))
                xmp = Path(str(dest) + ".xmp")
                self.srv.download("/api/photos/%s/xmp" % pid, xmp, None, given_up, (jid, str(pid)))
                it["synced_mtime"] = xmp.stat().st_mtime
                it["status"] = "ready"
                opened.append(dest)
                self._mark(b, "arrived", pid)
            except Cancelled:
                # it did not arrive: no entry, no partial file (download removed it), no RAW without its XMP
                if made:
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                with self.lock:
                    self.items.pop(pid, None)
                self._copying(b)
                self.save()
                if jid is not None and jid in self.cancel_jobs:
                    log.info("job %s stopped while copying %s", jid, info["filename"])
                    return "stopped"
                self._mark(b, "skipped", pid)
                continue
            except Exception as e:
                it.update(status="error", error=str(e))
                self._mark(b, "failed", pid)
            finally:
                self.step(it)
                self._copying(b)
            self.save()
        if jid is not None and jid in self.cancel_jobs:
            return "stopped"
        if want_open:
            self.open_paths(opened)
        return None

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
        running = darktable_running() or (time.sleep(0.5) is None and darktable_running())
        if running and bridge_alive():
            self.bridge.add("open", paths[0])
            for p in paths[1:]:
                self.bridge.add("import", p)
            self.bridge.flush()
            self.message = ("Sent to darktable. It opens as soon as you move the mouse over darktable's window "
                            "(darktable only takes outside requests while you use it).")
        elif running and not IS_WIN:
            # Linux: a second 'darktable <file>' hands the file to the running instance over D-Bus
            launch_darktable(self.cfg, paths[0], guard=False)
            for p in paths[1:]:
                self.bridge.add("import", p)
        elif running:
            self.message = ("darktable is open but its LightPhotoLibrary script hasn't answered in the last few seconds. "
                            "The photo opens as soon as it does (moving the mouse over darktable's thumbnails also wakes it). "
                            "If it never does: in darktable, lighttable > scripts > lightphotolibrary > stop and start lpl_bridge.")
            for p in paths:
                self.bridge.add("import", p)
            self.bridge.add("open", paths[0])
        else:
            if not launch_darktable(self.cfg, paths[0]):
                self.bridge.add("open", paths[0])  # darktable is starting: the bridge picks it up once it's ready
            for p in paths[1:]:
                self.bridge.add("import", p)
        with self.lock:  # the first one is opened in the darkroom; the others are imported and wait to be opened
            for it in self.items.values():
                if it["status"] in ("opening", "ready") and it["local_path"] == str(paths[0]):
                    it["status"] = "editing"
                elif it["status"] == "opening":
                    it["status"] = "ready"
        self.save()

    def sync(self, pid: str, force=False, preview: Path = None):
        it = self.items.get(pid)
        if not it:
            return
        xmp = Path(it["local_path"] + ".xmp")
        if preview is not None and preview.is_file():
            self.step(it, "sending preview")
            try:
                self.srv.upload("/api/photos/%s/preview" % pid, preview.read_bytes(), "image/jpeg")
            except Exception as e:
                log.warning("preview upload failed: %s", e)
            finally:
                self.step(it)
        if not xmp.is_file():
            return
        mtime = xmp.stat().st_mtime
        if not force and abs(mtime - it.get("synced_mtime", 0)) < 0.01:
            return
        it["status"] = "syncing"
        self.step(it, "sending edit")
        try:
            # agent=: LPL records which device sent the edit ("last edited on Desktop")
            body, headers = self.srv.upload("/api/photos/%s/xmp?checkout_ts=%s&agent=%s" % (
                pid, it["checkout_ts"], urllib.parse.quote(self.cfg["id"], safe="")), xmp.read_bytes(), "application/rdf+xml")
            merged = (headers.get("X-LPL-Merged") or headers.get("x-lpl-merged")) == "1"
            if merged:
                # LPL had newer rating/labels/tags: put them into the local file and tell darktable to re-read it
                xmp.write_bytes(body)
                self.bridge.add("reload", Path(it["local_path"]))
            it.update(status="synced", synced_mtime=xmp.stat().st_mtime, synced_ts=time.time(), error="")
            log.info("synced %s%s", it["filename"], " (merged)" if merged else "")
        except Exception as e:
            it.update(status="error", error="sync failed: %s" % e)
        finally:
            self.step(it)
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
            try:
                Path(target).resolve().relative_to(WORK.resolve())
            except ValueError:
                continue  # belongs to the local library, which handles it itself
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
        self.recover()
        threading.Thread(target=self.worker, daemon=True).start()
        if not once:
            start_local_library(self.cfg)
        log.info("agent %s (%s) talking to %s", self.cfg["name"], self.cfg["id"], self.srv.base)
        backoff = self.cfg.get("poll_seconds", 2)
        while not self.quit.is_set():
            try:
                r = self.srv.json("POST", "/api/agents/heartbeat", self.heartbeat_body(), timeout=15)
                self.take(r)
                with self.lock:  # an edit job that ended while the server could not be reached: say so now
                    unsaid = [(jid, b["state"]) for jid, b in self.batches.items() if b["unsaid"]]
                for jid, st in unsaid:
                    self.report_done(jid, st)
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
                    self.srv.json("POST", "/api/agents/heartbeat", self.heartbeat_body(), timeout=15)
                except Exception:
                    pass
                return
            # while photos are being copied: more often, so progress is fresh and a Stop arrives within a second
            self.quit.wait(min(backoff, 1.0) if self.batches else backoff)


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
    v = input("Also run a local library on this PC, for photos you keep or edit here (works without the NAS)? [%s]: "
              % ("Y/n" if cfg.get("local_library", True) else "y/N")).strip().lower()
    if v:
        cfg["local_library"] = v.startswith("y")
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
    if cfg.get("local_library", True):
        print("\nLocal library: open http://localhost:%s on this PC once the agent runs." % cfg.get("local_port", 8090))
    print("\nSaved to %s" % config_path())


def selftest(cfg: dict) -> int:
    """Used by the build: can the bundled local library start, read metadata and answer?"""
    out = data_dir() / "selftest.txt"
    lines = []
    try:
        cfg = dict(cfg, local_library=True, local_port=18090, server="http://127.0.0.1:9")
        start_local_library(cfg)
        if LOCAL["error"]:
            raise RuntimeError(LOCAL["error"])
        for _ in range(60):
            try:
                st = json.loads(urllib.request.urlopen("http://127.0.0.1:18090/api/status", timeout=2).read())
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("local library did not answer")
        lines.append("status: mode=%s version=%s" % (st.get("mode"), st.get("version")))
        html = urllib.request.urlopen("http://127.0.0.1:18090/", timeout=5).read()
        lines.append("web app: %s" % ("ok" if b"LightPhotoLibrary" in html else "MISSING"))
        exe = os.environ.get("EXIFTOOL", "exiftool")
        ver = subprocess.run([exe, "-ver"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
        lines.append("exiftool: %s (%s)" % (ver or "MISSING", exe))
        try:
            from PIL import Image
            from lpl import media
            sample = data_dir() / "selftest.jpg"
            Image.new("RGB", (64, 48), (200, 120, 40)).save(sample, quality=80)
            md = media.read_metadata([sample]).get(str(sample)) or next(iter(media.read_metadata([sample]).values()), {})
            lines.append("metadata: %s" % ("ok" if md else "EMPTY"))
            ok_md = bool(md)
        except Exception as e:
            lines.append("metadata: FAILED %s" % e)
            ok_md = False
        try:
            import rawpy  # noqa
            lines.append("rawpy: ok")
        except Exception as e:
            lines.append("rawpy: %s" % e)
        ok = bool(ver) and b"LightPhotoLibrary" in html and ok_md
    except Exception as e:
        lines.append("FAILED: %s" % e)
        ok = False
    lines.append("RESULT: %s" % ("OK" if ok else "FAILED"))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="LightPhotoLibrary PC agent")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--install-bridge", action="store_true")
    ap.add_argument("--once", action="store_true", help="one heartbeat + jobs, then exit (testing)")
    ap.add_argument("--server")
    ap.add_argument("--selftest", action="store_true", help="start the local library briefly and check it answers (build check)")
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
    if a.selftest:
        sys.exit(selftest(cfg))
    if a.status:
        print(json.dumps({"config": {k: v for k, v in cfg.items() if k != "password"}, "config_file": str(config_path()),
                          "data": str(data_dir()), "darktable": cfg.get("darktable") or find_darktable(),
                          "state": Agent(cfg).state()}, indent=2))
        return
    Agent(cfg).run(once=a.once)


if __name__ == "__main__":
    main()
