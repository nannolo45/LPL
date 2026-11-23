"""Indexes the photo folder: new / changed / removed files, XMP changes, previews."""
import itertools
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

from . import db, edits, media, versions, xmp
from .config import SKIP_DIRS, SUPPORTED_EXTS, config

log = logging.getLogger("lpl.scan")

status = {"state": "idle", "phase": "", "done": 0, "total": 0, "last_scan": None, "previews_pending": 0, "error": ""}
_scan_lock = threading.Lock()
_preview_inflight = set()
_inflight_lock = threading.Lock()
# previews are made in priority order: 0 = someone is looking at it right now, 1 = normal (newest photos first)
_pq: "queue.PriorityQueue" = queue.PriorityQueue()
_seq = itertools.count()
_workers_started = False


def excluded_folders() -> list:
    """Folders (relpaths) left out of the library: not scanned, not shown."""
    try:
        return [f for f in json.loads(db.get_setting("excluded_folders", "[]")) if f]
    except ValueError:
        return []


def hidden_folders() -> list:
    """Folders still indexed but hidden from the views unless 'show hidden' is on."""
    try:
        return [f for f in json.loads(db.get_setting("hidden_folders", "[]")) if f]
    except ValueError:
        return []


def _excluded_abs() -> set:
    out = set()
    for f in excluded_folders():
        try:
            out.add(str(abs_of(f)))
        except ValueError:
            pass
    return out


def _walk(root: Path, skip: set = frozenset(), side: Optional[list] = None):
    """Photos under root. side, when given, collects (directory, name) of the *_nn.EXT.xmp files met on the way (versions)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                       and str(Path(dirpath) / d) not in skip]
        for fn in filenames:
            if fn.startswith("."):
                continue
            ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
            if ext in SUPPORTED_EXTS:
                yield Path(dirpath) / fn, ext
            elif side is not None and ext == "xmp" and versions.parse_name(fn):
                side.append((Path(dirpath), fn))


def roots():
    """[(name, folder)]. NAS mode: one unnamed root. Local mode: the folders added in the web app,
    each shown as a top-level folder named after it."""
    if not config.local:
        return [("", config.photo_root)]
    try:
        items = json.loads(db.get_setting("roots", "[]"))
    except ValueError:
        items = []
    return [(r["name"], Path(r["path"]).resolve()) for r in items]


def rel_of(p: Path) -> str:
    for name, root in roots():
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        return "%s/%s" % (name, rel) if name else rel
    raise ValueError("not inside a library folder: %s" % p)


def abs_of(relpath: str) -> Path:
    if config.local:
        name, _, rest = relpath.partition("/")
        root = dict(roots()).get(name)
        if root is None:
            raise ValueError("unknown library folder: %s" % name)
    else:
        root, rest = config.photo_root, relpath
    root = root.resolve()
    p = (root / rest).resolve()
    if root not in p.parents and p != root:
        raise ValueError("path outside the library folder")
    return p


def apply_xmp_to_db(photo_id: int, x: dict, touch: bool = False, version: int = 0) -> None:
    """What an XMP says, into the database. version: which sidecar it is (R17). The photo's own columns show the
    cover version: they follow this XMP only when it is the cover's."""
    c = db.conn()
    if version or versions.has_rows(photo_id):
        versions.upsert(photo_id, version, x, touch=touch)
        if versions.cover_of(photo_id) != version:
            if version == 0:  # the file's time and stamps still belong to the photo
                c.execute("UPDATE photos SET change_ts=COALESCE(?, change_ts), export_ts=COALESCE(?, export_ts), "
                          "print_ts=COALESCE(?, print_ts), import_ts=COALESCE(?, import_ts), xmp_mtime=? WHERE id=?",
                          (x["change_ts"], x["export_ts"], x["print_ts"], x["import_ts"], x.get("mtime"), photo_id))
            return
        if version:
            versions.mirror_cover(photo_id, touch=touch)
            return
    db.set_photo_meta(photo_id, rating=x["rating"], labels=x["labels"], tags=x["tags"],
                      title=x["title"], description=x["description"], touch=touch)
    c.execute(
        "UPDATE photos SET edited=?, change_ts=COALESCE(?, change_ts), export_ts=COALESCE(?, export_ts), "
        "print_ts=COALESCE(?, print_ts), import_ts=COALESCE(?, import_ts), xmp_mtime=? WHERE id=?",
        (x["edited"], x["change_ts"], x["export_ts"], x["print_ts"], x["import_ts"], x.get("mtime"), photo_id),
    )


def _index(changed, known, progress=False):
    """Read metadata + XMP for (path, ext, relpath, stat) tuples and insert/update them."""
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
                    edits.observe_scan(pid, x)
            c.commit()
        if progress:
            status["done"] = min(len(changed), i + batch)


