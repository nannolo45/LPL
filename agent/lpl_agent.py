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
import hashlib
import json
import logging
import os
import platform
import re
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
from queue import Empty, Queue

VERSION = "0.2.0"
# What this agent can do beyond the first protocol, announced in every heartbeat (docs/devices-stop-estimate.md):
# batch = reports how far each edit job is; cancel = stops a job or leaves out photos when the heartbeat answer
# says so; open = honours "open": false in an edit job (copy the photos, do not open darktable);
# versions = darktable duplicates (IMG_01_02.RAF.xmp) are copied beside the RAW and sent back with version=n (R17);
# shared = darktable styles, presets and module layouts are shared through LPL (R11; left out when the agent's
# darktable/module-names.json is missing).
# luts = darktable's LUT folder (lut 3D files) is carried through LPL (R19, docs/luts-r19.md).
CAPS = ["batch", "cancel", "open", "versions", "shared", "luts"]
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
AGENT_STARTED = time.time()  # told to LPL: a render job taken before this was lost with the previous run
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


_MACHINE = None


def machine_id() -> str:
    """A hash of this computer's own id (/etc/machine-id, Windows' MachineGuid, the Mac's platform UUID), sent
    with every heartbeat so LPL can tell that an agent installed again under a new id is the same computer.
    Empty when there is none to be had. The raw id never leaves the computer."""
    global _MACHINE
    if _MACHINE is not None:
        return _MACHINE
    raw = ""
    try:
        if IS_WIN:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
                raw = str(winreg.QueryValueEx(k, "MachineGuid")[0])
        elif sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True).stdout
            m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
            raw = m.group(1) if m else ""
        else:
            for f in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                if Path(f).is_file():
                    raw = Path(f).read_text(encoding="utf-8").strip()
                    if raw:
                        break
    except Exception:
        raw = ""
    _MACHINE = hashlib.sha256(("lpl:" + raw).encode()).hexdigest()[:24] if raw else ""
    return _MACHINE


def copy_failed(it: dict) -> bool:
    """A working-set entry whose copy from the NAS never completed (no XMP here yet): nothing of the user's in it."""
    return it.get("status") == "error" and (bool(it.get("copy_failed")) or not it.get("synced_mtime"))


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

    def upload_version(self, path, data: bytes):
        """POST an XMP of a version. Returns (body, headers, None), or (None, None, next) when LPL answers 409
        version_exists (another device made that number): next is the free number it names."""
        try:
            body, headers = self.upload(path, data, "application/rdf+xml")
            return body, headers, None
        except urllib.error.HTTPError as e:
            if e.code == 409:
                try:
                    nxt = int(json.loads(e.read().decode() or "{}").get("next"))
                except (ValueError, TypeError):
                    nxt = 0
                if nxt > 0:
                    return None, None, nxt
            raise


# ------------------------------------------------------------------ versions (R17)
_VERSION_NAME = re.compile(r"^(.+)_(\d{2,})\.([A-Za-z0-9]+)\.xmp$")


def version_file(local: Path, n: int) -> Path:
    """Where version n's sidecar lies beside the local copy of the photo: IMG_01.RAF -> IMG_01_02.RAF.xmp (two digits,
    three from 100). darktable stores no number inside the XMP (darktable:xmp_version is the file format's), so the
    name is the only place the number lives."""
    stem, dot, ext = local.name.rpartition(".")
    return local.with_name("%s_%02d%s%s.xmp" % (stem, n, dot, ext))


def local_versions(local: Path) -> dict:
    """{n: path} of the version sidecars (n >= 1) beside the local copy."""
    stem, dot, ext = local.name.rpartition(".")
    out = {}
    for f in local.parent.glob(glob_escape(stem) + "_*" + dot + glob_escape(ext) + ".xmp"):
        m = _VERSION_NAME.match(f.name)
        if m and "%s.%s" % (m.group(1), m.group(3)) == local.name:
            out[int(m.group(2))] = f
    return out


def glob_escape(t: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", t)


# ------------------------------------------------------------------ darktable
def darktable_cmd(cfg: dict) -> list:
    dtp = cfg.get("darktable") or find_darktable()
    if not dtp:
        raise RuntimeError("darktable not found. Set its path with --setup.")
    if dtp == "flatpak":
        return ["flatpak", "run", "org.darktable.Darktable"]
    return [dtp]


def is_appimage(p) -> bool:
    """A darktable AppImage (by name, or by the AppImage type 2 magic bytes "AI\\x02" at offset 8)."""
    try:
        rp = Path(p).resolve()
        if rp.name.lower().endswith(".appimage"):
            return True
        with open(rp, "rb") as f:
            return f.read(11)[8:11] == b"AI\x02"
    except OSError:
        return False


def darktable_cli_cmd(cfg: dict):
    """darktable-cli beside the configured darktable (same folder, the flatpak's, or inside the AppImage), or on
    PATH; None if there is none. An AppImage runs the binary named like the link it is called through (darktable's
    manual, "invocation per OS"), so a link named darktable-cli to the AppImage is made in the agent's folder."""
    dtp = cfg.get("darktable") or find_darktable()
    if dtp == "flatpak":
        return ["flatpak", "run", "--command=darktable-cli", "org.darktable.Darktable"]
    if dtp and not IS_WIN and is_appimage(dtp):
        link = data_dir() / "bin" / "darktable-cli"
        try:
            target = Path(dtp).resolve()
            if not (link.is_symlink() and Path(os.readlink(link)) == target):
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(target)
            return [str(link)]
        except OSError as e:
            log.warning("darktable-cli link to the AppImage not made: %s", e)
    if dtp:
        sib = Path(dtp).with_name("darktable-cli.exe" if IS_WIN else "darktable-cli")
        if sib.is_file():
            return [str(sib)]
    for n in ("darktable-cli", "darktable.darktable-cli"):  # the second: the snap
        w = shutil.which(n)
        if w:
            return [w]
    return None


def render_unavailable(cfg: dict):
    """Why this PC cannot render previews (shown in LPL), or None when it can."""
    if darktable_cli_cmd(cfg):
        return None
    dtp = cfg.get("darktable") or find_darktable()
    return ("darktable-cli not found next to %s or on PATH" % dtp) if dtp else "darktable not found (set its path with --setup)"


RENDER_SIZE = 1920


def cli_out(p: Path) -> str:
    """An output path for darktable-cli. darktable expands variables in it and takes a backslash as an escape, so
    C:\\Users\\x.jpg ends up somewhere else (Desktop, 26 Nov: every preview "missing"); Windows takes forward slashes."""
    return p.as_posix() if IS_WIN else str(p)


MISSING_MODULE = re.compile(r"the module [`'\"]?([\w.-]+)[`'\"]? requested by image id=\d+ [`'\"]?([^`'\"]+)[`'\"]? is not installed"
                            r"|cannot get iop-order for ([\w.-]+) instance")


def missing_modules(text: str):
    """darktable modules an edit uses that this darktable lacks, from darktable-cli's output: {module: file or None}.
    Such a photo is not rendered here (darktable may crash on it, or leave the module out and show a wrong look)."""
    out = {}
    for m in MISSING_MODULE.finditer(text or ""):
        if m.group(1):
            out[m.group(1)] = m.group(2)
        elif m.group(3):
            out.setdefault(m.group(3), None)
    return out


class MissingModule(RuntimeError):
    pass


def darktable_version(cfg: dict):
    """darktable-cli --version, first line (cached per command)."""
    cmd = darktable_cli_cmd(cfg)
    if not cmd:
        return None
    key = tuple(cmd)
    if key not in _DT_VERSION:
        try:
            r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=60,
                               **({"creationflags": NO_WINDOW} if IS_WIN else {}))
            line = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            _DT_VERSION[key] = next((l.strip() for l in line if "darktable" in l.lower()), line[0].strip() if line else None)
        except Exception:
            _DT_VERSION[key] = None
    return _DT_VERSION[key]


_DT_VERSION = {}


