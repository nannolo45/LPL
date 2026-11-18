"""Indexes the photo folder: new / changed / removed files, XMP changes, previews."""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db, media, xmp
from .config import SKIP_DIRS, SUPPORTED_EXTS, config

log = logging.getLogger("lpl.scan")

status = {"state": "idle", "phase": "", "done": 0, "total": 0, "last_scan": None, "previews_pending": 0, "error": ""}
_scan_lock = threading.Lock()
_preview_pool = None
_preview_inflight = set()
_inflight_lock = threading.Lock()


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext in SUPPORTED_EXTS:
                yield Path(dirpath) / fn, ext


def rel_of(p: Path) -> str:
    return p.relative_to(config.photo_root).as_posix()


def abs_of(relpath: str) -> Path:
    p = (config.photo_root / relpath).resolve()
    if config.photo_root not in p.parents and p != config.photo_root:
        raise ValueError("path outside photo root")
    return p


def apply_xmp_to_db(photo_id: int, x: dict, touch: bool = False) -> None:
    c = db.conn()
    db.set_photo_meta(photo_id, rating=x["rating"], labels=x["labels"], tags=x["tags"],
                      title=x["title"], description=x["description"], touch=touch)
    c.execute(
        "UPDATE photos SET edited=?, change_ts=COALESCE(?, change_ts), export_ts=COALESCE(?, export_ts), "
        "print_ts=COALESCE(?, print_ts), import_ts=COALESCE(?, import_ts), xmp_mtime=? WHERE id=?",
        (x["edited"], x["change_ts"], x["export_ts"], x["print_ts"], x["import_ts"], x.get("mtime"), photo_id),
    )


def scan(full: bool = False) -> None:
    if not _scan_lock.acquire(blocking=False):
        return
    try:
        status.update(state="scanning", phase="listing files", done=0, total=0, error="")
        root = config.photo_root
        if not root.is_dir():
            status.update(state="error", error="photo folder not found: %s" % root)
            return
        known = {r["relpath"]: r for r in db.conn().execute(
            "SELECT id, relpath, size, mtime, xmp_mtime, missing FROM photos").fetchall()}
        seen = set()
        changed = []      # (path, ext, rel, stat)
        xmp_changed = []  # (id, xmp dict)
        for path, ext in _walk(root):
            rel = rel_of(path)
            seen.add(rel)
            try:
                st = path.stat()
            except OSError:
                continue
            row = known.get(rel)
            if row is None or full or row["size"] != st.st_size or abs((row["mtime"] or 0) - st.st_mtime) > 1 or row["missing"]:
                changed.append((path, ext, rel, st))
            else:
                sc = xmp.sidecar_path(path)
                alt = xmp.alt_sidecar_path(path)
                xm = None
                for cand in (sc, alt):
                    try:
                        xm = cand.stat().st_mtime
                        break
                    except OSError:
                        pass
                if xm is not None and abs((row["xmp_mtime"] or 0) - xm) > 1:
                    x = xmp.read_for_image(path)
                    if x:
                        xmp_changed.append((row["id"], x))
        status.update(phase="reading metadata", total=len(changed))
        batch = 200
        for i in range(0, len(changed), batch):
            chunk = changed[i:i + batch]
            meta = media.read_metadata([c[0] for c in chunk])
            with db.write_lock:
                c = db.conn()
                for path, ext, rel, st in chunk:
                    m = meta.get(str(path), {})
                    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
                    row = known.get(rel)
                    fields = (folder, path.name, ext, st.st_size, st.st_mtime, m.get("taken"), m.get("camera"),
                              m.get("lens"), m.get("focal"), m.get("aperture"), m.get("iso"), m.get("exposure"),
                              m.get("width"), m.get("height"), m.get("orientation") or 1)
                    if row is None:
                        cur = c.execute(
                            "INSERT INTO photos(folder, filename, ext, size, mtime, taken, camera, lens, focal, aperture, iso, exposure,"
                            " width, height, orientation, relpath, first_seen, import_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            fields + (rel, time.time(), time.time()))
                        pid = cur.lastrowid
                    else:
                        pid = row["id"]
                        c.execute(
                            "UPDATE photos SET folder=?, filename=?, ext=?, size=?, mtime=?, taken=?, camera=?, lens=?, focal=?,"
                            " aperture=?, iso=?, exposure=?, width=?, height=?, orientation=?, missing=0, preview_state=0 WHERE id=?",
                            fields + (pid,))
                    x = xmp.read_for_image(path)
                    if x:
                        apply_xmp_to_db(pid, x)
                c.commit()
            status["done"] = min(len(changed), i + batch)
        if xmp_changed:
            status.update(phase="reading changed XMP files")
            with db.write_lock:
                for pid, x in xmp_changed:
                    apply_xmp_to_db(pid, x)
                db.conn().commit()
        missing = [r["id"] for rel, r in known.items() if rel not in seen and not r["missing"]]
        if missing:
            with db.write_lock:
                db.conn().executemany("UPDATE photos SET missing=1 WHERE id=?", [(m,) for m in missing])
                db.conn().commit()
        status.update(state="idle", phase="", last_scan=time.time())
        log.info("scan done: %d new/changed, %d xmp changed, %d missing", len(changed), len(xmp_changed), len(missing))
    except Exception as e:  # pragma: no cover
        log.exception("scan failed")
        status.update(state="error", error=str(e))
    finally:
        _scan_lock.release()
        queue_previews()


def _do_preview(pid: int, rel: str, ext: str, orient: int) -> None:
    try:
        media.make_previews(pid, abs_of(rel), ext, orient)
        state = 1
    except Exception as e:
        log.warning("preview failed for %s: %s", rel, e)
        state = -1
    with db.write_lock:
        c = db.conn()
        c.execute("UPDATE photos SET preview_state=?, preview_ver=preview_ver+1 WHERE id=?", (state, pid))
        c.commit()
    with _inflight_lock:
        _preview_inflight.discard(pid)
        status["previews_pending"] = len(_preview_inflight)


def queue_previews() -> None:
    global _preview_pool
    if _preview_pool is None:
        _preview_pool = ThreadPoolExecutor(max_workers=max(1, config.preview_workers), thread_name_prefix="preview")
    rows = db.conn().execute(
        "SELECT id, relpath, ext, orientation FROM photos WHERE preview_state=0 AND missing=0 ORDER BY taken DESC").fetchall()
    with _inflight_lock:
        for r in rows:
            if r["id"] in _preview_inflight:
                continue
            _preview_inflight.add(r["id"])
            _preview_pool.submit(_do_preview, r["id"], r["relpath"], r["ext"], r["orientation"] or 1)
        status["previews_pending"] = len(_preview_inflight)


def start_background() -> None:
    def loop():
        scan()
        while config.scan_interval > 0:
            time.sleep(config.scan_interval)
            scan()
    threading.Thread(target=loop, name="scanner", daemon=True).start()
