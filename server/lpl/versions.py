"""Versions of a photo (darktable duplicates, request R17; design in docs/versions-r17-design.md).

Version 0 is the photo as always: IMG_01.RAF with IMG_01.RAF.xmp. Version n >= 1 is a second sidecar beside it,
IMG_01_02.RAF.xmp (two digits, three from 100): the same RAW, another edit. They never become photos of their own.

Storage: `photo_versions` has one row per version, but only for photos that have at least one version besides 0
(then v0 has a row too). A photo without versions has no rows: it is version 0 and its values are in `photos`.
`photos.rating`, labels, tags and `edited` always show the COVER version (v0 unless the owner chose another), so
tiles, filters, series and everything that reads `photos` keep working unchanged.
A number is never reused: `photo_version_hw` remembers the highest number ever seen for a photo.
"""
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db, xmp
from .config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS photo_versions (
    photo_id INTEGER NOT NULL,
    n INTEGER NOT NULL,
    name TEXT DEFAULT '',
    xmp_path TEXT,                  -- the sidecar's file name, beside the photo
    mtime REAL,
    size INTEGER,
    cover INTEGER DEFAULT 0,
    rating INTEGER DEFAULT 0,
    labels TEXT DEFAULT '[]',       -- JSON list of 0..4
    tags TEXT DEFAULT '[]',         -- JSON list (all tags of the XMP, as in photo_tags)
    edited INTEGER DEFAULT 0,
    meta_mtime REAL DEFAULT 0,      -- when LPL last changed rating, labels or tags of this version
    preview_ver INTEGER DEFAULT 0,
    PRIMARY KEY (photo_id, n)
);
CREATE TABLE IF NOT EXISTS photo_version_hw (photo_id INTEGER PRIMARY KEY, hw INTEGER NOT NULL);
-- which versions an agent was told about when it checked a photo out (for 409 version_exists)
CREATE TABLE IF NOT EXISTS version_checkouts (
    agent_id TEXT NOT NULL,
    photo_id INTEGER NOT NULL,
    ts REAL,
    ns TEXT NOT NULL,
    PRIMARY KEY (agent_id, photo_id)
);
-- the version an edit event is about (only for versions >= 1; no row: version 0)
CREATE TABLE IF NOT EXISTS edit_event_version (event_id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
-- edit_baseline (edits.py) for versions >= 1
CREATE TABLE IF NOT EXISTS edit_baseline_v (
    photo_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    history TEXT NOT NULL,
    xmp_mtime REAL,
    PRIMARY KEY (photo_id, version)
);
"""

_NAME = re.compile(r"^(.+)_(\d{2,})\.([A-Za-z0-9]+)\.xmp$")


def migrate(c) -> None:
    """Tables and columns for versions; changes nothing the second time. Existing libraries need nothing else:
    the next scan lists the version files that lie beside their photos."""
    c.executescript(SCHEMA)
    for t in ("series_items", "folder_items"):  # an entry can pin a version (NULL: the photo's cover)
        if "version" not in {r[1] for r in c.execute("PRAGMA table_info(%s)" % t).fetchall()}:
            c.execute("ALTER TABLE %s ADD COLUMN version INTEGER" % t)


def parse_name(fn: str):
    """'IMG_01_02.RAF.xmp' -> ('IMG_01.RAF', 2); None when it is not a version sidecar name."""
    m = _NAME.match(fn)
    if not m:
        return None
    return "%s.%s" % (m.group(1), m.group(3)), int(m.group(2))


def version_path(img: Path, n: int) -> Path:
    return xmp.version_sidecar_path(img, n)


def rel_of_version(relpath: str, n: int) -> str:
    """Name used for a version's backups: the photo's relpath with _nn before the extension."""
    if n <= 0:
        return relpath
    stem, dot, ext = relpath.rpartition(".")
    return "%s_%02d%s%s" % (stem, n, dot, ext)


# ---------------------------------------------------------------- rows
def _rows(pid: int) -> list:
    return db.conn().execute("SELECT * FROM photo_versions WHERE photo_id=? ORDER BY n", (pid,)).fetchall()


def has_rows(pid: int) -> bool:
    return db.conn().execute("SELECT 1 FROM photo_versions WHERE photo_id=? LIMIT 1", (pid,)).fetchone() is not None


def cover_of(pid: int) -> int:
    r = db.conn().execute("SELECT n FROM photo_versions WHERE photo_id=? AND cover=1", (pid,)).fetchone()
    return r["n"] if r else 0


def hw_of(pid: int) -> int:
    c = db.conn()
    r = c.execute("SELECT hw FROM photo_version_hw WHERE photo_id=?", (pid,)).fetchone()
    m = c.execute("SELECT MAX(n) m FROM photo_versions WHERE photo_id=?", (pid,)).fetchone()["m"] or 0
    return max(r["hw"] if r else 0, m)


def next_n(pid: int) -> int:
    """The next free number: the highest ever used plus one (a deleted number is not used again)."""
    return hw_of(pid) + 1


def _bump_hw(pid: int, n: int) -> None:
    db.conn().execute("INSERT INTO photo_version_hw(photo_id,hw) VALUES(?,?) ON CONFLICT(photo_id) DO UPDATE SET "
                      "hw=MAX(hw, excluded.hw)", (pid, n))


def ensure_rows(pid: int) -> None:
    """Give a photo that had no versions its version 0 row, taken from `photos` (caller holds write_lock)."""
    c = db.conn()
    if has_rows(pid):
        return
    p = c.execute("SELECT filename, xmp_mtime, rating, edited FROM photos WHERE id=?", (pid,)).fetchone()
    if not p:
        return
    c.execute("INSERT INTO photo_versions(photo_id,n,name,xmp_path,mtime,cover,rating,labels,tags,edited) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (pid, 0, "", p["filename"] + ".xmp", p["xmp_mtime"], 1, p["rating"] or 0,
               json.dumps(db.photo_labels(pid)), json.dumps(db.photo_tags(pid)), p["edited"] or 0))


def upsert(pid: int, n: int, x: dict, mtime: Optional[float] = None, size: Optional[int] = None,
           touch: bool = False) -> None:
    """Record what a version's XMP says (x from xmp.parse / read). Caller holds write_lock; mirrors to `photos`
    when n is the cover (see mirror_cover)."""
    c = db.conn()
    ensure_rows(pid)
    p = c.execute("SELECT filename FROM photos WHERE id=?", (pid,)).fetchone()
    fn = p["filename"] if p else ""
    xp = (fn + ".xmp") if n == 0 else Path(version_path(Path(fn), n)).name
    vals = (x.get("rating") or 0, json.dumps(list(x.get("labels") or [])), json.dumps(list(x.get("tags") or [])),
            x.get("edited") or 0, mtime if mtime is not None else x.get("mtime"), size, xp)
    if c.execute("SELECT 1 FROM photo_versions WHERE photo_id=? AND n=?", (pid, n)).fetchone():
        c.execute("UPDATE photo_versions SET rating=?, labels=?, tags=?, edited=?, mtime=?, size=COALESCE(?, size), xmp_path=?"
                  + (", meta_mtime=%f" % db.now() if touch else "") + " WHERE photo_id=? AND n=?", vals + (pid, n))
    else:
        c.execute("INSERT INTO photo_versions(photo_id,n,rating,labels,tags,edited,mtime,size,xmp_path,cover,meta_mtime) "
                  "VALUES(?,?,?,?,?,?,?,?,?,0,?)", (pid, n) + vals + (db.now() if touch else 0,))
    if "version_name" in x:
        c.execute("UPDATE photo_versions SET name=? WHERE photo_id=? AND n=?", (x.get("version_name") or "", pid, n))
    _bump_hw(pid, n)


def set_meta(pid: int, n: int, rating=None, labels=None, tags=None, touch: bool = True) -> None:
    """Change rating / labels / tags of one version (caller holds write_lock, commits). The photo's own columns follow
    when n is the cover."""
    c = db.conn()
    ensure_rows(pid)
    r = c.execute("SELECT * FROM photo_versions WHERE photo_id=? AND n=?", (pid, n)).fetchone()
    if not r:
        raise KeyError(n)
    if rating is not None:
        c.execute("UPDATE photo_versions SET rating=? WHERE photo_id=? AND n=?", (int(rating), pid, n))
    if labels is not None:
        c.execute("UPDATE photo_versions SET labels=? WHERE photo_id=? AND n=?",
                  (json.dumps(sorted(set(int(x) for x in labels if 0 <= int(x) <= 4))), pid, n))
    if tags is not None:  # darktable's own tags stay as they are in the row
        keep = [t for t in json.loads(r["tags"] or "[]") if t.startswith("darktable|")]
        mine = [t.strip().strip("|") for t in tags if t.strip().strip("|") and not t.startswith("darktable|")]
        c.execute("UPDATE photo_versions SET tags=? WHERE photo_id=? AND n=?", (json.dumps(sorted(set(keep + mine))), pid, n))
    if touch:
        c.execute("UPDATE photo_versions SET meta_mtime=? WHERE photo_id=? AND n=?", (db.now(), pid, n))
    if n == cover_of(pid):
        mirror_cover(pid, touch=touch)


def mirror_cover(pid: int, touch: bool = False, bump_preview: bool = False) -> None:
    """Copy the cover version's rating, labels, tags and edited flag into `photos` (the tile shows the cover's)."""
    c = db.conn()
    r = c.execute("SELECT * FROM photo_versions WHERE photo_id=? AND cover=1", (pid,)).fetchone()
    if not r:
        return
    db.set_photo_meta(pid, rating=r["rating"], labels=json.loads(r["labels"] or "[]"), tags=json.loads(r["tags"] or "[]"), touch=touch)
    c.execute("UPDATE photos SET edited=?" + (", preview_ver=preview_ver+1" if bump_preview else "") + " WHERE id=?", (r["edited"], pid))


def set_cover(pid: int, n: int) -> None:
    """Caller holds write_lock and commits. Raises KeyError for a number that is not a version of the photo."""
    c = db.conn()
    if n == cover_of(pid) and has_rows(pid):
        return
    if n == 0 and not has_rows(pid):
        return
    if n != 0 and not row_of(pid, n):
        raise KeyError(n)  # (nothing written yet)
    ensure_rows(pid)
    c.execute("UPDATE photo_versions SET cover=(n=?) WHERE photo_id=?", (n, pid))
    mirror_cover(pid, touch=False, bump_preview=True)


def _drop_rows_if_alone(pid: int) -> None:
    """When only version 0 is left, the photo is a plain photo again: no rows."""
    c = db.conn()
    left = c.execute("SELECT n, cover FROM photo_versions WHERE photo_id=?", (pid,)).fetchall()
    if left and all(r["n"] == 0 for r in left):
        c.execute("DELETE FROM photo_versions WHERE photo_id=?", (pid,))


def remove_row(pid: int, n: int) -> None:
    """A version's file is gone (deleted here, or outside LPL). Caller holds write_lock."""
    c = db.conn()
    was_cover = cover_of(pid) == n
    c.execute("DELETE FROM photo_versions WHERE photo_id=? AND n=?", (pid, n))
    c.execute("DELETE FROM edit_baseline_v WHERE photo_id=? AND version=?", (pid, n))
    if was_cover:
        c.execute("UPDATE photo_versions SET cover=(n=0) WHERE photo_id=?", (pid,))
        mirror_cover(pid, bump_preview=True)
    _drop_rows_if_alone(pid)


