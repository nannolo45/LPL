"""HTTP API + static web UI."""
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, edits, media, query, scanner, xmp
from .config import config

log = logging.getLogger("lpl.api")
app = FastAPI(title="LightPhotoLibrary", version="0.1.0")
AGENT_ONLINE_SECONDS = 15
JOB_MAX_HANDOUTS = 3  # a job that was never reported done is handed out at most this often (see config.job_retry_seconds)


# ---------------------------------------------------------------- auth
def _token() -> str:
    salt = db.get_setting("auth_salt")
    if not salt:
        salt = secrets.token_hex(16)
        db.set_setting("auth_salt", salt)
    return hashlib.sha256((salt + config.password).encode()).hexdigest()


@app.middleware("http")
async def auth_mw(request: Request, call_next):
    path = request.url.path
    if config.password and path.startswith("/api/") and path not in ("/api/login", "/api/auth", "/api/auth/token"):
        tok = request.cookies.get("lpl_token") or request.headers.get("x-lpl-token")
        if tok not in (_token(), config.password):
            return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)


@app.get("/api/auth")
def auth_status(request: Request):
    if not config.password:
        return {"required": False, "ok": True}
    return {"required": True, "ok": request.cookies.get("lpl_token") == _token()}


@app.get("/api/auth/token")
def auth_token(request: Request):
    """Is the x-lpl-token header right? Only the header counts (not the login cookie), so an app can check a
    password before it uses it. 200 {"ok": true, "required": ...} or 401 like every other call."""
    if not config.password:
        return {"required": False, "ok": True}
    tok = request.headers.get("x-lpl-token") or ""
    if any(secrets.compare_digest(tok.encode(), t.encode()) for t in (_token(), config.password)):
        return {"required": True, "ok": True}
    return JSONResponse({"detail": "login required"}, status_code=401)


@app.post("/api/login")
def login(body: Dict[str, Any] = Body(...)):
    if not config.password or body.get("password") == config.password:
        r = JSONResponse({"ok": True})
        if config.password:
            r.set_cookie("lpl_token", _token(), max_age=3600 * 24 * 365, httponly=True, samesite="lax")
        return r
    raise HTTPException(401, "wrong password")


# ---------------------------------------------------------------- helpers
def photo_row(pid: int):
    r = db.conn().execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "photo not found")
    return r


def photo_dicts(ids: List[int]) -> List[dict]:
    if not ids:
        return []
    where, args, join = query.build_where({"ids": ids})
    rows = db.conn().execute("SELECT %s FROM photos p%s WHERE %s" % (query.SELECT_COLS, join, where), args).fetchall()
    by = {r["id"]: query.row_to_photo(r) for r in rows}
    return [by[i] for i in ids if i in by]


def category_tags() -> List[str]:
    return [r["path"] for r in db.conn().execute("SELECT path FROM tags WHERE category=1").fetchall()]


def write_xmp(pid: int, cats: Optional[List[str]] = None) -> Optional[str]:
    """Write the DB state (rating, labels, tags) of one photo into its sidecar. Returns an error message or None."""
    r = photo_row(pid)
    try:
        img = scanner.abs_of(r["relpath"])
        sc = xmp.sidecar_path(img)
        data = sc.read_bytes() if sc.is_file() else None
        tags = [t for t in db.photo_tags(pid) if not t.startswith("darktable|")]
        new = xmp.apply_meta(data, rating=r["rating"], labels=db.photo_labels(pid), tags=tags,
                             category_tags=cats if cats is not None else category_tags())
        xmp.atomic_write(sc, new, config.backup_dir, r["relpath"])
        db.conn().execute("UPDATE photos SET xmp_mtime=? WHERE id=?", (sc.stat().st_mtime, pid))
        return None
    except Exception as e:
        log.warning("XMP write failed for %s: %s", r["relpath"], e)
        return "%s: %s" % (r["relpath"], e)


def write_xmps(ids: List[int]) -> List[str]:
    cats = category_tags()
    errs = []
    for pid in ids:
        e = write_xmp(pid, cats)
        if e:
            errs.append(e)
    return errs


# ---------------------------------------------------------------- status / scan
@app.get("/api/status")
def status():
    c = db.conn()
    n = c.execute("SELECT COUNT(*) n FROM photos WHERE missing=0").fetchone()["n"]
    agents = list_agents()
    return {"photos": n, "scan": scanner.status, "agents": agents, "server_time": time.time(),
            "photo_root": str(config.photo_root), "version": app.version, "mode": config.mode,
            "nas_url": config.nas_url if config.local else None}


