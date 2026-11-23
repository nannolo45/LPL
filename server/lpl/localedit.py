"""Local mode: photos are already on this PC, so darktable opens them where they are (no copying).
Talks to darktable through the same Lua bridge as the agent, using its own request file
(request-local.txt) and telling the bridge which folders belong to the local library (roots-local.txt).
When you leave an image in darkroom, the bridge exports a preview; this module picks it up and re-reads the XMP."""
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import db, edits, media, scanner, xmp
from .config import config

log = logging.getLogger("lpl.local")
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
_pending = []
_lock = threading.Lock()


def _bridge() -> Path:
    return config.bridge_dir


def darktable_running() -> bool:
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq darktable.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, creationflags=NO_WINDOW)
            return "darktable.exe" in out.stdout.lower()
        return subprocess.run(["pgrep", "-x", "darktable"], capture_output=True).returncode == 0
    except Exception:
        return False


def bridge_alive() -> bool:
    try:
        return time.time() - (_bridge() / "alive").stat().st_mtime < 12
    except OSError:
        return False


def _darktable_cmd():
    dtp = config.darktable or shutil.which("darktable") or ""
    if not dtp:
        raise RuntimeError("darktable not found: set it with the agent's --setup")
    return ["flatpak", "run", "org.darktable.Darktable"] if dtp == "flatpak" else [dtp]


def _launch(image: Path) -> None:
    lib = config.dt_library or str(config.data_dir / "darktable-library.db")
    cmd = _darktable_cmd() + ["--library", lib, str(image)]
    log.info("starting darktable: %s", cmd)
    kw = {"creationflags": 0x00000008 | 0x00000200} if IS_WIN else {"start_new_session": True}
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **kw)


def _queue(cmd: str, path: Path) -> None:
    with _lock:
        _pending.append("%s\t%s" % (cmd, path))


def flush() -> None:
    req = _bridge() / "request-local.txt"
    with _lock:
        if not _pending or req.exists():
            return
        tmp = _bridge() / "request-local.tmp"
        tmp.write_text("\n".join(_pending) + "\n", encoding="utf-8")
        os.replace(tmp, req)
        _pending.clear()


def write_roots() -> None:
    """Tell the bridge which folders are ours, so it exports previews for images in them."""
    try:
        (_bridge() / "outbox").mkdir(parents=True, exist_ok=True)
        text = "\n".join(str(r) for _, r in scanner.roots()) + "\n"
        f = _bridge() / "roots-local.txt"
        if not f.exists() or f.read_text(encoding="utf-8") != text:
            f.write_text(text, encoding="utf-8")
    except OSError as e:
        log.warning("cannot write bridge roots: %s", e)


def open_photos(ids) -> str:
    rows = db.conn().execute("SELECT id, relpath FROM photos WHERE id IN (%s)" % ",".join(str(int(i)) for i in ids)).fetchall()
    order = {int(i): k for k, i in enumerate(ids)}
    paths = [scanner.abs_of(r["relpath"]) for r in sorted(rows, key=lambda r: order.get(r["id"], 0))]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        return "Nothing to open (files not found)."
    write_roots()
    running = darktable_running()
    if running and bridge_alive():
        _queue("open", paths[0])
        for p in paths[1:]:
            _queue("import", p)
        flush()
        return "Opening in darktable…"
    if running and not IS_WIN:
        _launch(paths[0])  # Linux: handed to the running darktable over D-Bus
        for p in paths[1:]:
            _queue("import", p)
        return "Opening in darktable…"
    if running:
        for p in paths[1:]:
            _queue("import", p)
        _queue("open", paths[0])
        return ("darktable is open but the bridge script isn't active, so the photo can't be sent to it. "
                "In darktable: scripts module → lightphotolibrary → start lpl_bridge.")
    _launch(paths[0])
    for p in paths[1:]:
        _queue("import", p)
    return "Starting darktable…"


def _handle_outbox() -> None:
    outbox = _bridge() / "outbox"
    if not outbox.is_dir():
        return
    for marker in list(outbox.glob("*.done")):
        try:
            target = Path(marker.read_text(encoding="utf-8").strip())
            rel = scanner.rel_of(target.resolve())
        except (OSError, ValueError):
            continue  # not one of ours (the agent's working set, or another folder)
        row = db.conn().execute("SELECT id FROM photos WHERE relpath=?", (rel,)).fetchone()
        jpg = outbox / (marker.name[:-5] + ".jpg")
        if row:
            pid = row["id"]
            if jpg.is_file():
                try:
                    from PIL import Image
                    with Image.open(jpg) as im:
                        media.save_previews(im.convert("RGB"), pid)
                except Exception as e:
                    log.warning("preview from darktable failed: %s", e)
            x = xmp.read_for_image(target)
            from . import autotag
            with db.write_lock:
                if x:
                    scanner.apply_xmp_to_db(pid, x)
                    edits.observe_scan(pid, x)
                db.conn().execute("UPDATE photos SET preview_ver=preview_ver+1, preview_state=1 WHERE id=?", (pid,))
                if jpg.is_file():
                    autotag.preview_changed(pid, rendered=True)
                db.conn().commit()
            autotag.mark([pid])
        for f in (marker, jpg):
            try:
                f.unlink()
            except OSError:
                pass


def start_background() -> None:
    def loop():
        n = 0
        while True:
            try:
                flush()
                _handle_outbox()
                if n % 15 == 0:
                    write_roots()
            except Exception:
                log.exception("local edit loop")
            n += 1
            time.sleep(2)
    threading.Thread(target=loop, name="local-edit", daemon=True).start()