def render_preview(cfg: dict, raw: Path, xmp: Path, out: Path, timeout=300) -> None:
    """darktable's rendering of raw with the edit in xmp, as a JPEG of at most 1920 px: what LPL shows as the preview.
    darktable-cli runs with a copy of darktable's settings (darktablerc: LUT folder, OpenCL; data.db: styles and
    presets) in a folder of its own, so it never waits for, or locks, the darktable that may be open."""
    cmd = darktable_cli_cmd(cfg)
    if not cmd:
        raise RuntimeError("darktable-cli not found")
    tmp = data_dir() / "render-conf" / uuid.uuid4().hex[:8]   # under the agent's folder: a flatpak darktable sees it
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        src = darktable_config_dir(cfg)
        for n in ("darktablerc", "data.db"):
            if (src / n).is_file():
                shutil.copy2(src / n, tmp / n)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        # --hq false: processed at about the output size instead of full size and scaled down (a preview, about
        # twice as fast; measured with darktable 4.6)
        args = cmd + [str(raw), str(xmp), cli_out(out), "--width", str(RENDER_SIZE), "--height", str(RENDER_SIZE),
                      "--hq", "false", "--core", "--configdir", str(tmp), "--library", ":memory:"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           **({"creationflags": NO_WINDOW} if IS_WIN else {}))
        said = (r.stderr or "") + (r.stdout or "")
        mm = missing_modules(said)
        if mm:
            try:
                out.unlink()
            except OSError:
                pass
            raise MissingModule("the edit uses %s, which darktable on this PC does not have (%s): render it on a PC "
                                "whose darktable has it, or update darktable here"
                                % (", ".join(sorted(mm)), darktable_version(cfg) or "version unknown"))
        if not out.is_file() or out.stat().st_size == 0:
            tail = said.strip().splitlines()[-3:]
            raise RuntimeError("darktable-cli made no image (exit %s): %s" % (r.returncode, " / ".join(tail)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render_batch(cfg: dict, files: list, outdir: Path, timeout_each=120) -> dict:
    """Several photos in one darktable-cli run (it starts, reads its settings and sets up OpenCL once): each file's
    edit is the sidecar beside it (<file>.xmp); outputs are outdir/<file name without extension>.jpg, so the names
    must differ. Returns file -> its JPEG for those that came out; the others the caller renders one by one."""
    cmd = darktable_cli_cmd(cfg)
    if not cmd:
        raise RuntimeError("darktable-cli not found")
    tmp = data_dir() / "render-conf" / uuid.uuid4().hex[:8]
    tmp.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        src = darktable_config_dir(cfg)
        for n in ("darktablerc", "data.db"):
            if (src / n).is_file():
                shutil.copy2(src / n, tmp / n)
        args = list(cmd)
        for f in files:
            args += ["--import", str(f)]
        args += [cli_out(outdir / "$(FILE_NAME)"), "--out-ext", "jpg", "--width", str(RENDER_SIZE), "--height", str(RENDER_SIZE),
                 "--hq", "false", "--core", "--configdir", str(tmp), "--library", ":memory:"]
        r = None
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=60 + timeout_each * len(files),
                               **({"creationflags": NO_WINDOW} if IS_WIN else {}))
        except subprocess.TimeoutExpired:
            log.warning("darktable-cli took too long for %d photos", len(files))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    said = ((r.stderr or "") + (r.stdout or "")) if r is not None else ""
    lacking = missing_modules(said)
    bad_stems = {Path(v).stem for v in lacking.values() if v}
    if lacking and not bad_stems:
        bad_stems = {Path(f).stem for f in files}  # cannot tell which photo: each one again on its own
    got = {}
    for f in files:
        o = outdir / (Path(f).stem + ".jpg")
        if Path(f).stem in bad_stems:
            try:
                o.unlink()
            except OSError:
                pass
            continue  # rendered alone next, where its missing module is said
        if o.is_file() and o.stat().st_size > 0:
            got[f] = o
    if len(got) < len(files) and r is not None:
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-6:]
        log.warning("darktable-cli (exit %s) made %d of %d: %s", r.returncode, len(got), len(files), " / ".join(tail))
    return got


class Renderer:
    """Renders previews one at a time in its own thread (darktable-cli takes seconds per photo) and posts them to
    LPL: after a sync that came with no rendered preview from the bridge (auto sync, "Save edited to NAS", darktable
    closed while in darkroom)."""

    def __init__(self, agent):
        self.agent = agent
        self.q = Queue()
        self.waiting = set()
        self.thread = None

    def available(self) -> bool:
        return bool(darktable_cli_cmd(self.agent.cfg))

    def request(self, pid: str):
        if pid in self.waiting or not self.available():
            return
        self.waiting.add(pid)
        self.q.put(pid)
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, name="render", daemon=True)
            self.thread.start()

    def _run(self):
        while True:
            try:
                pid = self.q.get(timeout=30)
            except Empty:
                return
            self.waiting.discard(pid)
            it = self.agent.items.get(pid)
            if not it:
                continue
            raw, xmp = Path(it["local_path"]), Path(it["local_path"] + ".xmp")
            if not raw.is_file() or not xmp.is_file():
                continue
            mt = xmp.stat().st_mtime
            if it.get("preview_mtime") == mt:
                continue  # the bridge's preview of this very edit got there first
            out = data_dir() / "render" / ("%s.jpg" % pid)
            try:
                self.agent.step(it, "rendering preview")
                render_preview(self.agent.cfg, raw, xmp, out)
                self.agent.srv.upload("/api/photos/%s/preview" % pid, out.read_bytes(), "image/jpeg")
                it["preview_mtime"] = mt
                self.agent.save()
                log.info("preview rendered for %s", it.get("filename") or pid)
            except Exception as e:
                log.warning("preview of %s not rendered: %s", it.get("filename") or pid, e)
            finally:
                self.agent.step(it)
                try:
                    out.unlink()
                except OSError:
                    pass


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


def darktable_running_strict() -> bool:
    """darktable_running() for writing data.db: a check that fails counts as "running"."""
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq darktable.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20)
            return out.returncode != 0 or "darktable.exe" in out.stdout.lower()
        out = subprocess.run(["pgrep", "-x", "darktable"], capture_output=True, timeout=20)
        return out.returncode != 1  # 0 = found, 1 = none, anything else = the check failed
    except Exception:
        return True


def pid_alive(pid: int) -> bool:
    """Is there a process with this id? When it cannot be told: yes."""
    if pid <= 0:
        return False
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20)
            return out.returncode != 0 or ('"%d"' % pid) in out.stdout
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def data_db_locked(path: Path) -> bool:
    """darktable's own lock file beside data.db (data.db.lock, holding its pid) names a live process. A lock whose pid
    is gone is stale (darktable crashed): not locked. A lock that cannot be read counts as locked."""
    lock = Path(str(path) + ".lock")
    if not lock.exists():
        return False
    try:
        txt = lock.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return True
    m = re.match(r"\d+", txt)
    return pid_alive(int(m.group(0))) if m else True


def data_db_writable(path: Path) -> bool:
    """data.db may be written only when darktable is surely closed."""
    return not darktable_running_strict() and not data_db_locked(path)


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
        poke_darktable()


def poke_darktable():
    """Linux: ask the running darktable over D-Bus to carry out what the bridge was just sent. Without this darktable
    only acts on its next own event (the mouse moving over its window). Best effort: no gdbus, no D-Bus service or an
    older bridge script just means it waits for that event, as before."""
    if IS_WIN or not shutil.which("gdbus"):
        return

    def run():
        try:
            subprocess.run(["gdbus", "call", "--session", "--dest", "org.darktable.service", "--object-path", "/darktable",
                            "--method", "org.darktable.service.Remote.Lua", "if lpl_bridge_poke then lpl_bridge_poke() end"],
                           capture_output=True, timeout=10)
        except Exception as e:
            log.debug("could not wake darktable over D-Bus: %s", e)

    threading.Thread(target=run, daemon=True).start()


def bundled_bridge():
    """The lpl_bridge.lua that came with this agent (beside the script, or beside the .exe)."""
    for src in (Path(__file__).resolve().parent / "darktable" / "lpl_bridge.lua",
                Path(sys.executable).resolve().parent / "darktable" / "lpl_bridge.lua"):
        if src.is_file():
            return src
    return None


def refresh_bridge(cfg: dict):
    """At start: when darktable already has the bridge installed and it differs from the one this agent brings, put
    the new one in (darktable loads it at its next start). Nothing is installed where there was none."""
    src = bundled_bridge()
    if not src:
        return None
    try:
        dest = darktable_config_dir(cfg) / "lua" / "lightphotolibrary" / "lpl_bridge.lua"
        if dest.is_file() and dest.read_bytes() != src.read_bytes():
            shutil.copy2(src, dest)
            log.info("darktable bridge updated in %s (restart darktable to load it)", dest.parent)
            return dest
    except OSError as e:
        log.warning("darktable bridge not updated: %s", e)
    return None


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


# ------------------------------------------------------------------ shared styles, presets and module layouts (R11)
# Contract: darktable-android docs/PLAN.md 4.5, copied in docs/shared-r11.md. The agent reads darktable's data.db only
# from a read-only snapshot (sqlite backup into memory) and writes it only for a shared-update job, and only while
# darktable is closed (darktable keeps data.db with journal_mode MEMORY, synchronous OFF).
SHARED_EVERY = 600  # seconds between snapshots while darktable runs (and between checks with the server otherwise)
LAYOUT_OP = "modulegroups"
_PRESET_CORE = ["name", "description", "operation", "op_version", "op_params", "enabled", "blendop_params",
                "blendop_version", "multi_priority", "multi_name", "multi_name_hand_edited", "model", "maker", "lens",
                "iso_min", "iso_max", "exposure_min", "exposure_max", "aperture_min", "aperture_max",
                "focal_length_min", "focal_length_max", "autoapply", "filter", "def", "format"]