@app.post("/api/scan")
def start_scan(body: Dict[str, Any] = Body(default={})):
    import threading
    threading.Thread(target=scanner.scan, kwargs={"full": bool(body.get("full"))}, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------- photos
@app.post("/api/photos/query")
def photos_query(body: Dict[str, Any] = Body(...)):
    filters = body.get("filters") or {}
    where, args, join = query.build_where(filters)
    order = query.build_order(body.get("sort") or [], body.get("seed") or 1, bool(filters.get("series") or filters.get("series_folder")))
    limit = max(1, min(int(body.get("limit") or 200), 2000))
    offset = max(0, int(body.get("offset") or 0))
    c = db.conn()
    total = c.execute("SELECT COUNT(*) n FROM photos p%s WHERE %s" % (join, where), args).fetchone()["n"]
    rows = c.execute("SELECT %s FROM photos p%s WHERE %s ORDER BY %s LIMIT ? OFFSET ?" % (query.SELECT_COLS, join, where, order),
                     args + [limit, offset]).fetchall()
    return {"total": total, "offset": offset, "items": [query.row_to_photo(r) for r in rows]}


@app.post("/api/photos/ids")
def photos_ids(body: Dict[str, Any] = Body(...)):
    """All ids matching filters (for 'select all')."""
    filters = body.get("filters") or {}
    where, args, join = query.build_where(filters)
    order = query.build_order(body.get("sort") or [], body.get("seed") or 1, bool(filters.get("series") or filters.get("series_folder")))
    rows = db.conn().execute("SELECT p.id FROM photos p%s WHERE %s ORDER BY %s LIMIT 20000" % (join, where, order), args).fetchall()
    return {"ids": [r["id"] for r in rows]}


@app.post("/api/photos/position")
def photos_position(body: Dict[str, Any] = Body(...)):
    """Where a photo sits in a query's order (0-based), or -1 when it doesn't match."""
    filters = body.get("filters") or {}
    pid = int(body.get("id"))
    where, args, join = query.build_where(filters)
    order = query.build_order(body.get("sort") or [], body.get("seed") or 1, bool(filters.get("series") or filters.get("series_folder")))
    r = db.conn().execute(
        "SELECT n FROM (SELECT p.id, ROW_NUMBER() OVER (ORDER BY %s) - 1 AS n FROM photos p%s WHERE %s) WHERE id=?" % (order, join, where),
        args + [pid]).fetchone()
    return {"index": r["n"] if r else -1}


@app.post("/api/photos/get")
def photos_get(body: Dict[str, Any] = Body(...)):
    return {"items": photo_dicts([int(i) for i in body.get("ids") or []][:2000])}


@app.get("/api/photos/{pid}")
def photo_detail(pid: int):
    items = photo_dicts([pid])
    if not items:
        raise HTTPException(404)
    d = items[0]
    d["series"] = series_mod.photo_series(pid)
    d["last_edit"] = edits.last(pid)
    return d


@app.get("/api/photos/{pid}/edits")
def photo_edits(pid: int, limit: int = 200):
    """Who edited the photo and what changed, newest first (see edits.py)."""
    photo_row(pid)
    return {"edits": edits.events(pid, max(1, min(limit, 1000)))}


@app.get("/api/preview/{pid}")
def preview(pid: int, s: str = "t"):
    t, l = media.preview_paths(pid)
    p = l if s == "l" else t
    if not p.is_file():
        if s == "l" and t.is_file():
            p = t
        else:
            scanner.prioritize([pid])  # someone wants to see it: make it next
            raise HTTPException(404, "preview not ready")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/api/meta")
def set_meta(body: Dict[str, Any] = Body(...)):
    """Change rating / labels / tags of many photos at once and write their XMP files.

    body: ids, rating, add_labels, remove_labels, toggle_label, attach, detach, title, description
    """
    ids = [int(i) for i in body.get("ids") or []]
    if not ids:
        return {"items": [], "errors": []}
    with db.write_lock:
        c = db.conn()
        for pid in ids:
            labels = set(db.photo_labels(pid))
            tags = set(db.photo_tags(pid))
            if "toggle_label" in body:
                lab = int(body["toggle_label"])
                all_have = all(lab in db.photo_labels(i) for i in ids)
                labels.discard(lab) if all_have else labels.add(lab)
            for lab in body.get("add_labels") or []:
                labels.add(int(lab))
            for lab in body.get("remove_labels") or []:
                labels.discard(int(lab))
            for t in body.get("attach") or []:
                t = t.strip().strip("|")
                if t:
                    tags.add(t)
            for t in body.get("detach") or []:
                tags.discard(t)
            db.set_photo_meta(pid, rating=body.get("rating"), labels=sorted(labels), tags=sorted(tags),
                              title=body.get("title"), description=body.get("description"))
        c.commit()
        errors = write_xmps(ids)
        c.commit()
    return {"items": photo_dicts(ids), "errors": errors}


# ---------------------------------------------------------------- facets, folders, timeline
@app.get("/api/facets")
def facets():
    c = db.conn()
    lenses = [dict(r) for r in c.execute("SELECT lens AS v, COUNT(*) n FROM photos WHERE missing=0 AND lens IS NOT NULL AND lens<>'' GROUP BY lens ORDER BY lens").fetchall()]
    cams = [dict(r) for r in c.execute("SELECT camera AS v, COUNT(*) n FROM photos WHERE missing=0 AND camera IS NOT NULL AND camera<>'' GROUP BY camera ORDER BY camera").fetchall()]
    hidden = c.execute("SELECT COUNT(*) n FROM photos WHERE missing=0 AND hidden=1").fetchone()["n"]
    return {"lenses": lenses, "cameras": cams, "hidden_photos": hidden}


@app.post("/api/photos/hide")
def photos_hide(body: Dict[str, Any] = Body(...)):
    """Hide photos from the library views, or show them again. Only in LPL: nothing is written to the files."""
    ids = [int(i) for i in body.get("ids") or []][:20000]
    flag = 1 if body.get("hidden", True) else 0
    with db.write_lock:
        c = db.conn()
        for k in range(0, len(ids), 900):
            part = ids[k:k + 900]
            c.execute("UPDATE photos SET hidden=? WHERE id IN (%s)" % ",".join("?" * len(part)), [flag] + part)
        c.commit()
    n = db.conn().execute("SELECT COUNT(*) n FROM photos WHERE missing=0 AND hidden=1").fetchone()["n"]
    return {"changed": len(ids), "hidden_photos": n}


@app.get("/api/folders")
def folders():
    c = db.conn()
    rows = c.execute(
        "SELECT folder, COUNT(*) n, SUM(edited) ed, MIN(taken) first, MAX(taken) last "
        "FROM photos WHERE missing=0 GROUP BY folder ORDER BY MAX(taken) DESC").fetchall()
    # covers: photos whose preview is ready first, then the best rated, then the newest
    covers = {}
    for r in c.execute(
            "SELECT folder, id, preview_ver, preview_state FROM (SELECT folder, id, preview_ver, preview_state, ROW_NUMBER() OVER "
            "(PARTITION BY folder ORDER BY preview_state=1 DESC, rating DESC, taken DESC) rn FROM photos WHERE missing=0) WHERE rn<=4").fetchall():
        covers.setdefault(r["folder"], []).append({"id": r["id"], "v": r["preview_ver"], "ready": r["preview_state"] == 1})
    agg: Dict[str, dict] = {}
    for r in rows:  # newest subfolders first, so a year folder shows recent photos
        f = r["folder"]
        parts = f.split("/") if f else []
        for k in range(0, len(parts) + 1):
            p = "/".join(parts[:k])
            a = agg.setdefault(p, {"path": p, "count": 0, "edited": 0, "first": None, "last": None, "covers": [], "direct": 0})
            a["count"] += r["n"]
            a["edited"] += r["ed"] or 0
            if r["first"] and (a["first"] is None or r["first"] < a["first"]):
                a["first"] = r["first"]
            if r["last"] and (a["last"] is None or r["last"] > a["last"]):
                a["last"] = r["last"]
            if k == len(parts):
                a["direct"] = r["n"]
            a.setdefault("_cand", []).extend(covers.get(f, []))
    pending = []
    for a in agg.values():
        cand = a.pop("_cand")
        ready = [x for x in cand if x["ready"]]
        pick = (ready + [x for x in cand if not x["ready"]])[:4]
        a["covers"] = [{"id": x["id"], "v": x["v"]} if x["ready"] else {"id": x["id"], "v": x["v"], "pending": True} for x in pick]
        pending += [x["id"] for x in pick if not x["ready"]]
    scanner.prioritize(pending)
    pos = {r["path"]: r["pos"] for r in c.execute("SELECT path, pos FROM folder_order").fetchall()}
    hidden = set(scanner.hidden_folders())
    for p, a in agg.items():
        a["pos"] = pos.get(p)
        a["hidden"] = p in hidden
    return {"folders": sorted(agg.values(), key=lambda a: a["path"]), "excluded": scanner.excluded_folders(),
            "previews_pending": scanner.status.get("previews_pending", 0)}


@app.post("/api/folders/hide")
def folders_hide(body: Dict[str, Any] = Body(...)):
    """Hide a folder (and everything in it) from the views, or show it again. Files and data are untouched."""
    path = (body.get("path") or "").strip("/")
    if not path:
        raise HTTPException(400, "choose a folder")
    with db.write_lock:
        cur = [h for h in scanner.hidden_folders() if h != path]
        if body.get("hidden", True):
            cur.append(path)
        db.set_setting("hidden_folders", json.dumps(sorted(set(cur))))
        db.conn().commit()
    return {"hidden": scanner.hidden_folders()}


@app.post("/api/folders/exclude")
def folders_exclude(body: Dict[str, Any] = Body(...)):
    """Leave a folder out of the library (not scanned, no previews, gone from LPL), or take it back in.
    The files and their XMPs on disk are never touched."""
    path = (body.get("path") or "").strip("/")
    if not path:
        raise HTTPException(400, "choose a folder")
    exclude = bool(body.get("excluded", True))
    removed = 0
    with db.write_lock:
        c = db.conn()
        cur = [e for e in scanner.excluded_folders() if e != path]
        if exclude:
            cur.append(path)
        db.set_setting("excluded_folders", json.dumps(sorted(set(cur))))
        if exclude:
            like = path.replace("%", "\\%").replace("_", "\\_") + "/%"
            ids = [r["id"] for r in c.execute("SELECT id FROM photos WHERE folder=? OR folder LIKE ? ESCAPE '\\'", (path, like)).fetchall()]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                qs = ",".join("?" * len(chunk))
                for t in ("photo_labels", "photo_tags", "series_items", "folder_items", "sent"):
                    c.execute("DELETE FROM %s WHERE photo_id IN (%s)" % (t, qs), chunk)
                c.execute("DELETE FROM photos WHERE id IN (%s)" % qs, chunk)
            removed = len(ids)
            c.execute("DELETE FROM folder_order WHERE path=? OR path LIKE ? ESCAPE '\\'", (path, like))
        c.commit()
    if exclude:
        threading.Thread(target=_drop_previews, args=(ids,), daemon=True).start()
    else:
        threading.Thread(target=scanner.scan, daemon=True).start()
    return {"excluded": scanner.excluded_folders(), "removed": removed}


def _drop_previews(ids) -> None:
    for pid in ids:
        for p in media.preview_paths(pid):
            try:
                p.unlink()
            except OSError:
                pass


@app.post("/api/folders/order")
def folder_order(body: Dict[str, Any] = Body(...)):
    with db.write_lock:
        c = db.conn()
        for i, p in enumerate(body.get("order") or []):
            c.execute("INSERT INTO folder_order(path,pos) VALUES(?,?) ON CONFLICT(path) DO UPDATE SET pos=excluded.pos", (p, i))
        c.commit()
    return {"ok": True}


@app.post("/api/timeline")
def timeline(body: Dict[str, Any] = Body(default={})):
    """Months with their photo count, folders and best photos. Two passes over the photos in total (it used to be
    one full pass per month, seconds on a big library)."""
    f = dict(body.get("filters") or {})
    f.pop("month", None)
    per = max(1, min(int(body.get("covers") or 30), 60))
    where, args, join = query.build_where(f)
    c = db.conn()
    rows = c.execute("SELECT substr(p.taken,1,7) m, COUNT(*) n, group_concat(DISTINCT p.folder) folders FROM photos p%s WHERE %s AND p.taken IS NOT NULL "
                     "GROUP BY m ORDER BY m DESC" % (join, where), args).fetchall()
    covers: Dict[str, list] = {}
    for x in c.execute(
            "SELECT m, id, v FROM (SELECT substr(p.taken,1,7) m, p.id id, p.preview_ver v, "
            "ROW_NUMBER() OVER (PARTITION BY substr(p.taken,1,7) ORDER BY p.rating DESC, p.taken) k "
            "FROM photos p%s WHERE %s AND p.taken IS NOT NULL) WHERE k <= ? ORDER BY m, k" % (join, where), args + [per]).fetchall():
        covers.setdefault(x["m"], []).append({"id": x["id"], "v": x["v"]})
    out = []
    for r in rows:
        paths = sorted({x for x in (r["folders"] or "").split(",") if x}, key=lambda x: x.split("/")[-1].lower())
        out.append({"month": r["m"], "count": r["n"], "folders": [x.split("/")[-1] for x in paths], "paths": paths,
                    "covers": covers.get(r["m"], [])})
    return {"months": out}


# ---------------------------------------------------------------- tags
@app.get("/api/tags")
def tags_list():
    rows = db.conn().execute(
        "SELECT t.path, t.synonyms, t.category, t.private, COUNT(pt.photo_id) n FROM tags t "
        "LEFT JOIN photo_tags pt ON pt.tag_id=t.id LEFT JOIN photos p ON p.id=pt.photo_id AND p.missing=0 "
        "WHERE t.path NOT LIKE 'darktable|%' GROUP BY t.id ORDER BY t.path").fetchall()
    return {"tags": [dict(r) for r in rows]}


@app.post("/api/tags/create")
def tag_create(body: Dict[str, Any] = Body(...)):
    path = (body.get("path") or "").strip().strip("|")
    if not path:
        raise HTTPException(400, "empty tag")
    with db.write_lock:
        db.tag_id(path)
        db.conn().commit()
    return {"ok": True}


@app.post("/api/tags/props")
def tag_props(body: Dict[str, Any] = Body(...)):
    path = body.get("path")
    with db.write_lock:
        c = db.conn()
        tid = db.tag_id(path)
        for k in ("synonyms", "category", "private"):
            if k in body:
                v = body[k]
                c.execute("UPDATE tags SET %s=? WHERE id=?" % k, (v if k == "synonyms" else int(bool(v)), tid))
        c.commit()
        if "category" in body:
            ids = [r["photo_id"] for r in c.execute("SELECT photo_id FROM photo_tags WHERE tag_id=?", (tid,)).fetchall()]
            errs = write_xmps(ids)
            c.commit()
            return {"ok": True, "errors": errs}
    return {"ok": True}


def _affected(path: str):
    """The tag itself and every tag below it."""
    return db.conn().execute(
        "SELECT t.id, t.path FROM tags t WHERE t.path=? OR t.path LIKE ? ESCAPE '\\'",
        (path, path.replace("%", "\\%").replace("_", "\\_") + "|%")).fetchall()


@app.post("/api/tags/rename")
def tag_rename(body: Dict[str, Any] = Body(...)):
    """Rename or move a tag (and everything below it). 'places|Lisboa' -> 'places|Portugal|Lisbon'."""
    old = (body.get("old") or "").strip().strip("|")
    new = (body.get("new") or "").strip().strip("|")
    if not old or not new or old == new:
        raise HTTPException(400, "nothing to rename")
    with db.write_lock:
        c = db.conn()
        rows = _affected(old)
        ids = set()
        for r in rows:
            target = new + r["path"][len(old):]
            existing = c.execute("SELECT id FROM tags WHERE path=?", (target,)).fetchone()
            pids = [x["photo_id"] for x in c.execute("SELECT photo_id FROM photo_tags WHERE tag_id=?", (r["id"],)).fetchall()]
            ids.update(pids)
            if existing:
                for pid in pids:
                    c.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id) VALUES(?,?)", (pid, existing["id"]))
                c.execute("DELETE FROM photo_tags WHERE tag_id=?", (r["id"],))
                c.execute("DELETE FROM tags WHERE id=?", (r["id"],))
            else:
                c.execute("UPDATE tags SET path=? WHERE id=?", (target, r["id"]))
        for pid in ids:
            c.execute("UPDATE photos SET meta_mtime=? WHERE id=?", (time.time(), pid))
        c.commit()
        errs = write_xmps(sorted(ids))
        c.commit()
    return {"ok": True, "photos": len(ids), "errors": errs}