def preview_files(pid: int, n: int):
    from . import media
    return media.preview_paths(pid, n)


def delete(pid: int, n: int, img: Path, relpath: str) -> Optional[Path]:
    """Move version n's sidecar to the backup folder (never erased) and forget the version. Caller holds write_lock.
    Returns where the file went (None when there was no file)."""
    import shutil
    if n <= 0:
        raise ValueError("version 0 cannot be deleted")
    p = version_path(img, n)
    dest = None
    if p.is_file():
        dest = config.backup_dir / ("%s.deleted-%d.xmp" % (rel_of_version(relpath, n), int(time.time())))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
    remove_row(pid, n)
    for f in preview_files(pid, n):
        try:
            f.unlink()
        except OSError:
            pass
    return dest


# ---------------------------------------------------------------- what the API shows
def list_versions(pid: int) -> List[Dict[str, Any]]:
    """[{n, name, mtime, size, rating, labels, tags, edited, cover, has_file, preview_ver}] by number. A photo without
    versions is one entry: version 0."""
    rows = _rows(pid)
    if not rows:
        p = db.conn().execute("SELECT xmp_mtime, rating, edited, preview_ver FROM photos WHERE id=?", (pid,)).fetchone()
        if not p:
            return []
        return [{"n": 0, "name": "", "mtime": p["xmp_mtime"], "size": None, "rating": p["rating"] or 0,
                 "labels": db.photo_labels(pid), "tags": [t for t in db.photo_tags(pid) if not t.startswith("darktable|")],
                 "edited": p["edited"] or 0, "cover": True, "has_file": p["xmp_mtime"] is not None, "preview_ver": p["preview_ver"] or 0}]
    out = []
    for r in rows:
        out.append({"n": r["n"], "name": r["name"] or "", "mtime": r["mtime"], "size": r["size"], "rating": r["rating"] or 0,
                    "labels": json.loads(r["labels"] or "[]"),
                    "tags": [t for t in json.loads(r["tags"] or "[]") if not t.startswith("darktable|")],
                    "edited": r["edited"] or 0, "cover": bool(r["cover"]), "has_file": r["mtime"] is not None,
                    "preview_ver": r["preview_ver"] or 0})
    return out


