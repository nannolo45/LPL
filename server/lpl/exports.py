# SPDX-License-Identifier: GPL-3.0-or-later
"""Exports received from the darktable Android app (R18, docs/exports-r18.md): where they are stored, the table, the
list / download / delete. The upload itself is streamed to disk in api.py."""
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db, scanner
from .config import config

FORMATS = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "tif": "tiff", "tiff": "tiff", "webp": "webp"}
MAX_BYTES = int(os.environ.get("LPL_MAX_EXPORT_MB", "4096")) * 1024 * 1024
SERVER_CAPS = ["versions", "exports"]


def clean_name(name: str) -> str:
    """A bare file name: no folders, no leading dots; ValueError when it is not a name of a supported format."""
    n = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lstrip(".")
    if not n or n in (".", "..") or "\x00" in n or len(n) > 200:
        raise ValueError("bad file name")
    ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
    if ext not in FORMATS:
        raise ValueError("not an export format (jpg, png, tif, webp): %s" % n)
    return n


def sniff_ok(head: bytes, fmt: str) -> bool:
    return {"jpeg": head[:3] == b"\xff\xd8\xff", "png": head[:8] == b"\x89PNG\r\n\x1a\n",
            "tiff": head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
            "webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP"}[fmt]


def exports_root() -> Path:
    """Setting "exports folder" (Settings > Devices; pref `exportsFolder`): relative to the library root, or absolute.
    Empty = <library root>/exports."""
    import json
    try:
        s = json.loads(db.get_setting("pref.settings") or "null")
    except ValueError:
        s = None
    v = (s.get("exportsFolder") if isinstance(s, dict) else "") or ""
    v = v.strip() if isinstance(v, str) else ""
    root = config.photo_root
    p = Path(v) if v else Path("exports")
    p = p if p.is_absolute() else root / p
    p = p.resolve()
    if not v.startswith("/") and root not in p.parents and p != root:
        p = (root / "exports").resolve()  # a relative setting never leaves the library
    return p


def skip_dirs() -> set:
    """The exports folder, when it is inside the library: the scanner must not take exports for photos."""
    try:
        return {str(exports_root())}
    except Exception:
        return set()


def shown(p: Path) -> str:
    """The path as the API shows it: relative to the library root when inside it, else absolute."""
    try:
        return p.relative_to(config.photo_root).as_posix()
    except ValueError:
        return str(p)


def path_of(stored: str) -> Path:
    p = Path(stored)
    return p if p.is_absolute() else config.photo_root / p


def target_dir(photo_relpath: str) -> Path:
    folder = photo_relpath.rsplit("/", 1)[0] if "/" in photo_relpath else ""
    d = (exports_root() / folder).resolve()
    if exports_root() not in d.parents and d != exports_root():
        raise ValueError("bad folder")
    return d


def free_name(d: Path, name: str) -> Path:
    """d/name, or d/'stem (2).ext', (3) ...: never an existing file."""
    p = d / name
    stem, dot, ext = name.rpartition(".")
    i = 2
    while p.exists():
        p = d / ("%s (%d).%s" % (stem, i, ext))
        i += 1
    return p


def add(pid: int, version: int, tmp: Path, dest_dir: Path, name: str, size: int, agent: Optional[str]) -> Dict[str, Any]:
    """Move the finished upload tmp into place (never over a file) and record it. Caller holds db.write_lock."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = free_name(dest_dir, name)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dest)
    fmt = FORMATS[dest.suffix.lstrip(".").lower()]
    cur = db.conn().execute(
        "INSERT INTO photo_exports(photo_id,version,path,name,size,format,created,agent) VALUES(?,?,?,?,?,?,?,?)",
        (pid, version, shown(dest), dest.name, size, fmt, time.time(), agent))
    return {"export_id": cur.lastrowid, "path": shown(dest)}


def _row(r, agents: Dict[str, str]) -> Dict[str, Any]:
    return {"id": r["id"], "version": r["version"], "name": r["name"], "path": r["path"], "size": r["size"], "format": r["format"],
            "created": r["created"], "agent": r["agent"], "device": agents.get(r["agent"] or "") or r["agent"],
            "has_file": path_of(r["path"]).is_file()}


def list_for(pid: int) -> List[Dict[str, Any]]:
    c = db.conn()
    agents = {a["id"]: a["name"] for a in c.execute("SELECT id,name FROM agents").fetchall()}
    return [_row(r, agents) for r in c.execute("SELECT * FROM photo_exports WHERE photo_id=? ORDER BY created DESC, id DESC", (pid,)).fetchall()]


def get(pid: int, eid: int):
    return db.conn().execute("SELECT * FROM photo_exports WHERE id=? AND photo_id=?", (eid, pid)).fetchone()


def delete(r) -> Optional[Path]:
    """Move the file to the backup folder (never erased) and forget the export. Caller holds db.write_lock."""
    p = path_of(r["path"])
    dest = None
    if p.is_file():
        dest = config.backup_dir / "exports" / ("%s.deleted-%d" % (r["path"].lstrip("/").replace(":", ""), int(time.time())))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
    db.conn().execute("DELETE FROM photo_exports WHERE id=?", (r["id"],))
    return dest