@app.post("/api/tags/delete")
def tag_delete(body: Dict[str, Any] = Body(...)):
    path = (body.get("path") or "").strip().strip("|")
    with db.write_lock:
        c = db.conn()
        rows = _affected(path)
        ids = set()
        for r in rows:
            ids.update(x["photo_id"] for x in c.execute("SELECT photo_id FROM photo_tags WHERE tag_id=?", (r["id"],)).fetchall())
            c.execute("DELETE FROM photo_tags WHERE tag_id=?", (r["id"],))
            c.execute("DELETE FROM tags WHERE id=?", (r["id"],))
        for pid in ids:
            c.execute("UPDATE photos SET meta_mtime=? WHERE id=?", (time.time(), pid))
        c.commit()
        errs = write_xmps(sorted(ids))
        c.commit()
    return {"ok": True, "photos": len(ids), "errors": errs}


@app.post("/api/tags/suggest")
def tag_suggest(body: Dict[str, Any] = Body(...)):
    ids = [int(i) for i in body.get("ids") or []][:500]
    if not ids:
        return {"tags": []}
    c = db.conn()
    marks = ",".join(str(i) for i in ids)
    folders = [r["folder"] for r in c.execute("SELECT DISTINCT folder FROM photos WHERE id IN (%s)" % marks).fetchall()]
    fm = ",".join("?" * len(folders))
    rows = c.execute(
        "SELECT t.path, COUNT(*) n FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id JOIN photos p ON p.id=pt.photo_id "
        "WHERE p.folder IN (%s) AND t.path NOT LIKE 'darktable|%%' GROUP BY t.path ORDER BY n DESC LIMIT 12" % fm, folders).fetchall()
    have = None
    for pid in ids:
        s = set(db.photo_tags(pid))
        have = s if have is None else have & s
    return {"tags": [r["path"] for r in rows if r["path"] not in (have or set())][:6]}


# ---------------------------------------------------------------- user preferences kept on the server (shared by every PC)
PREF_KEYS = {"shortcuts", "settings"}


@app.get("/api/prefs/{key}")
def pref_get(key: str):
    if key not in PREF_KEYS:
        raise HTTPException(404, "unknown preference")
    try:
        return {"value": json.loads(db.get_setting("pref." + key) or "null")}
    except ValueError:
        return {"value": None}