_LAYOUT_CORE = ["name", "operation", "op_version", "op_params"]
_STYLE_CORE = ["name", "description", "iop_list"]
_ITEM_CORE = ["num", "module", "operation", "op_params", "enabled", "blendop_params", "blendop_version",
              "multi_priority", "multi_name", "multi_name_hand_edited"]


def module_operations() -> set:
    """darktable's processing modules (operation names), from module-names.json shipped in the agent's darktable folder
    (a copy of darktable-android schema/module-names.json). Empty when the file is missing: then nothing is shared."""
    for d in (Path(__file__).resolve().parent / "darktable", Path(sys.executable).resolve().parent / "darktable",
              _bundle_dir() / "darktable"):
        f = d / "module-names.json"
        if f.is_file():
            try:
                return {k for k in json.loads(f.read_text(encoding="utf-8")) if not k.startswith("_")}
            except (OSError, ValueError):
                pass
    return set()


def excluded_names() -> tuple:
    """(style_prefix, layout_names) from R11-excluded-names.json in the agent's darktable folder (PLAN.md 4.5, not
    shared): styles starting with the prefix and layouts with one of the names are skipped. Defaults when missing."""
    for d in (Path(__file__).resolve().parent / "darktable", Path(sys.executable).resolve().parent / "darktable",
              _bundle_dir() / "darktable"):
        f = d / "R11-excluded-names.json"
        if f.is_file():
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                return str(j.get("style_prefix") or "_l10n_"), {str(n) for n in j.get("layout_names") or []}
            except (OSError, ValueError):
                pass
    return "_l10n_", set()


def shared_canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def shared_core(body: dict) -> dict:
    k = body["kind"]
    if k == "style":
        items = sorted(body.get("items") or [], key=lambda i: (i.get("num") is None, i.get("num") or 0))
        return {"kind": k, "style": {c: (body.get("style") or {}).get(c) for c in _STYLE_CORE},
                "items": [{c: it.get(c) for c in _ITEM_CORE} for it in items]}
    cols = _LAYOUT_CORE if k == "layout" else _PRESET_CORE
    return {"kind": k, "row": {c: (body.get("row") or {}).get(c) for c in cols}}


def shared_hash(body: dict) -> str:
    return hashlib.sha256(shared_canonical(shared_core(body))).hexdigest()


def _to_json(v):
    """A data.db value as it travels: INTEGER a number, REAL a %.17g string, BLOB lowercase hex, TEXT as is, NULL null."""
    if isinstance(v, float):
        return format(v, ".17g")
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


def _from_json(v, decl: str):
    """The other way, by the column's declared type in this data.db."""
    if v is None:
        return None
    t = (decl or "").upper()
    try:
        if "BLOB" in t:
            return bytes.fromhex(v) if isinstance(v, str) else v
        if "INT" in t:
            return int(v)
        if "REAL" in t or "FLOA" in t or "DOUB" in t:
            return float(v)
    except (TypeError, ValueError):
        return v
    return v


def shared_key(kind, operation, op_version, name) -> str:
    return json.dumps([kind, operation or "", int(op_version or 0), name], ensure_ascii=False)


def darktable_data_db(cfg: dict) -> Path:
    return Path(cfg["darktable_datadb"]) if cfg.get("darktable_datadb") else darktable_config_dir(cfg) / "data.db"


def read_shared(db, ops: set) -> dict:
    """Every shared item of an open data.db connection: key -> body. Presets of processing modules and module layouts
    with writeprotect = 0, and every style that is not one of darktable's own, minus the names of R11-excluded-names.json."""
    out = {}
    style_prefix, layout_names = excluded_names()
    db.row_factory = None
    pcols = [r[1] for r in db.execute("PRAGMA table_info(presets)")]
    if pcols:
        for row in db.execute("SELECT * FROM presets"):
            d = dict(zip(pcols, row))
            if d.get("writeprotect") or not d.get("name"):
                continue
            op = d.get("operation") or ""
            kind = "layout" if op == LAYOUT_OP else ("preset" if op in ops else None)
            if not kind or (kind == "layout" and d["name"] in layout_names):
                continue
            body = {"kind": kind, "row": {k: _to_json(v) for k, v in d.items() if k != "writeprotect"}}
            out[shared_key(kind, op, d.get("op_version"), d["name"])] = body
    scols = [r[1] for r in db.execute("PRAGMA table_info(styles)")]
    icols = [r[1] for r in db.execute("PRAGMA table_info(style_items)")]
    if scols and icols:
        for row in db.execute("SELECT * FROM styles ORDER BY id"):
            s = dict(zip(scols, row))
            name = s.get("name")
            if not name or name.startswith(style_prefix):
                continue
            items = [{k: _to_json(v) for k, v in zip(icols, r) if k != "styleid"}
                     for r in db.execute("SELECT * FROM style_items WHERE styleid=? ORDER BY num", (s["id"],))]
            out[shared_key("style", "", 0, name)] = {"kind": "style", "style": {c: _to_json(s.get(c)) for c in _STYLE_CORE},
                                                     "items": items}
    return out


def snapshot_shared(path: Path, ops: set):
    """A read-only snapshot of data.db (sqlite backup into memory, mode=ro): key -> body. None when it fails."""
    import sqlite3
    if not path.is_file():
        return None
    try:
        src = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        mem = sqlite3.connect(":memory:")
        try:
            src.backup(mem)
        finally:
            src.close()
        try:
            return read_shared(mem, ops)
        finally:
            mem.close()
    except Exception as e:
        log.warning("data.db snapshot skipped: %s", e)
        return None


def apply_shared(path: Path, writes: list, deletes: list) -> None:
    """Write items into data.db in one transaction, by column name. writes: bodies; deletes: (kind, operation, op_version,
    name). Preset / layout: insert or replace on name + operation + op_version with writeprotect = 0. Style: the style of
    that name and its items are removed and the new ones inserted. Never called while darktable runs."""
    import sqlite3
    db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    try:
        pdecl = {r[1]: r[2] for r in db.execute("PRAGMA table_info(presets)")}
        sdecl = {r[1]: r[2] for r in db.execute("PRAGMA table_info(styles)")}
        idecl = {r[1]: r[2] for r in db.execute("PRAGMA table_info(style_items)")}
        db.execute("BEGIN IMMEDIATE")
        try:
            def drop_style(name):
                for (sid,) in db.execute("SELECT id FROM styles WHERE name=?", (name,)).fetchall():
                    db.execute("DELETE FROM style_items WHERE styleid=?", (sid,))
                    db.execute("DELETE FROM styles WHERE id=?", (sid,))
            for kind, op, ver, name in deletes:
                if kind == "style":
                    drop_style(name)
                else:
                    db.execute("DELETE FROM presets WHERE name=? AND operation=? AND op_version=? AND COALESCE(writeprotect,0)=0",
                               (name, op, ver))
            for b in writes:
                if b["kind"] == "style":
                    drop_style(b["style"]["name"])
                    cols = [c for c in sdecl if c != "id"]
                    st = b.get("style") or {}
                    cur = db.execute("INSERT INTO styles(%s) VALUES(%s)" % (",".join('"%s"' % c for c in cols), ",".join("?" * len(cols))),
                                     [_from_json(st.get(c), sdecl[c]) for c in cols])
                    sid = cur.lastrowid
                    cols = [c for c in idecl if c != "styleid"]
                    for it in sorted(b.get("items") or [], key=lambda i: i.get("num") or 0):
                        db.execute("INSERT INTO style_items(styleid,%s) VALUES(?,%s)" % (",".join('"%s"' % c for c in cols), ",".join("?" * len(cols))),
                                   [sid] + [_from_json(it.get(c), idecl[c]) for c in cols])
                else:
                    row = b.get("row") or {}
                    cols = list(pdecl)
                    vals = [0 if c == "writeprotect" else _from_json(row.get(c), pdecl[c]) for c in cols]
                    db.execute("DELETE FROM presets WHERE name=? AND operation=? AND op_version=?",
                               (row.get("name"), row.get("operation"), row.get("op_version")))
                    db.execute("INSERT OR REPLACE INTO presets(%s) VALUES(%s)" % (",".join('"%s"' % c for c in cols), ",".join("?" * len(cols))), vals)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()