def row_of(pid: int, n: int):
    return db.conn().execute("SELECT * FROM photo_versions WHERE photo_id=? AND n=?", (pid, n)).fetchone()


def meta_mtime(pid: int, n: int, default: float) -> float:
    r = row_of(pid, n)
    return (r["meta_mtime"] or 0) if r else default


def meta_of(pid: int, n: int) -> Optional[dict]:
    """rating / labels / tags (no darktable| tags) of a version; None when it has no row and is not version 0."""
    r = row_of(pid, n)
    if r:
        return {"rating": r["rating"] or 0, "labels": json.loads(r["labels"] or "[]"),
                "tags": [t for t in json.loads(r["tags"] or "[]") if not t.startswith("darktable|")]}
    if n == 0:
        p = db.conn().execute("SELECT rating FROM photos WHERE id=?", (pid,)).fetchone()
        return {"rating": p["rating"] or 0, "labels": db.photo_labels(pid),
                "tags": [t for t in db.photo_tags(pid) if not t.startswith("darktable|")]} if p else None
    return None


# ---------------------------------------------------------------- checkout record (409 version_exists)
def record_checkout(agent: str, pid: int, ns: List[int]) -> None:
    with db.write_lock:
        db.conn().execute("INSERT INTO version_checkouts(agent_id,photo_id,ts,ns) VALUES(?,?,?,?) ON CONFLICT(agent_id,photo_id) "
                          "DO UPDATE SET ts=excluded.ts, ns=excluded.ns", (agent, pid, db.now(), json.dumps(ns)))
        db.conn().commit()