def index_paths(paths) -> dict:
    """Index specific files right away (used when photos arrive from a PC). Returns {relpath: id}."""
    known = {r["relpath"]: r for r in db.conn().execute("SELECT id, relpath, missing FROM photos").fetchall()}
    items = []
    for p in paths:
        p = Path(p)
        ext = p.name.rsplit(".", 1)[-1].lower() if "." in p.name else ""
        if ext in SUPPORTED_EXTS and p.is_file():
            items.append((p, ext, rel_of(p), p.stat()))
    _index(items, known)
    queue_previews()
    rels = [i[2] for i in items]
    if not rels:
        return {}
    rows = db.conn().execute("SELECT id, relpath FROM photos WHERE relpath IN (%s)" % ",".join("?" * len(rels)), rels).fetchall()
    return {r["relpath"]: r["id"] for r in rows}


def scan(full: bool = False) -> None:
    if not _scan_lock.acquire(blocking=False):
        return
    try:
        status.update(state="scanning", phase="listing files", done=0, total=0, error="")
        rts = roots()
        if not rts:
            status.update(state="idle", phase="", error="No folders yet: add one under 'Send to NAS' → Library folders.")
            return
        missing_roots = [str(r) for _, r in rts if not r.is_dir()]
        if missing_roots and len(missing_roots) == len(rts):
            status.update(state="error", error="photo folder not found: %s" % ", ".join(missing_roots))
            return
        known = {r["relpath"]: r for r in db.conn().execute(
            "SELECT id, relpath, size, mtime, xmp_mtime, missing FROM photos").fetchall()}
        seen = set()
        side = []         # (folder, name) of the version sidecars (IMG_01_02.RAF.xmp) met on the way
        changed = []      # (path, ext, rel, stat)
        xmp_changed = []  # (id, xmp dict)
        skip = _excluded_abs()
        def walk_all():
            for _, r in rts:
                if r.is_dir():
                    yield from _walk(r, skip, side)
        offline = tuple("%s/" % n for n, r in rts if n and not r.is_dir())
        for path, ext in walk_all():
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
        _index(changed, known, progress=True)
        if xmp_changed:
            status.update(phase="reading changed XMP files")
            with db.write_lock:
                for pid, x in xmp_changed:
                    apply_xmp_to_db(pid, x)
                    edits.observe_scan(pid, x)  # a changed darktable edit that no agent posted: "edited outside LPL"
                db.conn().commit()
            from . import autotag
            autotag.mark([pid for pid, _x in xmp_changed])
        try:
            nv = versions.sync(side, seen, rel_of, edits)  # versions of photos (also those whose photo did not change)
        except Exception:  # pragma: no cover
            log.exception("version scan failed")
            nv = 0
        # photos in a folder that is currently unplugged (e.g. external drive) are kept as they are
        missing = [r["id"] for rel, r in known.items() if rel not in seen and not r["missing"] and not (offline and rel.startswith(offline))]
        if missing:
            with db.write_lock:
                db.conn().executemany("UPDATE photos SET missing=1 WHERE id=?", [(m,) for m in missing])
                db.conn().commit()
        status.update(state="idle", phase="", last_scan=time.time())
        log.info("scan done: %d new/changed, %d xmp changed, %d versions, %d missing", len(changed), len(xmp_changed), nv, len(missing))
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
    from . import autotag
    with db.write_lock:
        c = db.conn()
        c.execute("UPDATE photos SET preview_state=?, preview_ver=preview_ver+1 WHERE id=?", (state, pid))
        if state == 1:
            autotag.preview_changed(pid, rendered=False)
        c.commit()
    if state == 1:
        autotag.mark([pid])


def _worker() -> None:
    while True:
        _prio, _n, pid, rel, ext, orient = _pq.get()
        try:
            with _inflight_lock:
                if pid not in _preview_inflight:
                    continue  # already done (it was queued twice: normal + priority)
            _do_preview(pid, rel, ext, orient)
        except Exception:  # pragma: no cover
            log.exception("preview worker")
        finally:
            with _inflight_lock:
                _preview_inflight.discard(pid)
                status["previews_pending"] = len(_preview_inflight)


def _ensure_workers() -> None:
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    for i in range(max(1, config.preview_workers)):
        threading.Thread(target=_worker, name="preview-%d" % i, daemon=True).start()


def _enqueue(rows, prio: int) -> None:
    _ensure_workers()
    with _inflight_lock:
        for r in rows:
            if prio > 0 and r["id"] in _preview_inflight:
                continue
            _preview_inflight.add(r["id"])
            _pq.put((prio, next(_seq), r["id"], r["relpath"], r["ext"], r["orientation"] or 1))
        status["previews_pending"] = len(_preview_inflight)


def queue_previews() -> None:
    rows = db.conn().execute(
        "SELECT id, relpath, ext, orientation FROM photos WHERE preview_state=0 AND missing=0 ORDER BY taken DESC").fetchall()
    _enqueue(rows, 1)


def prioritize(ids) -> None:
    """Make these previews next (folder covers, photos on screen)."""
    ids = [int(i) for i in ids][:500]
    if not ids:
        return
    rows = db.conn().execute("SELECT id, relpath, ext, orientation FROM photos WHERE preview_state=0 AND missing=0 AND id IN (%s)"
                             % ",".join(map(str, ids))).fetchall()
    _enqueue(rows, 0)


def start_background() -> None:
    def loop():
        scan()
        while config.scan_interval > 0:
            time.sleep(config.scan_interval)
            scan()
    threading.Thread(target=loop, name="scanner", daemon=True).start()