class SharedSync:
    """The agent's part of R11: per-item state file, uploads after each snapshot, the held shared-update job."""

    def __init__(self, agent):
        self.agent = agent
        self.ops = module_operations()
        self.enabled = bool(self.ops)
        self.data = {"items": {}, "job": None}
        self.update = None            # heartbeat state.shared_update: waiting_for_darktable / applying / None
        self.last_pass = 0.0
        self.was_running = None
        self.lock = threading.RLock()
        self.state_file = STATE_FILE.with_name("shared.json")  # per item: id, rev, local_hash, state; and a held job
        f = self.file()
        if f.is_file():
            try:
                self.data = json.loads(f.read_text(encoding="utf-8"))
                self.data.setdefault("items", {})
                self.data.setdefault("job", None)
            except Exception:
                pass

    def file(self) -> Path:
        return self.state_file

    def save(self):
        with self.lock:
            f = self.file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, f)

    def active(self) -> bool:
        return self.enabled and "shared" in (self.agent.server_caps or [])

    def path(self) -> Path:
        return darktable_data_db(self.agent.cfg)

    def hold(self, job):
        """A shared-update job: kept (also over a restart) until darktable is closed, then applied by tick()."""
        with self.lock:
            if self.data.get("job") and self.data["job"].get("id") == job.get("id"):
                return  # handed out again while this agent still holds it
            self.data["job"] = {"id": job["id"], "payload": job.get("payload") or {}}
            self.save()
        self.agent.wake.set()

    def tick(self, force=False):
        """From the main loop: apply a held job when darktable is closed; snapshot and upload at start, when darktable has
        closed, and every SHARED_EVERY seconds."""
        if not self.active():
            return
        running = darktable_running()
        job = self.data.get("job")
        if job:
            if not data_db_writable(self.path()):
                self.update = "waiting_for_darktable"
            else:
                self.apply_job(job)
        else:
            self.update = None
        closed = self.was_running is True and not running
        self.was_running = running
        if force or closed or time.time() - self.last_pass >= SHARED_EVERY:
            self.sync_pass()

    # --- uploads
    def sync_pass(self, snap=None):
        self.last_pass = time.time()
        snap = snapshot_shared(self.path(), self.ops) if snap is None else snap
        if snap is None:
            return
        srv = self.agent.srv
        aid = urllib.parse.quote(self.agent.cfg["id"])
        listed = srv.json("GET", "/api/shared?agent=%s&since=0" % aid)
        by_key = {shared_key(i["kind"], i["operation"], i["op_version"], i["name"]): i for i in listed.get("items") or []}
        by_id = {i["id"]: i for i in listed.get("items") or []}
        open_conf = {k["item_id"] for k in listed.get("conflicts") or []}
        items = self.data["items"]
        present = {k: shared_hash(b) for k, b in snap.items()}
        with self.lock:
            for key, st in list(items.items()):
                if st.get("state") == "conflict" and st.get("id") not in open_conf:
                    self.resolved(key, st, by_id.get(st.get("id")), present.get(key))
            for key, body in snap.items():
                h = present[key]
                st = items.get(key)
                cur = by_key.get(key)
                if st is None:
                    if cur and not cur["deleted"] and cur["hash"] == h:  # first sync: the same item is already there
                        items[key] = {"id": cur["id"], "rev": cur["rev"], "local_hash": h, "state": "ok"}
                        continue
                    self.put(key, body, h, 0)
                    continue
                if st.get("state") == "conflict":
                    continue  # not uploaded again until it is resolved
                if h != st.get("local_hash"):
                    self.put(key, body, h, st.get("rev") or 0)
                elif cur and not cur["deleted"] and cur["hash"] == h and cur["rev"] > (st.get("rev") or 0):
                    st["rev"] = cur["rev"]  # e.g. "keep mine" chosen in LPL: the current revision is this copy
            for key, st in list(items.items()):
                if key in snap or st.get("local_hash") is None or st.get("state") == "conflict" or not st.get("id"):
                    continue
                cur = by_id.get(st["id"])
                if cur and cur["deleted"]:  # deleted here and elsewhere
                    st.update(rev=cur["rev"], local_hash=None, state="ok")
                    continue
                self.delete(key, st)
            self.save()
        self.send_state()

    def resolved(self, key, st, cur, h):
        """Its conflict is gone: "mine" (the current revision is this copy) or "theirs" (it waits for the next update)."""
        if cur and ((cur["deleted"] and h is None) or (not cur["deleted"] and cur["hash"] == h)):
            st.update(rev=cur["rev"], local_hash=h, state="ok")
        else:
            st.update(local_hash=h, state="ok")  # not a local change until the update overwrites it

    def put(self, key, body, h, base_rev):
        kind, op, ver, name = json.loads(key)
        q = {"kind": kind, "operation": op, "op_version": ver, "name": name, "hash": h, "changed_at": time.time(),
             "base_rev": base_rev, "body": body}
        items = self.data["items"]
        try:
            r = self.agent.srv.json("PUT", "/api/shared?agent=%s" % urllib.parse.quote(self.agent.cfg["id"]), q)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                try:
                    it = json.loads(e.read().decode() or "{}").get("item") or {}
                except ValueError:
                    it = {}
                st = items.setdefault(key, {"id": it.get("id"), "rev": base_rev, "local_hash": None, "state": "ok"})
                st.update(id=it.get("id", st.get("id")), state="conflict")
                return
            log.warning("shared upload of %s failed: %s", name, e)
            return
        st = items.setdefault(key, {})
        if r.get("decided") == "theirs":  # newest wins and the other copy is newer: it waits for the next update
            st.update(id=r["id"], rev=st.get("rev") or 0, local_hash=h, state="ok")
        else:
            st.update(id=r["id"], rev=r["rev"], local_hash=h, state="ok")

    def delete(self, key, st):
        try:
            r = self.agent.srv.json("DELETE", "/api/shared/%d?agent=%s&base_rev=%d&changed_at=%.3f" % (st["id"], urllib.parse.quote(self.agent.cfg["id"]), st.get("rev") or 0, time.time()))
        except urllib.error.HTTPError as e:
            if e.code == 409:
                st["state"] = "conflict"
            elif e.code == 404:
                self.data["items"].pop(key, None)
            else:
                log.warning("shared deletion failed: %s", e)
            return
        if r.get("decided") == "theirs":
            st.update(local_hash=None, state="ok")
        else:
            st.update(rev=r["rev"], local_hash=None, state="ok")

    def send_state(self):
        with self.lock:
            out = {str(st["id"]): {"rev": st.get("rev") or 0, "state": st["state"] if st.get("state") in ("ok", "kept", "unusable") else "ok"}
                   for st in self.data["items"].values() if st.get("id")}
        try:
            self.agent.srv.json("PUT", "/api/shared/state?agent=%s" % urllib.parse.quote(self.agent.cfg["id"]),
                                {"items": out, "policy": "ask"})
        except Exception as e:
            log.warning("shared state not sent: %s", e)

    # --- taking in
    def apply_job(self, job):
        p = job.get("payload") or {}
        srv = self.agent.srv
        self.update = "applying"
        status = "done"
        try:
            got = {}
            for iid in list(p.get("ids") or []) + list(p.get("delete") or []) + list(p.get("keep") or []):
                if iid not in got:
                    got[iid] = srv.json("GET", "/api/shared/%d" % int(iid))
            writes, deletes = [], []
            for iid in p.get("ids") or []:  # an item that is a deletion by now is removed, as for "delete"
                g = got[iid]
                if g["deleted"]:
                    deletes.append((g["kind"], g["operation"], g["op_version"], g["name"]))
                else:
                    writes.append(g["body"])
            for iid in p.get("delete") or []:
                g = got[iid]
                deletes.append((g["kind"], g["operation"], g["op_version"], g["name"]))
            if not data_db_writable(self.path()):  # started again meanwhile (or not surely closed): wait
                self.update = "waiting_for_darktable"
                return
            log.info("shared update: %d written, %d deleted, %d kept", len(writes), len(deletes), len(p.get("keep") or []))
            apply_shared(self.path(), writes, deletes)
            after = snapshot_shared(self.path(), self.ops) or {}
            with self.lock:
                items = self.data["items"]
                for iid, g in got.items():
                    key = shared_key(g["kind"], g["operation"], g["op_version"], g["name"])
                    st = items.setdefault(key, {"id": g["id"], "rev": 0, "local_hash": None, "state": "ok"})
                    st["id"] = g["id"]
                    if iid in (p.get("keep") or []):
                        st.update(rev=g["rev"], state="kept")  # kept here after a deletion elsewhere; not offered again
                    else:
                        st.update(rev=g["rev"], state="ok", local_hash=shared_hash(after[key]) if key in after else None)
                self.data["job"] = None
                self.save()
        except Exception as e:
            log.exception("shared update failed")
            self.agent.message = "Updating styles and presets failed: %s" % e
            status = "failed"
            with self.lock:
                self.data["job"] = None
                self.save()
        self.update = None
        self.send_state()
        self.agent.report_done(job["id"], status)