def in_checkout(agent: str, pid: int, n: int) -> bool:
    if not agent:
        return False
    r = db.conn().execute("SELECT ns FROM version_checkouts WHERE agent_id=? AND photo_id=?", (agent, pid)).fetchone()
    try:
        return bool(r) and n in json.loads(r["ns"])
    except ValueError:
        return False


# ---------------------------------------------------------------- the scan
def sync(side: list, seen: set, rel_of, edits_mod=None) -> int:
    """side: [(directory Path, file name)] of the *_nn.EXT.xmp files the walk met; seen: relpaths of the photos found;
    rel_of: Path -> relpath. Lists them under their photos, and forgets versions whose file is gone. Returns the
    number of versions that were new or changed."""
    c = db.conn()
    found: Dict[str, Dict[int, Path]] = {}
    for d, fn in side:
        pn = parse_name(fn)
        if not pn:
            continue
        base, n = pn
        try:
            rel = rel_of(d / base)
        except ValueError:
            continue
        if rel not in seen:
            continue  # no such photo (a sidecar of a file LPL does not index)
        if rel_of(d / fn[:-4]) in seen:
            continue  # IMG_01_02.RAF is a photo of its own: this is its sidecar, not a version of IMG_01.RAF
        found.setdefault(rel, {})[n] = d / fn
    ids = {r["relpath"]: r["id"] for r in c.execute("SELECT id, relpath FROM photos WHERE missing=0").fetchall()}
    with_rows = {r["photo_id"] for r in c.execute("SELECT DISTINCT photo_id FROM photo_versions").fetchall()}
    changed = 0
    todo = {ids[r]: v for r, v in found.items() if r in ids}
    from . import autotag
    with db.write_lock:
        for pid, vs in todo.items():
            have = {r["n"]: r for r in _rows(pid)}
            touched_cover = False
            for n, p in sorted(vs.items()):
                try:
                    st = p.stat()
                except OSError:
                    continue
                row = have.get(n)
                if row is not None and row["mtime"] is not None and abs(row["mtime"] - st.st_mtime) <= 1:
                    continue
                try:
                    x = xmp.parse(p.read_bytes())
                except Exception:
                    continue  # not readable now: next scan
                x["mtime"] = st.st_mtime
                upsert(pid, n, x, mtime=st.st_mtime, size=st.st_size)
                if edits_mod is not None:
                    edits_mod.observe_scan(pid, x, version=n)
                changed += 1
                touched_cover = touched_cover or n == cover_of(pid)
            for n in [n for n in have if n >= 1 and n not in vs]:
                remove_row(pid, n)
                changed += 1
                touched_cover = True
            if touched_cover:
                mirror_cover(pid, bump_preview=True)
                autotag.mark([pid])
        seen_ids = {ids[r] for r in seen if r in ids}
        for pid in (with_rows & seen_ids) - set(todo):  # all their version files are gone
            for r in _rows(pid):
                if r["n"] >= 1:
                    remove_row(pid, r["n"])
                    changed += 1
        c.commit()
    return changed