@app.put("/api/prefs/{key}")
def pref_set(key: str, body: Dict[str, Any] = Body(...)):
    if key not in PREF_KEYS:
        raise HTTPException(404, "unknown preference")
    data = json.dumps(body.get("value"))
    if len(data) > 100_000:
        raise HTTPException(413, "too large")
    db.set_setting("pref." + key, data)
    return {"ok": True}


# ---------------------------------------------------------------- series + board
SERIES_TAG = "series|"


def _series(sid: int):
    r = db.conn().execute("SELECT * FROM series WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, "series not found")
    return r


@app.get("/api/series")
def series_list():
    c = db.conn()
    out = []
    for s in c.execute("SELECT * FROM series ORDER BY name COLLATE NOCASE").fetchall():
        items = c.execute("SELECT si.photo_id id, p.preview_ver v FROM series_items si JOIN photos p ON p.id=si.photo_id "
                          "WHERE si.series_id=? ORDER BY si.ord NULLS LAST LIMIT 8", (s["id"],)).fetchall()
        n = c.execute("SELECT COUNT(*) n FROM series_items WHERE series_id=?", (s["id"],)).fetchone()["n"]
        out.append({"id": s["id"], "name": s["name"], "count": n, "covers": [dict(i) for i in items], **series_mod.list_extra(s)})
    return {"series": out, "folders": series_mod.folders_list()}


@app.post("/api/series")
def series_create(body: Dict[str, Any] = Body(...)):
    """name, ids (photos to start with), folder_id (a series folder to put it in)."""
    return series_mod.create_series(body)


@app.patch("/api/series/{sid}")
def series_rename(sid: int, body: Dict[str, Any] = Body(...)):
    return series_mod.patch_series(sid, body)


@app.delete("/api/series/{sid}")
def series_delete(sid: int):
    s = _series(sid)
    ids = [r["photo_id"] for r in db.conn().execute("SELECT photo_id FROM series_items WHERE series_id=?", (sid,)).fetchall()]
    if ids and config.write_series_tags:
        set_meta({"ids": ids, "detach": [series_mod.tag_of(s)]})
    with db.write_lock:
        c = db.conn()
        series_mod.on_delete(c, s)
        c.execute("DELETE FROM series_items WHERE series_id=?", (sid,))
        c.execute("DELETE FROM series_frames WHERE series_id=?", (sid,))
        c.execute("DELETE FROM series WHERE id=?", (sid,))
        c.execute("DELETE FROM settings WHERE key=?", (_add_criteria_key(sid),))
        c.execute("DELETE FROM series_shapes WHERE series_id=?", (sid,))
        c.execute("DELETE FROM tags WHERE path=? AND id NOT IN (SELECT tag_id FROM photo_tags)", (series_mod.tag_of(s),))
        c.commit()
    return {"ok": True}


@app.get("/api/series/{sid}/board")
def series_board(sid: int):
    s = _series(sid)
    c = db.conn()
    frames = [dict(r) for r in c.execute("SELECT id,label,x,y,w,h,maybe,color,border FROM series_frames WHERE series_id=? ORDER BY id", (sid,)).fetchall()]
    pos = {r["photo_id"]: r for r in c.execute("SELECT photo_id,x,y,ord FROM series_items WHERE series_id=?", (sid,)).fetchall()}
    items = photo_dicts(list(pos.keys()))
    for it in items:
        r = pos[it["id"]]
        it["x"], it["y"], it["ord"] = r["x"], r["y"], r["ord"]
    try:
        last_add = json.loads(db.get_setting(_add_criteria_key(sid)) or "null")
    except ValueError:
        last_add = None
    r = c.execute("SELECT data FROM series_shapes WHERE series_id=?", (sid,)).fetchone()
    try:
        shapes = json.loads(r["data"]) if r else []
    except ValueError:
        shapes = []
    return {"series": {"id": s["id"], "name": s["name"]}, "frames": frames, "items": items, "last_add": last_add, "shapes": shapes,
            **series_mod.board_extra(s, items)}


@app.put("/api/series/{sid}/shapes")
def series_shapes_save(sid: int, body: Dict[str, Any] = Body(...)):
    """Notes and arrows on the board, saved as a whole list."""
    _series(sid)
    shapes = body.get("shapes")
    if not isinstance(shapes, list):
        raise HTTPException(400, "shapes must be a list")
    data = json.dumps(shapes[:5000], ensure_ascii=False)
    if len(data) > 5_000_000:
        raise HTTPException(413, "too much on this board")
    with db.write_lock:
        c = db.conn()
        c.execute("INSERT INTO series_shapes(series_id,data) VALUES(?,?) ON CONFLICT(series_id) DO UPDATE SET data=excluded.data", (sid, data))
        c.commit()
    return {"ok": True, "n": len(shapes)}


@app.post("/api/series/{sid}/items")
def series_add(sid: int, body: Dict[str, Any] = Body(...)):
    s = _series(sid)
    ids = [int(i) for i in body.get("ids") or []]
    with db.write_lock:
        c = db.conn()
        existing = {r["photo_id"] for r in c.execute("SELECT photo_id FROM series_items WHERE series_id=?", (sid,)).fetchall()}
        maxy = c.execute("SELECT MAX(y) m FROM series_items WHERE series_id=?", (sid,)).fetchone()["m"]
        fr = c.execute("SELECT MAX(y+h) m FROM series_frames WHERE series_id=?", (sid,)).fetchone()["m"]
        base_y = max((maxy or 0) + 150, (fr or 0) + 40)
        new = [i for i in ids if i not in existing]
        for k, pid in enumerate(new):
            c.execute("INSERT INTO series_items(series_id,photo_id,x,y,ord) VALUES(?,?,?,?,NULL)",
                      (sid, pid, 40 + (k % 8) * 165, base_y + (k // 8) * 140))
        c.commit()
    moved = series_mod.moved_count(sid, new)
    series_mod.after_add(sid, new)  # their place in the order; in a folder: out of its other series, onto its board
    if new and config.write_series_tags:
        set_meta({"ids": new, "attach": [series_mod.tag_of(s)]})
    return {"added": len(new), "moved": moved}


@app.post("/api/series/{sid}/remove")
def series_remove(sid: int, body: Dict[str, Any] = Body(...)):
    s = _series(sid)
    ids = [int(i) for i in body.get("ids") or []]
    with db.write_lock:
        c = db.conn()
        for pid in ids:
            c.execute("DELETE FROM series_items WHERE series_id=? AND photo_id=?", (sid, pid))
        c.commit()
    series_mod.after_remove(sid, ids)
    if ids and config.write_series_tags:
        set_meta({"ids": ids, "detach": [series_mod.tag_of(s)]})
    return {"ok": True}


@app.put("/api/series/{sid}/board")
def series_save(sid: int, body: Dict[str, Any] = Body(...)):
    _series(sid)
    with db.write_lock:
        c = db.conn()
        for it in body.get("items") or []:
            # the order is not taken from the client (an "ord" it sends is ignored): while the board is linked the
            # server reads it from the places, otherwise the strip keeps it (series.py)
            c.execute("UPDATE series_items SET x=?, y=? WHERE series_id=? AND photo_id=?",
                      (float(it.get("x", 0)), float(it.get("y", 0)), sid, int(it["id"])))
        if "frames" in body:
            keep = []
            for f in body["frames"]:
                vals = (f.get("label") or "", float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"]), int(bool(f.get("maybe"))),
                        str(f.get("color") or "")[:20], str(f.get("border") or "")[:10])
                cur = None
                if f.get("id"):
                    cur = c.execute("UPDATE series_frames SET label=?,x=?,y=?,w=?,h=?,maybe=?,color=?,border=? WHERE id=? AND series_id=?", vals + (int(f["id"]), sid))
                if cur is not None and cur.rowcount:
                    keep.append(int(f["id"]))
                else:  # new, or brought back by undo after it was deleted
                    cur = c.execute("INSERT INTO series_frames(label,x,y,w,h,maybe,color,border,series_id) VALUES(?,?,?,?,?,?,?,?,?)", vals + (sid,))
                    keep.append(cur.lastrowid)
            if keep:
                c.execute("DELETE FROM series_frames WHERE series_id=? AND id NOT IN (%s)" % ",".join(str(k) for k in keep), (sid,))
            else:
                c.execute("DELETE FROM series_frames WHERE series_id=?", (sid,))
        c.commit()
    series_mod.after_board_save(sid)
    return series_board(sid)


# ------------------------------------------------ add to a board by folders / criteria
ADD_LIMIT = 3000
CELL_W, CELL_H = 160, 115  # board grid cell; tiles are at most 150 x 105 (same sizes as the web board)


def _tile(w, h, o):
    w, h = (w or 3), (h or 2)
    if o in (5, 6, 7, 8):
        w, h = h, w
    r = w / h
    return (150, round(150 / r)) if r >= 1 else (round(105 * r), 105)


def _add_criteria_key(sid: int) -> str:
    return "series_add.%d" % sid


def _match(sid: int, filters: Dict[str, Any], sort: List[Dict[str, str]]):
    f = dict(filters or {})
    for k in ("series", "ids", "not_in_series"):
        f.pop(k, None)
    if not any(f.get(k) for k in ("folders", "folder", "ratings", "labels", "tags", "tag", "lenses", "cameras", "date_from", "date_to", "q", "edited", "month")):
        raise HTTPException(400, "pick at least one folder or criterion")
    c = db.conn()
    where, args, join = query.build_where(f)
    total = c.execute("SELECT COUNT(*) n FROM photos p%s WHERE %s" % (join, where), args).fetchone()["n"]
    f["not_in_series"] = sid
    where, args, join = query.build_where(f)
    order = query.build_order(sort or [{"f": "taken", "d": "asc"}, {"f": "name", "d": "asc"}], 1, False)
    rows = c.execute("SELECT p.id, p.folder, p.width, p.height, p.orientation, p.preview_ver FROM photos p%s WHERE %s ORDER BY %s"
                     % (join, where, order), args).fetchall()
    return total, rows


@app.post("/api/series/{sid}/match")
def series_match(sid: int, body: Dict[str, Any] = Body(...)):
    """Dry run for the board's Add photos panel: how many match, how many are new, a few to preview."""
    _series(sid)
    total, rows = _match(sid, body.get("filters") or {}, body.get("sort") or [])
    folders: Dict[str, int] = {}
    for r in rows:
        folders[r["folder"]] = folders.get(r["folder"], 0) + 1
    return {"total": total, "new": len(rows), "limit": ADD_LIMIT, "folders": len(folders),
            "sample": [{"id": r["id"], "v": r["preview_ver"]} for r in rows[:14]]}


@app.post("/api/series/{sid}/add-matching")
def series_add_matching(sid: int, body: Dict[str, Any] = Body(...)):
    """Add every photo matching folders/criteria that isn't on the board yet, laid out in a grid.

    target: {"frame": <frame id>} (an existing frame or maybe pile), {"new": true} (one new frame),
    or {"per_folder": true} (one new frame per folder, named after it). Frames grow to fit; whatever
    sits below a grown frame moves down with it."""
    s = _series(sid)
    filters = body.get("filters") or {}
    sort = body.get("sort") or []
    target = body.get("target") or {"new": True}
    _, rows = _match(sid, filters, sort)
    rows = rows[:ADD_LIMIT]
    if not rows:
        return {"added": 0}
    with db.write_lock:
        c = db.conn()
        frames = [dict(r) for r in c.execute("SELECT id,label,x,y,w,h,maybe,color,border FROM series_frames WHERE series_id=? ORDER BY id", (sid,)).fetchall()]
        items = [dict(r) for r in c.execute(
            "SELECT si.photo_id id, si.x, si.y, p.width, p.height, p.orientation FROM series_items si JOIN photos p ON p.id=si.photo_id "
            "WHERE si.series_id=?", (sid,)).fetchall()]
        for it in items:
            it["tw"], it["th"] = _tile(it["width"], it["height"], it["orientation"])
            it["x"], it["y"] = it["x"] or 0, it["y"] or 0

        def inside(it, f):
            cx, cy = it["x"] + it["tw"] / 2, it["y"] + it["th"] / 2
            return f["x"] <= cx <= f["x"] + f["w"] and f["y"] <= cy <= f["y"] + f["h"]

        def bottom_of_all():
            return max([f["y"] + f["h"] for f in frames] + [it["y"] + it["th"] for it in items] + [0])

        def place(f, group, start_y):
            cols = max(1, int((f["w"] - 32) // CELL_W))
            for k, r in enumerate(group):
                tw, th = _tile(r["width"], r["height"], r["orientation"])
                x = f["x"] + 16 + (k % cols) * CELL_W + (150 - tw) // 2
                y = start_y + (k // cols) * CELL_H + (105 - th) // 2
                c.execute("INSERT OR IGNORE INTO series_items(series_id,photo_id,x,y,ord) VALUES(?,?,?,?,NULL)", (sid, r["id"], x, y))
                items.append({"id": r["id"], "x": x, "y": y, "tw": tw, "th": th})
            rows_n = (len(group) + cols - 1) // cols
            return start_y + rows_n * CELL_H

        def new_frame(label, n):
            cols = min(8, max(5, int((n * 1.5) ** 0.5) + 1))
            rows_n = (n + cols - 1) // cols
            f = {"label": label, "x": 40, "y": bottom_of_all() + 40 if (frames or items) else 40,
                 "w": 32 + cols * CELL_W, "h": 50 + rows_n * CELL_H, "maybe": 0}
            f["id"] = c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe) VALUES(?,?,?,?,?,?,0)",
                                (sid, f["label"], f["x"], f["y"], f["w"], f["h"])).lastrowid
            frames.append(f)
            return f

        def next_label():
            nums = [int(f["label"]) for f in frames if not f["maybe"] and str(f["label"]).isdigit()]
            return str((max(nums) if nums else 0) + 1).zfill(2)

        if target.get("frame"):
            f = next((x for x in frames if x["id"] == int(target["frame"])), None)
            if not f:
                raise HTTPException(400, "that frame no longer exists")
            old_bottom = f["y"] + f["h"]
            mine = [it for it in items if inside(it, f)]
            start = max([f["y"] + 40] + [it["y"] + it["th"] + 14 for it in mine])
            others_below = [it for it in items if not inside(it, f) and it["y"] >= old_bottom - 1]
            frames_below = [x for x in frames if x is not f and x["y"] >= old_bottom - 1]
            end = place(f, rows, start)
            grow = end + 10 - old_bottom
            if grow > 0:
                f["h"] += grow
                c.execute("UPDATE series_frames SET h=? WHERE id=?", (f["h"], f["id"]))
                for x in frames_below:
                    x["y"] += grow
                    c.execute("UPDATE series_frames SET y=? WHERE id=?", (x["y"], x["id"]))
                for it in others_below:
                    c.execute("UPDATE series_items SET y=? WHERE series_id=? AND photo_id=?", (it["y"] + grow, sid, it["id"]))
        elif target.get("per_folder"):
            groups: Dict[str, list] = {}
            for r in rows:
                groups.setdefault(r["folder"], []).append(r)
            for folder, group in groups.items():
                f = new_frame(folder.rsplit("/", 1)[-1] or folder or "Root", len(group))
                place(f, group, f["y"] + 40)
        else:
            f = new_frame(str(target.get("label") or "").strip() or next_label(), len(rows))
            place(f, rows, f["y"] + 40)
        c.commit()
    db.set_setting(_add_criteria_key(sid), json.dumps({"filters": filters, "target": target}))
    ids = [r["id"] for r in rows]
    series_mod.after_add(sid, ids)
    if config.write_series_tags:
        set_meta({"ids": ids, "attach": [series_mod.tag_of(s)]})
    return {"added": len(ids)}


# ---------------------------------------------------------------- agents + jobs (hand-off to darktable)
from . import transfers  # noqa: E402  batches of photos sent to a device: totals, Stop, Resume (see transfers.py)

app.include_router(transfers.router)


def list_agents() -> List[dict]:
    """Every agent, in a stable order that does not change with heartbeats: by name (case-insensitive), then id.
    (Ordered by last_seen, two devices swapped places on every refresh.)"""
    out = []
    t = time.time()
    rows = db.conn().execute("SELECT * FROM agents").fetchall()
    for r in sorted(rows, key=lambda r: ((r["name"] or r["id"] or "").casefold(), r["id"])):
        d = dict(r)
        d["state"] = json.loads(d["state"] or "{}")
        d["online"] = (t - (d["last_seen"] or 0)) < AGENT_ONLINE_SECONDS
        if not d["online"]:  # a device that stopped reporting is not downloading or syncing anything any more
            for it in d["state"].get("working_set") or []:
                if isinstance(it, dict):
                    it.pop("step", None)
                    it.pop("progress", None)
        out.append(d)
    transfers.decorate(out)  # caps, transfer, stopped, folded
    return out


def _clean_progress(ws) -> None:
    """Working-set items may carry step ("downloading", "sending edit", "rendering preview", "sending preview")
    and progress (0 to 1) while a device copies or syncs a photo. Kept with the item as sent; a progress that is
    not a number is dropped, one outside 0..1 is clamped. Items without them are left as they are."""
    if not isinstance(ws, list):
        return
    for it in ws:
        if not isinstance(it, dict):
            continue
        if "step" in it and not (isinstance(it["step"], str) and it["step"].strip()):
            it.pop("step")
        elif "step" in it:
            it["step"] = it["step"].strip()[:40]
        if "progress" in it:
            p = it["progress"]
            if isinstance(p, bool) or not isinstance(p, (int, float)) or p != p:
                it.pop("progress")
            else:
                it["progress"] = max(0.0, min(1.0, float(p)))


@app.get("/api/agents")
def agents():
    return {"agents": list_agents(), "server_time": time.time()}


@app.delete("/api/agents/{aid}")
def agent_forget(aid: str):
    with db.write_lock:
        db.conn().execute("DELETE FROM agents WHERE id=?", (aid,))
        db.conn().commit()
    return {"ok": True}


@app.post("/api/agents/heartbeat")
def heartbeat(body: Dict[str, Any] = Body(...)):
    aid = str(body.get("id") or "")[:80]
    if not aid:
        raise HTTPException(400, "agent id required")
    state = body.get("state") or {}
    if isinstance(state, dict):
        _clean_progress(state.get("working_set"))
    with db.write_lock:
        c = db.conn()
        c.execute("INSERT INTO agents(id,name,os,last_seen,state) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                  "name=excluded.name, os=excluded.os, last_seen=excluded.last_seen, state=excluded.state",
                  (aid, body.get("name"), body.get("os"), time.time(), json.dumps(state)))
        # an Android agent (the darktable phone app) only takes jobs addressed to it, never ones meant for any PC
        anyone = "" if body.get("os") == "android" else " OR agent_id IS NULL"
        now = time.time()
        jobs = c.execute("SELECT * FROM jobs WHERE status='queued' AND (agent_id=?%s) ORDER BY id" % anyone, (aid,)).fetchall()
        if config.job_retry_seconds > 0:
            # taken by this agent long ago and never reported done (the app died, or the report got lost): again
            # (attempts is 0 for jobs taken before LPL counted them: those are left alone, they may be weeks old)
            jobs += c.execute("SELECT * FROM jobs WHERE status='taken' AND agent_id=? AND taken<? AND attempts BETWEEN 1 AND ? ORDER BY id",
                              (aid, now - config.job_retry_seconds, JOB_MAX_HANDOUTS - 1)).fetchall()
        out = []
        for j in jobs:
            c.execute("UPDATE jobs SET status='taken', taken=?, agent_id=?, attempts=COALESCE(attempts,0)+1 WHERE id=?",
                      (now, aid, j["id"]))
            out.append({"id": j["id"], "kind": j["kind"], "payload": json.loads(j["payload"] or "{}")})
        extra = transfers.on_heartbeat(c, aid, body, out, now)  # nothing for a device that announces no caps
        c.commit()
    return {"jobs": out, "server_time": time.time(), **extra}


@app.post("/api/jobs")
def job_create(body: Dict[str, Any] = Body(...)):
    kind = body.get("kind")
    if kind not in ("edit", "sync", "release", "open"):
        raise HTTPException(400, "unknown job")
    if config.local:
        if kind not in ("edit", "open"):
            return {"id": 0, "agent_id": "local", "message": "Nothing to sync: local photos are edited in place."}
        from . import localedit
        try:
            return {"id": 0, "agent_id": "local", "message": localedit.open_photos((body.get("payload") or {}).get("ids") or [])}
        except Exception as e:
            raise HTTPException(500, str(e))
    agent_id = body.get("agent_id")
    if not agent_id:
        online = [a for a in list_agents() if a["online"] and a["os"] != "android"]  # never pick a phone by itself
        agent_id = online[0]["id"] if len(online) == 1 else None
    with db.write_lock:
        c = db.conn()
        cur = c.execute("INSERT INTO jobs(agent_id,kind,payload,created) VALUES(?,?,?,?)",
                        (agent_id, kind, json.dumps(body.get("payload") or {}), time.time()))
        if kind == "edit":
            transfers.register(c, cur.lastrowid, agent_id, body.get("payload") or {})
        c.commit()
    return {"id": cur.lastrowid, "agent_id": agent_id}


@app.get("/api/jobs")
def jobs_list():
    rows = db.conn().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
    return {"jobs": [dict(r, payload=json.loads(r["payload"] or "{}")) for r in rows]}


@app.post("/api/jobs/{jid}/done")
def job_done(jid: int, body: Dict[str, Any] = Body(default={})):
    with db.write_lock:
        db.conn().execute("UPDATE jobs SET status=? WHERE id=?", (body.get("status") or "done", jid))
        transfers.on_done(db.conn(), jid, body)
        db.conn().commit()
    return {"ok": True}


@app.get("/api/photos/{pid}/checkout")
def checkout(pid: int):
    r = photo_row(pid)
    return {"id": pid, "relpath": r["relpath"], "filename": r["filename"], "size": r["size"], "mtime": r["mtime"],
            "meta_mtime": r["meta_mtime"], "server_time": time.time()}


@app.get("/api/photos/{pid}/original")
def original(pid: int):
    r = photo_row(pid)
    p = scanner.abs_of(r["relpath"])
    if not p.is_file():
        raise HTTPException(404, "file missing on NAS")
    return FileResponse(p, media_type="application/octet-stream", filename=r["filename"])


@app.get("/api/photos/{pid}/xmp")
def get_xmp(pid: int):
    r = photo_row(pid)
    sc = xmp.sidecar_path(scanner.abs_of(r["relpath"]))
    if sc.is_file():
        data = sc.read_bytes()
    else:
        data = xmp.apply_meta(None, rating=r["rating"], labels=db.photo_labels(pid),
                              tags=[t for t in db.photo_tags(pid) if not t.startswith("darktable|")],
                              category_tags=category_tags())
    return Response(data, media_type="application/rdf+xml")


def _posting_agent(pid: int, agent: str) -> Optional[str]:
    """The agent that posted an XMP: the one it names (agent=<id>), else, for agents that do not send it yet,
    the only device that has the photo in its working set; None when that is not exactly one."""
    if agent:
        return agent[:80]
    having = [a["id"] for a in list_agents()
              if any(isinstance(w, dict) and w.get("id") == pid for w in (a["state"].get("working_set") or []))]
    return having[0] if len(having) == 1 else None


def _record_post(pid: int, sc: Path, new_hist: list, source: str, agent_id: Optional[str]) -> None:
    """Before the posted XMP replaces sc: record it as an edit event, compared with the file it replaces.
    If that file had changed since LPL last recorded it (darktable on a PC that wrote to the NAS directly),
    that change is recorded first, as edited outside LPL. Caller holds db.write_lock."""
    old = None
    if sc.is_file():
        try:
            old = xmp.parse(sc.read_bytes())["history"]
            old_mtime = sc.stat().st_mtime
        except Exception:
            old = None
    base = edits.baseline(pid)
    if old is not None and base is not None and old_mtime > base["mtime"] + 0.001 \
            and edits.signature(old) != edits.signature(base["history"]):
        edits.record(pid, old, base["history"], "outside", ts=old_mtime, mtime=old_mtime)
    prev = old if old is not None else (base["history"] if base else None)
    edits.record(pid, new_hist, prev, source, agent_id=agent_id)


@app.post("/api/photos/{pid}/xmp")
async def put_xmp(pid: int, request: Request, checkout_ts: float = 0, agent: str = ""):
    """Sync-back from the agent. darktable's edit always wins; for rating/labels/tags the LPL values win
    if they were changed in LPL after the checkout. agent = the posting agent's id (recorded as who edited)."""
    data = await request.body()
    try:
        xmp.parse(data)
    except Exception:
        raise HTTPException(400, "not a valid XMP file")
    r = photo_row(pid)
    data = xmp.set_derived_from(data, r["filename"])
    merged = False
    if (r["meta_mtime"] or 0) > checkout_ts > 0:
        data = xmp.apply_meta(data, rating=r["rating"], labels=db.photo_labels(pid),
                              tags=[t for t in db.photo_tags(pid) if not t.startswith("darktable|")],
                              category_tags=category_tags())
        merged = True
    sc = xmp.sidecar_path(scanner.abs_of(r["relpath"]))
    agent_id = _posting_agent(pid, agent)
    with db.write_lock:
        x = xmp.parse(data)
        try:
            _record_post(pid, sc, x["history"], "agent", agent_id)
        except Exception:  # the record must never cost the edit
            log.exception("could not record the edit of photo %s", pid)
        try:
            xmp.atomic_write(sc, data, config.backup_dir, r["relpath"])
        except Exception:
            db.conn().rollback()  # no record of an edit that was not stored
            raise
        x["mtime"] = sc.stat().st_mtime
        scanner.apply_xmp_to_db(pid, x, touch=False)
        edits.set_baseline(pid, x["history"], x["mtime"])
        db.conn().commit()
    return Response(data, media_type="application/rdf+xml", headers={"X-LPL-Merged": "1" if merged else "0"})


@app.post("/api/photos/{pid}/preview")
async def put_preview(pid: int, request: Request):
    """darktable's rendered JPEG replaces the embedded-preview thumbnails."""
    import io
    from PIL import Image
    photo_row(pid)
    data = await request.body()
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        media.save_previews(img.convert("RGB"), pid)
    except Exception as e:
        raise HTTPException(400, "bad image: %s" % e)
    with db.write_lock:
        db.conn().execute("UPDATE photos SET preview_ver=preview_ver+1, preview_state=1 WHERE id=?", (pid,))
        db.conn().commit()
    return {"ok": True}


# ---------------------------------------------------------------- import (photos sent from a PC's local library)
def _dest(dest: str) -> Path:
    """A relative destination inside the photo folder, e.g. '2026/2026-12-03_Lisbon/DSCF0001.RAF'."""
    d = (dest or "").replace("\\", "/").strip("/")
    parts = d.split("/")
    if not d or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(400, "bad destination")
    ext = parts[-1].rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
    from .config import SUPPORTED_EXTS
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(400, "not a photo file: %s" % parts[-1])
    try:
        return scanner.abs_of(d)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@app.post("/api/import/plan")
def import_plan(body: Dict[str, Any] = Body(...)):
    """For each {dest, size}: 'new', 'same' (already there, same size; sha256 included) or 'conflict'."""
    if config.local:
        raise HTTPException(400, "this is a local library, not the NAS")
    out = []
    for it in body.get("items") or []:
        p = _dest(it.get("dest"))
        if not p.exists():
            out.append({"dest": it["dest"], "status": "new"})
        elif p.stat().st_size == int(it.get("size") or -1):
            out.append({"dest": it["dest"], "status": "same", "sha256": _sha256(p)})
        else:
            out.append({"dest": it["dest"], "status": "conflict"})
    return {"items": out}


@app.put("/api/import/file")
async def import_file(request: Request, dest: str, size: int):
    if config.local:
        raise HTTPException(400, "this is a local library, not the NAS")
    p = _dest(dest)
    if p.exists():
        if p.stat().st_size == size:
            return {"status": "same", "sha256": _sha256(p)}
        raise HTTPException(409, "a different file with this name already exists: %s" % dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(".lpl-upload-" + p.name)
    h = hashlib.sha256()
    n = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
                h.update(chunk)
                n += len(chunk)
        if n != size:
            raise HTTPException(400, "upload incomplete (%d of %d bytes)" % (n, size))
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"status": "stored", "sha256": h.hexdigest()}


@app.put("/api/import/xmp")
async def import_xmp(request: Request, dest: str):
    """The sidecar for an image sent earlier (or right before). darktable's latest edit wins."""
    if config.local:
        raise HTTPException(400, "this is a local library, not the NAS")
    img = _dest(dest)
    data = await request.body()
    try:
        data = xmp.set_derived_from(data, img.name)
        x = xmp.parse(data)
    except Exception:
        raise HTTPException(400, "not a valid XMP file")
    sc = xmp.sidecar_path(img)
    sc.parent.mkdir(parents=True, exist_ok=True)
    with db.write_lock:
        row = db.conn().execute("SELECT id FROM photos WHERE relpath=?", (scanner.rel_of(img),)).fetchone()
        if row:
            try:  # an edit sent from a PC's local library: recorded when its modules differ from what was here
                old = xmp.parse(sc.read_bytes())["history"] if sc.is_file() else None
                base = edits.baseline(row["id"])
                prev = old if old is not None else (base["history"] if base else None)
                if prev is not None and edits.signature(prev) != edits.signature(x["history"]):
                    edits.record(row["id"], x["history"], prev, "import")
            except Exception:
                log.exception("could not record the edit of %s", dest)
        try:
            xmp.atomic_write(sc, data, config.backup_dir, scanner.rel_of(img))
        except Exception:
            db.conn().rollback()
            raise
        if row:
            x["mtime"] = sc.stat().st_mtime
            scanner.apply_xmp_to_db(row["id"], x, touch=False)
            edits.set_baseline(row["id"], x["history"], x["mtime"])
            db.conn().commit()
    return {"ok": True}


@app.post("/api/import/index")
def import_index(body: Dict[str, Any] = Body(...)):
    """Index the received files now, and merge the series that came with them (with their board layout)."""
    if config.local:
        raise HTTPException(400, "this is a local library, not the NAS")
    paths = [_dest(d) for d in body.get("dests") or []]
    ids = scanner.index_paths(paths)
    by_dest = {d: ids.get(scanner.rel_of(_dest(d))) for d in body.get("dests") or []}
    merged = []
    added_by_series: Dict[str, list] = {}
    for se in body.get("series") or []:
        name = (se.get("name") or "").strip().replace("|", "/")
        if not name:
            continue
        with db.write_lock:
            c = db.conn()
            r = c.execute("SELECT id FROM series WHERE name=?", (name,)).fetchone()
            created = r is None
            if created:
                sid = c.execute("INSERT INTO series(name, created) VALUES(?,?)", (name, time.time())).lastrowid
                series_mod.import_place(c, sid, se)  # its folder, colour and link state, when the sender has them
                for f in se.get("frames") or []:
                    c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,?,?,?)",
                              (sid, f.get("label") or "", float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"]), int(bool(f.get("maybe"))),
                               str(f.get("color") or "")[:20], str(f.get("border") or "")[:10]))
            else:
                sid = r["id"]
            new_ids = []
            for it in se.get("items") or []:
                pid = by_dest.get(it.get("dest")) or ids.get(scanner.rel_of(_dest(it.get("dest"))))
                if not pid:
                    continue
                cur = c.execute("INSERT OR IGNORE INTO series_items(series_id,photo_id,x,y,ord) VALUES(?,?,?,?,?)",
                                (sid, pid, float(it.get("x") or 0), float(it.get("y") or 0), it.get("ord")))
                if cur.rowcount:
                    new_ids.append(pid)
            shapes = se.get("shapes") or []
            has = c.execute("SELECT data FROM series_shapes WHERE series_id=?", (sid,)).fetchone()
            if shapes and (created or not has or has["data"] in ("", "[]")):
                for sh in shapes:
                    for end in ("a", "b"):
                        e = sh.get(end)
                        if isinstance(e, dict) and e.get("dest"):
                            pid = by_dest.get(e.pop("dest"))
                            if pid:
                                e["photo"] = pid
                c.execute("INSERT INTO series_shapes(series_id,data) VALUES(?,?) ON CONFLICT(series_id) DO UPDATE SET data=excluded.data",
                          (sid, json.dumps(shapes, ensure_ascii=False)))
            c.commit()
        if new_ids and config.write_series_tags:
            set_meta({"ids": new_ids, "attach": [series_mod.tag_of(_series(sid))]})
        merged.append({"name": name, "created": created, "added": len(new_ids)})
        added_by_series[name] = new_ids
    series_mod.import_boards(body.get("series_folders"), lambda d: by_dest.get(d) if d else None, added_by_series)
    return {"indexed": len(ids), "ids": by_dest, "series": merged}


# ---------------------------------------------------------------- local library (LPL running on a PC)
def _local_only():
    if not config.local:
        raise HTTPException(400, "only in a PC's local library")


@app.get("/api/library/folders")
def library_folders():
    _local_only()
    c = db.conn()
    out = []
    for name, root in scanner.roots():
        n = c.execute("SELECT COUNT(*) n FROM photos WHERE missing=0 AND (relpath LIKE ? ESCAPE '\\')",
                      (name.replace("%", "\\%").replace("_", "\\_") + "/%",)).fetchone()["n"]
        out.append({"name": name, "path": str(root), "online": root.is_dir(), "count": n})
    return {"folders": out}


@app.post("/api/library/folders")
def library_add(body: Dict[str, Any] = Body(...)):
    _local_only()
    p = Path((body.get("path") or "").strip().strip('"')).expanduser()
    if not p.is_dir():
        raise HTTPException(400, "folder not found: %s" % p)
    p = p.resolve()
    items = json.loads(db.get_setting("roots", "[]"))
    for r in items:
        rp = Path(r["path"]).resolve()
        if rp == p or rp in p.parents or p in rp.parents:
            raise HTTPException(409, "already in the library (overlaps %s)" % r["path"])
    base = p.name or str(p).strip(":\\/") or "Photos"
    name, k = base, 2
    names = {r["name"] for r in items}
    while name in names:
        name, k = "%s (%d)" % (base, k), k + 1
    items.append({"name": name, "path": str(p)})
    db.set_setting("roots", json.dumps(items))
    import threading
    threading.Thread(target=scanner.scan, daemon=True).start()
    return {"name": name}


@app.post("/api/library/folders/remove")
def library_remove(body: Dict[str, Any] = Body(...)):
    """Forget a folder (the photos on disk are not touched)."""
    _local_only()
    name = body.get("name")
    items = [r for r in json.loads(db.get_setting("roots", "[]")) if r["name"] != name]
    db.set_setting("roots", json.dumps(items))
    with db.write_lock:
        c = db.conn()
        ids = [r["id"] for r in c.execute("SELECT id FROM photos WHERE relpath LIKE ? ESCAPE '\\'",
                                          (name.replace("%", "\\%").replace("_", "\\_") + "/%",)).fetchall()]
        for pid in ids:
            for t in ("photo_labels", "photo_tags", "series_items", "folder_items", "sent"):
                c.execute("DELETE FROM %s WHERE photo_id=?" % t, (pid,))
            c.execute("DELETE FROM photos WHERE id=?", (pid,))
        c.commit()
    return {"removed": len(ids)}


@app.get("/api/fs/list")
def fs_list(path: str = ""):
    """Folder picker for 'add folder' (local library only)."""
    _local_only()
    if not path:
        if os.name == "nt":
            import string
            drives = ["%s:\\" % d for d in string.ascii_uppercase if os.path.exists("%s:\\" % d)]
            return {"path": "", "parent": None, "dirs": drives}
        path = str(Path.home())
    p = Path(path)
    try:
        dirs = sorted((d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith((".", "$"))), key=str.lower)
    except OSError as e:
        raise HTTPException(400, str(e))
    parent = str(p.parent) if p.parent != p else ""
    return {"path": str(p), "parent": parent, "dirs": [str(p / d) for d in dirs]}


@app.get("/api/send/overview")
def send_overview():
    _local_only()
    from . import transfer
    return dict(transfer.overview(), nas=transfer.nas_status(), delete_after=db.get_setting("send_delete_after", "0") == "1")


@app.get("/api/send/nas-folders")
def send_nas_folders(year: str = ""):
    _local_only()
    from . import transfer
    try:
        paths = transfer.nas_folders()
    except Exception as e:
        raise HTTPException(502, "NAS not reachable: %s" % e)
    return {"folders": paths, "suggested_parent": transfer.suggest_parent(paths, year or time.strftime("%Y"))}


def _send_ids(body) -> List[int]:
    if body.get("ids"):
        return [int(i) for i in body["ids"]]
    where, args, join = query.build_where({"folder": body.get("folder") or "", "subfolders": True})
    return [r["id"] for r in db.conn().execute("SELECT p.id FROM photos p%s WHERE %s" % (join, where), args).fetchall()]


@app.post("/api/send/plan")
def send_plan(body: Dict[str, Any] = Body(...)):
    _local_only()
    from . import transfer
    items = transfer.plan(_send_ids(body), body.get("parent") or "", body.get("descriptor") or "", bool(body.get("split_by_day", True)))
    folders = {}
    for it in items:
        f = it["dest"].rsplit("/", 1)[0]
        folders[f] = folders.get(f, 0) + 1
    return {"count": len(items), "bytes": sum(i["size"] or 0 for i in items if not i["already_sent"]),
            "already_sent": sum(1 for i in items if i["already_sent"]),
            "folders": [{"path": k, "count": v} for k, v in sorted(folders.items())]}


@app.post("/api/send/start")
def send_start(body: Dict[str, Any] = Body(...)):
    _local_only()
    from . import transfer
    db.set_setting("send_delete_after", "1" if body.get("delete_after") else "0")
    try:
        return transfer.start(_send_ids(body), body.get("parent") or "", body.get("descriptor") or "",
                              bool(body.get("split_by_day", True)), bool(body.get("delete_after")))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/send/status")
def send_status():
    _local_only()
    from . import transfer
    return transfer.state


# ---------------------------------------------------------------- web UI
class SPAStatic(StaticFiles):
    async def get_response(self, path, scope):
        try:
            resp = await super().get_response(path, scope)
        except Exception:
            resp = None
        if resp is None or resp.status_code == 404:
            return await super().get_response("index.html", scope)
        return resp


def mount_web() -> None:
    if config.web_dir.is_dir():
        app.mount("/", SPAStatic(directory=str(config.web_dir), html=True), name="web")
    else:
        @app.get("/")
        def no_web():
            return {"message": "LightPhotoLibrary API is running. Web UI not built (missing %s)." % config.web_dir}


# series folders, the strip and the board link: their routes and the rules for membership and order (series.py)
from . import series as series_mod  # noqa: E402