# ------------------------------------------------------------------ the LUT folder (R19, docs/luts-r19.md)
# Contract: darktable-android docs/PLAN.md 4.6, copied in docs/luts-r19.md. darktable's lut 3D module reads its files
# from the folder of the preference plugins/darkroom/lut3d/def_path and keeps the path relative to it in the edit.
# The agent sends the files of that folder to LPL and takes in files it does not have, only into empty places: it
# never overwrites or deletes a file in the user's LUT folder.
LUT_EVERY = 600  # seconds between passes (and one at start)
LUT_EXTS = (".cube", ".3dl", ".png", ".gmz")
LUT_MAX = 64 * 1024 * 1024
LUT_KEY = "plugins/darkroom/lut3d/def_path"
LUT_MAX_FILES = 3000      # more LUT files than this: the folder is not a LUT folder (a home folder, a photo library)
LUT_MAX_WALK = 100000     # entries looked at before giving up on a folder that big


def lut_folder(cfg: dict):
    """(folder, None) from darktablerc, or (None, reason) when darktable has no LUT folder the agent can use."""
    rc = darktable_config_dir(cfg) / "darktablerc"
    try:
        text = rc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, "darktable's settings (darktablerc) were not found"
    value = ""
    for line in text.splitlines():
        if line.startswith(LUT_KEY + "="):
            value = line[len(LUT_KEY) + 1:].strip()
    if not value:
        return None, "darktable has no LUT folder set (preferences > processing > LUT 3D root folder)"
    p = Path(os.path.expandvars(os.path.expanduser(value)))
    if not p.is_absolute():
        return None, "darktable's LUT folder is not an absolute path: %s" % value
    if not p.is_dir():
        return None, "darktable's LUT folder does not exist: %s" % value
    try:
        rp, home = p.resolve(), Path.home().resolve()
    except OSError:
        rp, home = p, Path.home()
    if rp == Path(rp.anchor) or rp == home or rp in home.parents:
        return None, ("darktable's LUT folder is %s, a whole home folder or drive: set a folder with only LUT files "
                      "(darktable: preferences > processing > LUT 3D root folder)" % value)
    return p, None


def lut_path_ok(rel: str) -> bool:
    """The contract's path rules: / separators, relative, no empty, . or .. step, no control characters, a LUT extension."""
    if not rel or len(rel.encode("utf-8")) > 500 or "\\" in rel or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in rel):
        return False
    if any(s in ("", ".", "..") for s in rel.split("/")):
        return False
    return rel.lower().endswith(LUT_EXTS)


class TooManyLuts(Exception):
    pass