# ---------------------------------------------------------------- series and board entries
def decorate(items: list, pins: Dict[int, Optional[int]]) -> None:
    """Items are photo dicts (api.photo_dicts); pins maps photo id -> pinned version (or None). Adds `pin` (the
    pinned number, None when the entry follows the cover) and `show` (the version the entry shows: the pin if that
    version still exists, else the photo's cover)."""
    ids = [it["id"] for it in items if pins.get(it["id"]) is not None]
    have = set()
    if ids:
        for r in db.conn().execute("SELECT photo_id, n FROM photo_versions WHERE photo_id IN (%s)" % ",".join(map(str, ids))).fetchall():
            have.add((r["photo_id"], r["n"]))
    for it in items:
        pin = pins.get(it["id"])
        if pin is not None and (it["id"], pin) not in have and pin != 0:
            pin = None  # that version is gone
        it["pin"] = pin
        it["show"] = pin if pin is not None else it.get("cover", 0)


def set_pin(table: str, key: str, owner: int, pid: int, n: Optional[int]) -> None:
    """Pin a version to a series entry or a folder-board entry (n None: follow the cover). Caller holds write_lock.
    Raises KeyError for a version the photo does not have."""
    c = db.conn()
    if n is not None and n != 0 and not row_of(pid, n):
        raise KeyError(n)
    if not c.execute("SELECT 1 FROM %s WHERE %s=? AND photo_id=?" % (table, key), (owner, pid)).fetchone():
        raise LookupError(pid)
    c.execute("UPDATE %s SET version=? WHERE %s=? AND photo_id=?" % (table, key), (n, owner, pid))
