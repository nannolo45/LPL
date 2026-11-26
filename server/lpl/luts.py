# SPDX-License-Identifier: GPL-3.0-or-later
"""The LUT folder (R19, docs/luts-r19.md; contract: darktable-android PLAN.md 4.6).

darktable's lut 3D module reads its files from a LUT folder (preference plugins/darkroom/lut3d/def_path) and keeps
the file's path relative to that folder in its parameters. LPL carries those files between the PCs and the phone:
one current file per relative path (newest upload wins; a device that sends x-base-sha256 gets a 409 when the file
changed elsewhere), earlier contents kept as history, never deleted by an upload. Removing a path is a web UI
action and removes it from LPL only, never from a device.

Files are stored by content (DATA_DIR/luts/<first two hex digits>/<sha256>), so one content is stored once however
many paths or revisions carry it. A device takes part only when its heartbeat caps contain "luts"; the heartbeat
answer then lists "luts" in server_caps. Nothing here imports api.py: api.py mounts `router`."""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import db
from .config import config

SERVER_CAPS = ["luts"]
EXTENSIONS = (".cube", ".3dl", ".png", ".gmz")
MAX_BYTES = int(os.environ.get("LPL_MAX_LUT_MB", "64")) * 1024 * 1024
MAX_PATH_BYTES = 500
STATES = ("ok", "different", "removed", "conflict", "unusable")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lut_files (          -- the current file of each relative path
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    seq INTEGER NOT NULL,                       -- one counter over all paths: GET /api/luts?since=
    changed_at REAL NOT NULL,
    device_id TEXT,
    device_name TEXT,
    deleted INTEGER NOT NULL DEFAULT 0          -- removed from LPL in the web UI (keeps the last content)
);
CREATE INDEX IF NOT EXISTS idx_lut_files_seq ON lut_files(seq);
CREATE TABLE IF NOT EXISTS lut_history (        -- contents a path had before (kept, never deleted by an upload)
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    changed_at REAL,
    device_id TEXT,
    device_name TEXT,
    replaced_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lut_history_path ON lut_history(path);
CREATE TABLE IF NOT EXISTS lut_devices (        -- each device's last report (PUT /api/luts/state)
    agent_id TEXT PRIMARY KEY,
    folder TEXT,
    reason TEXT,
    files TEXT NOT NULL DEFAULT '{}',
    updated REAL
);
"""

router = APIRouter()


def migrate(c) -> None:
    """New tables; called by db.migrate at every start, changes nothing the second time."""
    c.executescript(SCHEMA)


# ---------------------------------------------------------------- paths and storage
def clean_path(p: Any) -> str:
    """The relative path as the contract allows it, or ValueError: '/' separators, no leading '/', no drive letter,
    no '\\', no empty, '.' or '..' step, no control characters, at most 500 bytes, a LUT extension (any case)."""
    if not isinstance(p, str) or not p:
        raise ValueError("path required")
    if len(p.encode("utf-8")) > MAX_PATH_BYTES:
        raise ValueError("path longer than %d bytes" % MAX_PATH_BYTES)
    if "\\" in p or p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        raise ValueError("the path must be relative, with / separators")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in p):
        raise ValueError("control character in the path")
    parts = p.split("/")
    if any(s in ("", ".", "..") for s in parts):
        raise ValueError("empty, . or .. step in the path")
    if not p.lower().endswith(EXTENSIONS):
        raise ValueError("not a LUT file (.cube, .3dl, .png, .gmz): %s" % p)
    return p


def store_dir() -> Path:
    return config.data_dir / "luts"


def blob_path(sha: str) -> Path:
    return store_dir() / sha[:2] / sha


def _device_name(c, aid: str) -> str:
    a = c.execute("SELECT name FROM agents WHERE id=?", (aid,)).fetchone()
    return (a["name"] if a and a["name"] else aid) or ""


def _next_seq(c) -> int:
    return (c.execute("SELECT COALESCE(MAX(seq),0) FROM lut_files").fetchone()[0] or 0) + 1


def entry(r) -> Dict[str, Any]:
    return {"path": r["path"], "size": r["size"], "sha256": r["sha256"], "seq": r["seq"], "changed_at": r["changed_at"],
            "device_id": r["device_id"], "device_name": r["device_name"], "deleted": bool(r["deleted"])}


def _row(c, path: str):
    return c.execute("SELECT * FROM lut_files WHERE path=?", (path,)).fetchone()


def _agent_id(agent: str) -> str:
    aid = (agent or "")[:80]
    if not aid:
        raise HTTPException(400, "agent required")
    return aid


def _hex(v: Optional[str]) -> Optional[str]:
    """A header value that should be a SHA-256: lowercase; None when absent."""
    if v is None:
        return None
    return v.strip().lower()


# ---------------------------------------------------------------- calls (contract, PLAN.md 4.6)
@router.get("/api/luts")
def luts_list(since: int = 0):
    c = db.conn()
    rows = c.execute("SELECT * FROM lut_files WHERE seq>? ORDER BY seq", (since,)).fetchall()
    seq = c.execute("SELECT COALESCE(MAX(seq),0) FROM lut_files").fetchone()[0] or 0
    return {"seq": seq, "files": [entry(r) for r in rows]}


@router.get("/api/luts/file")
def luts_file(path: str = ""):
    try:
        path = clean_path(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    r = _row(db.conn(), path)
    if not r or r["deleted"] or not blob_path(r["sha256"]).is_file():
        raise HTTPException(404, "no such LUT file")
    return FileResponse(blob_path(r["sha256"]), media_type="application/octet-stream",
                        filename=path.rsplit("/", 1)[-1], headers={"x-sha256": r["sha256"]})


@router.put("/api/luts/file")
async def luts_put(request: Request, path: str = "", agent: str = ""):
    """Body = the file's bytes, streamed to a temporary file (never held in memory); x-sha256 required, checked;
    x-base-sha256 optional (the hash the device last had, "" for none): when given and different from the current
    hash, 409 changed_elsewhere and nothing is stored."""
    aid = _agent_id(agent)
    try:
        path = clean_path(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    want = _hex(request.headers.get("x-sha256"))
    if not want or len(want) != 64 or any(ch not in "0123456789abcdef" for ch in want):
        raise HTTPException(400, "x-sha256 required (lowercase hex SHA-256 of the body)")
    base = _hex(request.headers.get("x-base-sha256"))
    store_dir().mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".lpl-lut-", dir=str(store_dir()))
    total = 0
    h = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                if total == 0 and chunk and path.lower().endswith(".png") and not chunk.startswith(PNG_SIGNATURE[:len(chunk)]):
                    raise HTTPException(400, "the file is not a PNG")
                total += len(chunk)
                if total > MAX_BYTES:
                    raise HTTPException(413, "LUT file larger than %d MB" % (MAX_BYTES // (1024 * 1024)))
                h.update(chunk)
                f.write(chunk)
        if total == 0:
            raise HTTPException(400, "empty upload")
        got = h.hexdigest()
        if got != want:
            return JSONResponse({"error": "hash_mismatch", "sha256": got}, status_code=400)
        with db.write_lock:
            c = db.conn()
            r = _row(c, path)
            if r and not r["deleted"] and r["sha256"] == got:
                return {"path": path, "sha256": got, "seq": r["seq"], "stored": False}
            if r and not r["deleted"] and base is not None and base != r["sha256"]:
                return JSONResponse({"error": "changed_elsewhere", "file": entry(r)}, status_code=409)
            dest = blob_path(got)
            if not dest.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(tmp, 0o644)
                os.replace(tmp, dest)
            now = time.time()
            seq = _next_seq(c)
            dev = _device_name(c, aid)
            if r:
                if r["sha256"] != got:  # the content it had stays as history
                    c.execute("INSERT INTO lut_history(path,sha256,size,changed_at,device_id,device_name,replaced_at) "
                              "VALUES(?,?,?,?,?,?,?)", (path, r["sha256"], r["size"], r["changed_at"], r["device_id"],
                                                        r["device_name"], now))
                c.execute("UPDATE lut_files SET sha256=?, size=?, seq=?, changed_at=?, device_id=?, device_name=?, deleted=0 "
                          "WHERE path=?", (got, total, seq, now, aid, dev, path))
            else:
                c.execute("INSERT INTO lut_files(path,sha256,size,seq,changed_at,device_id,device_name,deleted) "
                          "VALUES(?,?,?,?,?,?,?,0)", (path, got, total, seq, now, aid, dev))
            c.commit()
        return {"path": path, "sha256": got, "seq": seq, "stored": True}
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@router.delete("/api/luts/file")
def luts_delete(path: str = ""):
    """Web UI only: the path is removed from LPL (its content stays as history); no device deletes its copy."""
    try:
        path = clean_path(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db.write_lock:
        c = db.conn()
        r = _row(c, path)
        if not r:
            raise HTTPException(404, "no such LUT file")
        if not r["deleted"]:
            seq = _next_seq(c)
            c.execute("UPDATE lut_files SET deleted=1, seq=?, changed_at=?, device_id=NULL, device_name=NULL WHERE path=?",
                      (seq, time.time(), path))
            c.commit()
        return entry(_row(c, path))


@router.put("/api/luts/state")
def luts_state(agent: str = "", body: Dict[str, Any] = Body(...)):
    """The device's whole list (replace): folder (or null and a reason) and, per path, sha256 and state."""
    aid = _agent_id(agent)
    folder, reason = body.get("folder"), body.get("reason")
    folder = folder[:500] if isinstance(folder, str) and folder else None
    reason = reason[:300] if isinstance(reason, str) and reason else None
    files_in = body.get("files")
    if files_in is None:
        files_in = {}
    if not isinstance(files_in, dict):
        raise HTTPException(400, "files must be an object")
    files: Dict[str, Dict[str, Any]] = {}
    for p, v in list(files_in.items())[:20000]:
        try:
            p = clean_path(p)
        except ValueError:
            continue  # a path the contract does not allow is left out, not an error for the whole list
        if not isinstance(v, dict):
            continue
        sha = v.get("sha256")
        st = v.get("state") if v.get("state") in STATES else "ok"
        files[p] = {"sha256": sha.lower() if isinstance(sha, str) else None, "state": st}
    with db.write_lock:
        c = db.conn()
        c.execute("INSERT INTO lut_devices(agent_id,folder,reason,files,updated) VALUES(?,?,?,?,?) ON CONFLICT(agent_id) "
                  "DO UPDATE SET folder=excluded.folder, reason=excluded.reason, files=excluded.files, updated=excluded.updated",
                  (aid, folder, reason, json.dumps(files, ensure_ascii=False), time.time()))
        c.commit()
    return {"ok": True, "files": len(files)}


def _stored_bytes() -> int:
    total = 0
    d = store_dir()
    if d.is_dir():
        for sub in d.iterdir():
            if sub.is_dir():
                for f in sub.iterdir():
                    if f.is_file():
                        total += f.stat().st_size
    return total


@router.get("/api/luts/overview")
def luts_overview():
    """For the web UI: every path with its devices, every device that reported, the bytes stored."""
    from . import transfers
    c = db.conn()
    agents = {a["id"]: a for a in c.execute("SELECT * FROM agents").fetchall()}
    reports = c.execute("SELECT * FROM lut_devices").fetchall()
    devices, have = [], {}
    for d in reports:
        a = agents.get(d["agent_id"])
        if a is None or "luts" not in transfers.caps_of(c, d["agent_id"]):
            continue  # a forgotten device, or one that no longer takes part
        try:
            files = json.loads(d["files"] or "{}")
        except ValueError:
            files = {}
        devices.append({"agent_id": d["agent_id"], "name": a["name"] or d["agent_id"], "os": a["os"], "folder": d["folder"],
                        "reason": d["reason"], "files": len(files), "updated": d["updated"], "last_seen": a["last_seen"]})
        for p, v in files.items():
            have.setdefault(p, []).append({"agent_id": d["agent_id"], "name": a["name"] or d["agent_id"], "os": a["os"],
                                           "sha256": v.get("sha256"), "state": v.get("state") or "ok"})
    hist = {r[0]: r[1] for r in c.execute("SELECT path, COUNT(*) FROM lut_history GROUP BY path")}
    files = []
    for r in c.execute("SELECT * FROM lut_files ORDER BY path COLLATE NOCASE").fetchall():
        files.append(dict(entry(r), history=hist.get(r["path"], 0),
                          devices=sorted(have.get(r["path"], []), key=lambda x: (x["name"] or "").casefold())))
    current = c.execute("SELECT COALESCE(SUM(size),0) FROM lut_files WHERE deleted=0").fetchone()[0] or 0
    return {"files": files, "devices": sorted(devices, key=lambda x: (x["name"] or "").casefold()),
            "bytes_current": current, "bytes_stored": _stored_bytes(), "max_file_bytes": MAX_BYTES}