def lut_files(folder: Path):
    """relative path -> file of every LUT file under folder (links and hidden folders and files not followed); and the
    paths left out, with why. TooManyLuts when the folder is clearly not a LUT folder (too many files)."""
    out, skipped = {}, {}
    seen = 0
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(root, d)))
        seen += len(dirs) + len(files)
        if seen > LUT_MAX_WALK:
            raise TooManyLuts("more than %d files and folders" % LUT_MAX_WALK)
        for n in sorted(files):
            full = Path(root) / n
            if n.startswith(".") or full.is_symlink() or not n.lower().endswith(LUT_EXTS):
                continue
            rel = full.relative_to(folder).as_posix()
            if not lut_path_ok(rel):
                skipped[rel] = "path not allowed"
                continue
            try:
                if full.stat().st_size > LUT_MAX:
                    skipped[rel] = "larger than 64 MB"
                    continue
            except OSError:
                continue
            out[rel] = full
            if len(out) > LUT_MAX_FILES:
                raise TooManyLuts("more than %d LUT-type files" % LUT_MAX_FILES)
    return out, skipped


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LutSync:
    """The agent's part of R19: per path the hash it last sent or took in (base), the paths the user removed here."""

    def __init__(self, agent):
        self.agent = agent
        self.data = {"base": {}, "removed": [], "refused": {}}
        self.hashes = {}               # path -> (size, mtime, sha256)
        self.last_pass = 0.0
        self.started = False
        self.status = None             # heartbeat state.luts
        self.lock = threading.RLock()
        self.running = None            # the thread of the pass under way
        self.state_file = STATE_FILE.with_name("luts.json")
        if self.state_file.is_file():
            try:
                self.data.update(json.loads(self.state_file.read_text(encoding="utf-8")))
            except Exception:
                pass

    def save(self):
        with self.lock:
            f = self.state_file
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, f)

    def active(self) -> bool:
        return "luts" in (self.agent.server_caps or [])

    def tick(self, force=False):
        """From the main loop: a pass at start and every LUT_EVERY seconds, in its own thread, so a long pass (many
        files to send) never holds up the heartbeat and the jobs."""
        if not self.active():
            return
        if self.running is not None and self.running.is_alive():
            return
        if force or not self.started or time.time() - self.last_pass >= LUT_EVERY:
            self.started = True
            self.last_pass = time.time()
            self.running = threading.Thread(target=self._pass, name="lut-pass", daemon=True)
            self.running.start()

    def _pass(self):
        try:
            self.sync_pass()
        except Exception as e:
            log.warning("LUT folder: %s", e)

    def hash_of(self, rel: str, full: Path) -> str:
        st = full.stat()
        key = (st.st_size, st.st_mtime_ns)
        cached = self.hashes.get(rel)
        if cached and cached[:2] == key:
            return cached[2]
        h = file_sha256(full)
        self.hashes[rel] = key + (h,)
        return h

    def put(self, rel: str, full: Path, h: str, base):
        """PUT the file; base None = no x-base-sha256 (the PC changed it: newest wins), "" = a first upload. 200 or 409."""
        srv = self.agent.srv
        headers = dict(srv.headers)
        headers["x-sha256"] = h
        headers["content-type"] = "application/octet-stream"
        if base is not None:
            headers["x-base-sha256"] = base
        q = "/api/luts/file?path=%s&agent=%s" % (urllib.parse.quote(rel, safe=""), urllib.parse.quote(self.agent.cfg["id"], safe=""))
        req = urllib.request.Request(srv.base + q, data=full.read_bytes(), method="PUT", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                r.read()
                return 200
        except urllib.error.HTTPError as e:
            if e.code == 409:
                return 409
            raise

    def take_in(self, folder: Path, rel: str, entry: dict) -> str:
        """Download into the empty place rel: "ok", "different" (the place was taken meanwhile) or "failed"."""
        dest = folder.joinpath(*rel.split("/"))
        try:
            dest.resolve().relative_to(folder.resolve())
        except ValueError:
            return "failed"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(".lpl-lut-%s.part" % uuid.uuid4().hex[:8])
        try:
            self.agent.srv.download("/api/luts/file?path=%s" % urllib.parse.quote(rel, safe=""), tmp)
            if file_sha256(tmp) != entry["sha256"]:
                return "failed"  # changed on the server meanwhile: taken at the next pass
            try:
                os.link(tmp, dest)  # never over an existing file
            except FileExistsError:
                return "different"
            except OSError:  # a file system without hard links: create the file exclusively and copy into it
                fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
                with os.fdopen(fd, "wb") as out, open(tmp, "rb") as src:
                    shutil.copyfileobj(src, out)
            return "ok"
        except FileExistsError:
            return "different"
        except Exception as e:
            log.warning("LUT file %s not taken: %s", rel, e)
            return "failed"
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def sync_pass(self):
        self.last_pass = time.time()
        srv = self.agent.srv
        aid = urllib.parse.quote(self.agent.cfg["id"], safe="")
        folder, reason = lut_folder(self.agent.cfg)
        if folder is None:
            self.status = {"folder": None, "reason": reason}
            srv.json("PUT", "/api/luts/state?agent=%s" % aid, {"folder": None, "reason": reason, "files": {}})
            return
        try:
            local, skipped = lut_files(folder)
        except TooManyLuts as e:
            reason = "darktable's LUT folder %s has %s: not sent; set a folder with only LUT files" % (folder, e)
            self.status = {"folder": None, "reason": reason}
            srv.json("PUT", "/api/luts/state?agent=%s" % aid, {"folder": None, "reason": reason, "files": {}})
            log.warning("LUT folder: %s", reason)
            return
        listed = srv.json("GET", "/api/luts?since=0")
        server = {e["path"]: e for e in listed.get("files") or [] if not e.get("deleted")}
        base, removed, refused = self.data["base"], set(self.data["removed"]), self.data["refused"]
        report, sent, taken = {}, 0, 0
        for rel in sorted(set(local) | set(server)):
            s = server.get(rel)
            b = base.get(rel)
            if rel in local:
                removed.discard(rel)
                try:
                    h = self.hash_of(rel, local[rel])
                except OSError:
                    continue
                if s and s["sha256"] == h:
                    base[rel] = h
                    refused.pop(rel, None)
                    report[rel] = {"sha256": h, "state": "ok"}
                    continue
                if b is None or h != b:  # a local change (or a file never sent)
                    if b is None and refused.get(rel) == [h, s["sha256"] if s else None]:
                        report[rel] = {"sha256": h, "state": "different"}  # 409 before, nothing changed since
                        continue
                    code = self.put(rel, local[rel], h, "" if b is None else None)
                    if code == 200:
                        base[rel] = h
                        refused.pop(rel, None)
                        sent += 1
                        report[rel] = {"sha256": h, "state": "ok"}
                    else:
                        refused[rel] = [h, s["sha256"] if s else None]
                        report[rel] = {"sha256": h, "state": "different"}
                elif s and s["sha256"] != b:  # changed elsewhere: the user's file is not replaced
                    report[rel] = {"sha256": h, "state": "different"}
                else:
                    report[rel] = {"sha256": h, "state": "ok"}
            else:
                if b is not None or rel in removed:  # it was here and the user removed it: not taken in again
                    removed.add(rel)  # base is kept: a file the user puts back is a change made on this PC
                    report[rel] = {"sha256": None, "state": "removed"}
                    continue
                r = self.take_in(folder, rel, s)
                if r == "ok":
                    base[rel] = s["sha256"]
                    taken += 1
                    report[rel] = {"sha256": s["sha256"], "state": "ok"}
                elif r == "different":
                    report[rel] = {"sha256": None, "state": "different"}
        self.data["removed"] = sorted(removed)
        self.save()
        srv.json("PUT", "/api/luts/state?agent=%s" % aid, {"folder": str(folder), "reason": None, "files": report})
        diff = sum(1 for v in report.values() if v["state"] == "different")
        self.status = {"folder": str(folder), "reason": None, "files": len(local), "different": diff, "skipped": len(skipped)}
        if sent or taken or diff:
            log.info("LUT folder: %d sent, %d taken in, %d different from LPL (kept as they are)", sent, taken, diff)
        for rel, why in skipped.items():
            log.info("LUT file %s left out: %s", rel, why)


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
        self.wake = threading.Event()  # set: the main loop sends its next heartbeat now instead of after its pause
        self.server_caps = []          # what the server said it can do (heartbeat answer); [] = a server from before
        self.shared = SharedSync(self)  # R11: styles, presets, module layouts
        self.luts = LutSync(self)
        self.renderer = Renderer(self)  # previews that show the edit (darktable-cli)
        self.render_seen = set()        # render jobs this run has taken
        refresh_bridge(cfg)             # a newer darktable bridge came with this agent: put it in      # R19: darktable's LUT folder
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
            ws = [dict({k: v for k, v in it.items() if k in ("id", "filename", "status", "checkout_ts", "synced_ts", "error", "step", "progress")},
                       **({"copy_failed": True} if copy_failed(it) else {}))
                  for it in sorted(self.items.values(), key=lambda x: x.get("checkout_ts", 0))]
        running = darktable_running()
        return {"working_set": ws, "darktable_running": running, "bridge_alive": bridge_alive(),
                "bridge_status": bridge_status(running), "version": VERSION, "message": self.message,
                "agent_started": AGENT_STARTED,
                "local_url": LOCAL["url"], "local_error": LOCAL["error"],
                **({"shared_update": self.shared.update} if self.shared.active() else {}),
                **({"luts": self.luts.status} if self.luts.active() and self.luts.status else {}),
                **({"render_unavailable": render_unavailable(self.cfg)} if not self.renderer.available() else {}),
                **({"render": self.render_progress} if getattr(self, "render_progress", None) else {})}

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
        self.reread_name()
        return {"id": self.cfg["id"], "name": self.cfg["name"], "machine": machine_id(), "os": platform.system().lower(),
                "state": self.state(), "caps": [c for c in CAPS if c != "shared" or self.shared.enabled] + (["render"] if self.renderer.available() else []),
                "transfers": self.transfers()}

    def reread_name(self):
        """The setup run again while this agent runs (reinstalling does that) may have given this PC another
        name: take it from the config file, so LPL shows the new one without a restart."""
        try:
            m = config_path().stat().st_mtime
        except OSError:
            return
        if m == getattr(self, "_cfg_mtime", None):
            return
        self._cfg_mtime = m
        try:
            name = (json.loads(config_path().read_text(encoding="utf-8")).get("name") or "").strip()
        except (OSError, ValueError):
            return
        if name and name != self.cfg["name"]:
            log.info("name changed in the config: %s -> %s", self.cfg["name"], name)
            self.cfg["name"] = name

    def take_rename(self, name):
        """LPL was told another name for this PC: keep it in the config, so it outlives a restart or a reinstall."""
        name = (name or "").strip() if isinstance(name, str) else ""
        if not name or name == self.cfg["name"]:
            return
        log.info("renamed in LightPhotoLibrary: %s -> %s", self.cfg["name"], name)
        self.cfg["name"] = name
        try:
            on_disk = json.loads(config_path().read_text(encoding="utf-8")) if config_path().is_file() else dict(self.cfg)
            on_disk["name"] = name
            save_config(on_disk)
            self._cfg_mtime = config_path().stat().st_mtime
        except Exception as e:
            log.warning("could not save the new name: %s", e)

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
        self.stop_render = set(x for x in answer.get("stop_render") or [] if isinstance(x, int))
        sc = answer.get("server_caps")
        self.server_caps = [x for x in sc if isinstance(x, str)] if isinstance(sc, list) else []
        self.take_rename(answer.get("rename"))
        c = answer.get("cancel")
        if isinstance(c, dict):  # the whole list every time: what is not in it any more is not to be stopped
            self.cancel_jobs = set(c.get("jobs") or [])
            self.skip_photos = {str(i) for i in c.get("photos") or []}
            self.srv.interrupt(lambda tag: bool(tag) and self._given_up(tag[0], tag[1]))

    def report_done(self, jid, status) -> bool:
        """Tell the server a job ended (for an edit job with its lists, as in the heartbeat). False: not delivered
        (the main loop tries again after its next heartbeat)."""
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

    def confirm_ended(self):
        """After a heartbeat that the server answered: /done for every edit job that has ended (the heartbeat
        just said how; this takes it off this agent's list, or says it at all to a server from before batches)."""
        with self.lock:
            ended = [(jid, b["state"]) for jid, b in self.batches.items() if b["unsaid"]]
        for jid, st in ended:
            self.report_done(jid, st)

    # --- jobs
    def handle(self, job):
        kind, ids = job["kind"], [str(i) for i in (job.get("payload") or {}).get("ids") or []]
        if (job.get("payload") or {}).get("all"):
            ids = list(self.items.keys())
        log.info("job %s %s %s", job["id"], kind, ids)
        if kind == "shared-update":  # held until darktable is closed; tick() applies it and reports it done
            self.shared.hold(job)
            return
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
            elif kind == "forget":
                mode = (job.get("payload") or {}).get("mode") or "failed"
                for i in ids:
                    self.forget(i, mode)
            elif kind == "render":
                if job["id"] in self.render_seen:
                    return  # handed out again while this run still has it: the first one reports
                self.render_seen.add(job["id"])
                status = self.render_job(ids, job["id"]) or "done"
        except Exception as e:
            log.exception("job failed")
            self.message = "Last job failed: %s" % e
            status = "failed"
        with self.lock:
            b = self.batches.get(job["id"])
            if b:
                b.update(state=status, photo=None, bytes=0, unsaid=True)
        if b:
            # An edit job's end goes with a heartbeat sent right now, so the server learns in one message how
            # the batch ended and what the working set looks like after it (not "stopped" next to a row that
            # still says "copying"). The main loop then confirms it with /done, as for every job.
            self.wake.set()
        else:
            self.report_done(job["id"], status)

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

    RENDER_BATCH = 6      # photos per darktable-cli run
    RENDER_AHEAD = 8      # photos downloaded ahead of the one being rendered

    def render_job(self, ids, jid):
        """Photos on the NAS whose preview is from before their edit. A download thread fetches each original and
        XMP into a temporary folder (or the working-set copy is used) a few photos ahead, while darktable-cli renders
        them in batches; each JPEG is posted and the temporary files deleted. A photo that fails (a download cut off
        by sleep, a file darktable cannot read) is tried once more on its own before it counts as failed.
        Progress (with the last errors and where the time goes) goes to LPL with the heartbeat and to darktable through
        the bridge folder (render.txt; darktable's cancel writes render-stop.txt)."""
        tmp_root = data_dir() / "render" / ("job-%s" % jid)
        prog = self.render_progress = {"job": jid, "total": len(ids), "done": 0, "failed": 0, "current": None,
                                       "started": time.time(), "ended": None, "stopped": False, "errors": [],
                                       "download_s": 0.0, "render_s": 0.0, "upload_s": 0.0, "bytes": 0,
                                       "fetched": 0, "rendered": 0, "lacking": [], "lacking_modules": []}
        stop_file = BRIDGE / "render-stop.txt"  # names the job it stops: one left from another job does nothing

        def stopped():
            if jid in self.cancel_jobs or jid in getattr(self, "stop_render", ()):
                return True
            try:  # Cancel on the job in darktable's background jobs
                return stop_file.read_text().strip() == str(jid)
            except OSError:
                return False
        ready = Queue(maxsize=self.RENDER_AHEAD)
        END = object()
        self.render_tell_bridge(prog)

        def fetch():
            for pid in ids:
                if stopped():
                    break
                t = time.time()
                try:
                    src = self.render_source(pid, tmp_root)
                except Exception as e:
                    time.sleep(5)  # once more: the PC may just have woken up and the network come back
                    try:
                        src = self.render_source(pid, tmp_root)
                    except Exception as e2:
                        src = {"pid": pid, "name": str(pid), "error": "download: %s (first try: %s)" % (e2, e)}
                prog["download_s"] += time.time() - t
                if src.get("tmp"):
                    prog["fetched"] += 1  # downloaded (working-set copies are not)
                if src.get("raw") is not None and src.get("tmp"):
                    try:
                        prog["bytes"] += src["raw"].stat().st_size
                    except OSError:
                        pass
                ready.put(src)
            ready.put(END)
        threading.Thread(target=fetch, name="render-fetch", daemon=True).start()

        def finish(src, ok, why=None):
            if ok:
                prog["done"] += 1
            else:
                if isinstance(why, MissingModule):  # for "render these on another PC" in LPL
                    prog["lacking"].append(int(src["pid"]))
                    mods = re.findall(r"uses (.+?), which", str(why))
                    for m in (mods[0].split(", ") if mods else []):
                        if m not in prog["lacking_modules"]:
                            prog["lacking_modules"].append(m)
                prog["failed"] += 1
                why = str(why)[:400]
                prog["errors"] = (prog["errors"] + [{"photo": src.get("name") or src["pid"], "error": why}])[-5:]
                log.warning("preview of %s not rendered: %s", src.get("name") or src["pid"], why)
            if src.get("tmp"):
                shutil.rmtree(src["tmp"], ignore_errors=True)
            self.render_tell_bridge(prog)
            self.wake.set()  # the next heartbeat carries the progress now

        def single(src, outdir):
            out = outdir / ("%s-single.jpg" % src["pid"])
            t = time.time()
            try:
                render_preview(self.cfg, src["raw"], src["xmp"], out)
                prog["rendered"] += 1
            finally:
                prog["render_s"] += time.time() - t
            return out

        def post(src, out):
            t = time.time()
            try:
                self.srv.upload("/api/photos/%s/preview" % src["pid"], out.read_bytes(), "image/jpeg")
            finally:
                prog["upload_s"] += time.time() - t

        ended = False
        while not ended:
            if stopped():
                break
            first = ready.get()
            if first is END:
                break
            batch = [first]
            gather = time.time() + 0.5  # a moment for photos that are about to arrive, to share the run
            while len(batch) < self.RENDER_BATCH:
                try:
                    nxt = ready.get(timeout=max(0.0, gather - time.time()))
                except Empty:
                    break
                if nxt is END:
                    ended = True
                    break
                batch.append(nxt)
            for src in [x for x in batch if x.get("error")]:
                finish(src, False, src["error"])
            todo = [x for x in batch if not x.get("error")]
            if not todo:
                continue
            names = [x["name"] for x in todo]
            prog["current"] = names[0] + (" +%d" % (len(names) - 1) if len(names) > 1 else "")
            self.render_tell_bridge(prog)
            self.message = "Rendering previews: %d of %d" % (prog["done"] + prog["failed"] + len(todo), len(ids))
            outdir = tmp_root / ("out-%s" % uuid.uuid4().hex[:6])
            together = [x for x in todo if x.get("tmp")]  # downloaded, named <photo id>.<ext>: outputs cannot clash
            got, batch_err = {}, None
            if len(together) > 1:
                t = time.time()
                try:
                    got = render_batch(self.cfg, [x["raw"] for x in together], outdir)
                    prog["rendered"] += len(got)
                    if len(got) < len(together):
                        batch_err = "the batch run left out %d of %d" % (len(together) - len(got), len(together))
                except Exception as e:
                    batch_err = str(e)
                    log.warning("batch of %d not rendered together: %s", len(together), e)
                prog["render_s"] += time.time() - t
            for src in todo:
                out = got.get(src["raw"])
                try:
                    if out is None:  # alone: a working-set copy, a batch of one, or one the batch run left out
                        out = single(src, outdir)
                    post(src, out)
                    finish(src, True)
                except MissingModule as e:
                    finish(src, False, e)  # no point trying again here
                except Exception as e:
                    time.sleep(5)
                    try:
                        if out is None or not out.is_file():
                            out = single(src, outdir)
                        post(src, out)
                        finish(src, True)
                    except MissingModule as e2:
                        finish(src, False, e2)
                    except Exception as e2:
                        finish(src, False, "%s (first try: %s)%s" % (e2, e, ("; batch: %s" % batch_err) if batch_err else ""))
            shutil.rmtree(outdir, ignore_errors=True)
        was_stopped = stopped()
        if was_stopped:  # let the download thread see it and end; nothing more is fetched
            try:
                while True:
                    x = ready.get(timeout=2)
                    if x is END:
                        break
            except Empty:
                pass
        shutil.rmtree(tmp_root, ignore_errors=True)
        prog.update(ended=time.time(), current=None, stopped=was_stopped)
        self.render_tell_bridge(prog)
        try:
            stop_file.unlink()
        except OSError:
            pass
        self.wake.set()
        done, failed = prog["done"], prog["failed"]
        self.message = "Previews rendered: %d%s" % (done, (", %d failed (see the agent log)" % failed) if failed else "")
        log.info("%s (download %.0f s, darktable %.0f s, upload %.0f s)", self.message, prog["download_s"], prog["render_s"], prog["upload_s"])
        if was_stopped:
            return "stopped"
        return "done" if done or not failed else "failed"

    def render_tell_bridge(self, prog):
        """The render job's progress for darktable (the bridge shows it as a background job with Cancel):
        <bridge>/render.txt = job, done, failed, total, ended (0/1), stopped (0/1), current, tab separated."""
        try:
            BRIDGE.mkdir(parents=True, exist_ok=True)
            line = "\t".join(str(x) for x in (prog["job"], prog["done"], prog["failed"], prog["total"],
                                               1 if prog["ended"] else 0, 1 if prog["stopped"] else 0,
                                               (prog["current"] or "").replace("\t", " ")))
            tmp = BRIDGE / "render.txt.tmp"
            tmp.write_text(line + "\n", encoding="utf-8")
            os.replace(tmp, BRIDGE / "render.txt")
        except OSError:
            pass

    def render_source(self, pid, tmp_root: Path) -> dict:
        """One photo to render: its working-set copy, or its original and XMP downloaded as <id>.<ext> (+ .xmp, the
        sidecar name darktable-cli looks for) into a folder of its own."""
        it = self.items.get(pid)
        if it and Path(it["local_path"]).is_file() and Path(it["local_path"] + ".xmp").is_file():
            return {"pid": pid, "name": it.get("filename") or pid, "raw": Path(it["local_path"]),
                    "xmp": Path(it["local_path"] + ".xmp"), "tmp": None}
        name = self.photo_name(pid)
        d = tmp_root / pid
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        ext = Path(name).suffix or ".raw"
        raw = d / ("%s%s" % (pid, ext))
        xmp = d / ("%s%s.xmp" % (pid, ext))
        self.srv.download("/api/photos/%s/original" % pid, raw)
        self.srv.download("/api/photos/%s/xmp" % pid, xmp)
        return {"pid": pid, "name": name, "raw": raw, "xmp": xmp, "tmp": d}

    def photo_name(self, pid) -> str:
        """The photo's file name on the NAS (darktable-cli needs the real extension)."""
        r = self.srv.json("POST", "/api/photos/get", {"ids": [int(pid)]})
        items = r.get("items") or []
        name = (items[0].get("filename") if items else None) or ("photo-%s" % pid)
        return re.sub(r'[\\/:*?"<>|]', "_", name)

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
            if it and Path(it["local_path"]).is_file() and it.get("status") != "downloading" and not copy_failed(it):
                self.refresh_versions(str(pid), it)
                opened.append(Path(it["local_path"]))
                self._mark(b, "present", pid)
                continue
            try:
                info = self.srv.json("GET", "/api/photos/%s/checkout?agent=%s" % (pid, urllib.parse.quote(self.cfg["id"], safe="")))
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
                if "versions" in info:  # a server that knows versions (an old one does not say): copy them too
                    it["versions"] = {}
                    self.pull_versions(str(pid), it, info["versions"], given_up, (jid, str(pid)))
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
                    for f in local_versions(dest).values():
                        try:
                            f.unlink()
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
                it.update(status="error", error=str(e), copy_failed=True)
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
        sent_preview = False
        if preview is not None and preview.is_file():
            self.step(it, "sending preview")
            try:
                self.srv.upload("/api/photos/%s/preview" % pid, preview.read_bytes(), "image/jpeg")
                sent_preview = True
                if xmp.is_file():
                    it["preview_mtime"] = xmp.stat().st_mtime
            except Exception as e:
                log.warning("preview upload failed: %s", e)
            finally:
                self.step(it)
        todo = self.versions_to_send(it)
        v0 = xmp.is_file() and (force or abs(xmp.stat().st_mtime - it.get("synced_mtime", 0)) >= 0.01)
        if not v0 and not todo:
            return
        it["status"] = "syncing"
        self.step(it, "sending edit")
        try:
            merged = False
            if v0:
                # agent=: LPL records which device sent the edit ("last edited on Desktop")
                body, headers = self.srv.upload("/api/photos/%s/xmp?checkout_ts=%s&agent=%s" % (
                    pid, it["checkout_ts"], urllib.parse.quote(self.cfg["id"], safe="")), xmp.read_bytes(), "application/rdf+xml")
                merged = (headers.get("X-LPL-Merged") or headers.get("x-lpl-merged")) == "1"
                if merged:
                    # LPL had newer rating/labels/tags: put them into the local file and tell darktable to re-read it
                    xmp.write_bytes(body)
                    self.bridge.add("reload", Path(it["local_path"]))
                if xmp.is_file():
                    it["synced_mtime"] = xmp.stat().st_mtime
            if todo:
                self.send_versions(pid, it)
            it.update(status="synced", synced_ts=time.time(), error="")
            log.info("synced %s%s", it["filename"], " (merged)" if merged else "")
            if v0 and not sent_preview and xmp.is_file() and it.get("preview_mtime") != xmp.stat().st_mtime:
                self.renderer.request(pid)  # no rendering came with this edit: make one
        except Exception as e:
            it.update(status="error", error="sync failed: %s" % e)
        finally:
            self.step(it)
        self.save()

    # --- versions (R17): only with a server that lists them (it["versions"] is None otherwise: as before)
    def pull_versions(self, pid, it, listed, cancel=None, tag=None):
        """Copy the version sidecars the server lists (n >= 1) beside the RAW; remember each one's time."""
        local = Path(it["local_path"])
        known = it.setdefault("versions", {})
        for v in listed:
            n = int(v["n"])
            if n < 1 or not v.get("has_file", True):
                continue
            f = version_file(local, n)
            if not f.is_file():
                self.srv.download("/api/photos/%s/xmp?version=%d" % (pid, n), f, None, cancel, tag)
                known[str(n)] = f.stat().st_mtime

    def refresh_versions(self, pid, it):
        """Before a send, and when a photo that is here is asked for again: copy versions made elsewhere; a version
        deleted in LPL goes from here too if it is unchanged here, else it stays and is sent back (an edit wins).
        (Asks /versions, not /checkout: a checkout would tell LPL this device has seen every number.)"""
        if it.get("versions") is None:
            return
        try:
            listed = self.srv.json("GET", "/api/photos/%s/versions" % pid)["versions"]
        except Exception as e:
            log.info("versions of %s not refreshed: %s", pid, e)
            return
        local = Path(it["local_path"])
        on_server = {int(v["n"]) for v in listed if int(v["n"]) >= 1 and v.get("has_file", True)}
        known = it["versions"]
        for k in list(known):
            if int(k) in on_server:
                continue
            f = version_file(local, int(k))
            try:
                if f.is_file() and abs(f.stat().st_mtime - known[k]) < 0.01:
                    f.unlink()
            except OSError as e:
                log.warning("could not remove %s: %s", f, e)
                continue
            known.pop(k)  # a changed one stays as a new local version: send_versions posts it again
        try:
            self.pull_versions(pid, it, [v for v in listed if int(v["n"]) not in {int(k) for k in known}])
        except Exception as e:
            log.warning("versions of %s not copied: %s", pid, e)

    def versions_to_send(self, it):
        """Local version sidecars that are new or changed since the last send."""
        if it.get("versions") is None:
            return []
        known = it["versions"]
        return [n for n, f in sorted(local_versions(Path(it["local_path"])).items())
                if str(n) not in known or abs(f.stat().st_mtime - known[str(n)]) >= 0.01]

    def send_versions(self, pid, it):
        self.refresh_versions(pid, it)
        for n in self.versions_to_send(it):
            self.post_version(pid, it, n)

    def post_version(self, pid, it, n):
        local = Path(it["local_path"])
        f = version_file(local, n)
        for _ in range(4):
            body, headers, nxt = self.srv.upload_version("/api/photos/%s/xmp?checkout_ts=%s&agent=%s&version=%d" % (
                pid, it["checkout_ts"], urllib.parse.quote(self.cfg["id"], safe=""), n), f.read_bytes())
            if nxt is None:
                break
            # another device made this number first: ours becomes the free one LPL named (darktable keeps no number inside the XMP)
            g = version_file(local, nxt)
            if g.exists():
                raise RuntimeError("version %d exists here as well" % nxt)
            os.replace(f, g)
            log.info("version %d of %s is taken in LPL: renamed to %d", n, it["filename"], nxt)
            f, n = g, nxt
        else:
            raise RuntimeError("no free version number")
        if (headers.get("X-LPL-Merged") or headers.get("x-lpl-merged")) == "1":
            f.write_bytes(body)
        it["versions"][str(n)] = f.stat().st_mtime

    def release(self, pid: str):
        it = self.items.get(pid)
        if not it:
            return
        if copy_failed(it):  # nothing to save: what the copy left is deleted
            self.forget(pid, "failed")
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
            for f in [Path(str(p) + ".xmp"), p] + list(local_versions(p).values()):
                if f.exists():
                    f.unlink()
            with self.lock:
                self.items.pop(pid, None)
        except OSError as e:
            it.update(status="error", error="could not delete local copy (still open in darktable?): %s" % e)
        self.save()

    def forget(self, pid: str, mode: str = "failed"):
        """Take a photo off this PC's list. mode "failed": only a copy that never completed, with what it left;
        "keep": the files stay where they are, LPL just stops tracking them; "discard": delete the copy and its
        edits without saving them to the NAS (asked for explicitly on the Devices page)."""
        it = self.items.get(pid)
        if not it or it.get("status") in ("downloading", "syncing"):
            return
        if mode == "failed" and not copy_failed(it):
            return
        if mode != "keep":
            p = Path(it["local_path"])
            if mode == "discard" and darktable_running():
                self.bridge.add("forget", p)
                self.bridge.flush()
                time.sleep(1.5)
            try:
                for f in [Path(str(p) + ".xmp"), p, Path(str(p) + ".part")] + list(local_versions(p).values()):
                    if f.exists():
                        f.unlink()
            except OSError as e:
                it.update(status="error", error="could not delete local copy (still open in darktable?): %s" % e)
                self.save()
                return
        with self.lock:
            self.items.pop(pid, None)
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
            if it["status"] in ("downloading", "syncing", "error"):
                continue
            ms = [xmp.stat().st_mtime] if xmp.is_file() and abs(xmp.stat().st_mtime - it.get("synced_mtime", 0)) > 0.01 else []
            vs = local_versions(Path(it["local_path"])) if it.get("versions") is not None else {}
            ms += [vs[n].stat().st_mtime for n in self.versions_to_send(it) if n in vs]  # a new or changed version counts as an edit
            if not xmp.is_file() and not ms:
                continue
            m = max(ms) if ms else 0
            if ms:
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
                self.confirm_ended()
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
            try:
                first_shared = getattr(self, "_shared_started", False) is False and self.shared.active()
                self.shared.tick(force=first_shared)
                if first_shared:
                    self._shared_started = True
            except Exception as e:
                log.warning("shared styles and presets: %s", e)
            try:
                self.luts.tick()
            except Exception as e:
                log.warning("LUT folder: %s", e)
            if once:
                time.sleep(0.5)
                if self.luts.running is not None:
                    self.luts.running.join(600)
                while not self.jobs.empty():
                    time.sleep(0.2)
                time.sleep(0.5)
                try:  # report the final state
                    self.srv.json("POST", "/api/agents/heartbeat", self.heartbeat_body(), timeout=15)
                    self.confirm_ended()
                except Exception:
                    pass
                return
            # while photos are being copied: more often, so progress is fresh and a Stop arrives within a second
            self.wake.wait(min(backoff, 1.0) if self.batches else backoff)
            self.wake.clear()


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
