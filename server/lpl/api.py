"""HTTP API + static web UI."""
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, media, query, scanner, xmp
from .config import config

log = logging.getLogger("lpl.api")
app = FastAPI(title="LightPhotoLibrary", version="0.1.0")
AGENT_ONLINE_SECONDS = 15


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
    if config.password and path.startswith("/api/") and path not in ("/api/login", "/api/auth"):
        tok = request.cookies.get("lpl_token") or request.headers.get("x-lpl-token")
        if tok not in (_token(), config.password):
            return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)


@app.get("/api/auth")
def auth_status(request: Request):
    if not config.password:
        return {"required": False, "ok": True}
    return {"required": True, "ok": request.cookies.get("lpl_token") == _token()}


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
            "photo_root": str(config.photo_root), "version": app.version}


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
    order = query.build_order(body.get("sort") or [], body.get("seed") or 1, bool(filters.get("series")))
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
    order = query.build_order(body.get("sort") or [], body.get("seed") or 1, bool(filters.get("series")))
    rows = db.conn().execute("SELECT p.id FROM photos p%s WHERE %s ORDER BY %s LIMIT 20000" % (join, where, order), args).fetchall()
    return {"ids": [r["id"] for r in rows]}


@app.post("/api/photos/get")
def photos_get(body: Dict[str, Any] = Body(...)):
    return {"items": photo_dicts([int(i) for i in body.get("ids") or []][:2000])}


@app.get("/api/photos/{pid}")
def photo_detail(pid: int):
    items = photo_dicts([pid])
    if not items:
        raise HTTPException(404)
    d = items[0]
    d["series"] = [dict(r) for r in db.conn().execute(
        "SELECT s.id, s.name FROM series_items si JOIN series s ON s.id=si.series_id WHERE si.photo_id=?", (pid,)).fetchall()]
    return d


@app.get("/api/preview/{pid}")
def preview(pid: int, s: str = "t"):
    t, l = media.preview_paths(pid)
    p = l if s == "l" else t
    if not p.is_file():
        if s == "l" and t.is_file():
            p = t
        else:
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
    return {"lenses": lenses, "cameras": cams}


@app.get("/api/folders")
def folders():
    c = db.conn()
    rows = c.execute(
        "SELECT folder, COUNT(*) n, SUM(edited) ed, MIN(taken) first, MAX(taken) last FROM photos WHERE missing=0 GROUP BY folder").fetchall()
    covers = {}
    for r in c.execute(
            "SELECT folder, id, preview_ver FROM (SELECT folder, id, preview_ver, ROW_NUMBER() OVER (PARTITION BY folder ORDER BY rating DESC, taken) rn "
            "FROM photos WHERE missing=0) WHERE rn<=4").fetchall():
        covers.setdefault(r["folder"], []).append({"id": r["id"], "v": r["preview_ver"]})
    agg: Dict[str, dict] = {}
    for r in rows:
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
            if len(a["covers"]) < 4:
                a["covers"].extend(covers.get(f, [])[: 4 - len(a["covers"])])
            if k == len(parts):
                a["direct"] = r["n"]
    pos = {r["path"]: r["pos"] for r in c.execute("SELECT path, pos FROM folder_order").fetchall()}
    for p, a in agg.items():
        a["pos"] = pos.get(p)
    return {"folders": sorted(agg.values(), key=lambda a: a["path"])}


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
    f = dict(body.get("filters") or {})
    f.pop("month", None)
    where, args, join = query.build_where(f)
    c = db.conn()
    rows = c.execute("SELECT substr(p.taken,1,7) m, COUNT(*) n, group_concat(DISTINCT p.folder) folders FROM photos p%s WHERE %s AND p.taken IS NOT NULL "
                     "GROUP BY m ORDER BY m DESC" % (join, where), args).fetchall()
    out = []
    for r in rows:
        cov = c.execute("SELECT p.id, p.preview_ver FROM photos p%s WHERE %s AND substr(p.taken,1,7)=? ORDER BY p.rating DESC, p.taken LIMIT 12" % (join, where),
                        args + [r["m"]]).fetchall()
        out.append({"month": r["m"], "count": r["n"], "folders": sorted({x.split("/")[-1] for x in (r["folders"] or "").split(",") if x}),
                    "covers": [{"id": x["id"], "v": x["preview_ver"]} for x in cov]})
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
        out.append({"id": s["id"], "name": s["name"], "count": n, "covers": [dict(i) for i in items]})
    return {"series": out}


@app.post("/api/series")
def series_create(body: Dict[str, Any] = Body(...)):
    name = (body.get("name") or "").strip().replace("|", "/")
    if not name:
        raise HTTPException(400, "name required")
    with db.write_lock:
        c = db.conn()
        try:
            cur = c.execute("INSERT INTO series(name, created) VALUES(?,?)", (name, time.time()))
        except Exception:
            raise HTTPException(409, "a series with this name exists")
        sid = cur.lastrowid
        c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe) VALUES(?,?,?,?,?,?,0)", (sid, "01", 40, 40, 900, 320))
        c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe) VALUES(?,?,?,?,?,?,1)", (sid, "Maybe pile", 980, 40, 360, 320))
        c.commit()
    ids = [int(i) for i in body.get("ids") or []]
    if ids:
        series_add(sid, {"ids": ids})
    return {"id": sid}


@app.patch("/api/series/{sid}")
def series_rename(sid: int, body: Dict[str, Any] = Body(...)):
    s = _series(sid)
    name = (body.get("name") or "").strip().replace("|", "/")
    if not name or name == s["name"]:
        return {"ok": True}
    with db.write_lock:
        db.conn().execute("UPDATE series SET name=? WHERE id=?", (name, sid))
        db.conn().commit()
    if config.write_series_tags:
        tag_rename({"old": SERIES_TAG + s["name"], "new": SERIES_TAG + name})
    return {"ok": True}


@app.delete("/api/series/{sid}")
def series_delete(sid: int):
    s = _series(sid)
    ids = [r["photo_id"] for r in db.conn().execute("SELECT photo_id FROM series_items WHERE series_id=?", (sid,)).fetchall()]
    if ids and config.write_series_tags:
        set_meta({"ids": ids, "detach": [SERIES_TAG + s["name"]]})
    with db.write_lock:
        c = db.conn()
        c.execute("DELETE FROM series_items WHERE series_id=?", (sid,))
        c.execute("DELETE FROM series_frames WHERE series_id=?", (sid,))
        c.execute("DELETE FROM series WHERE id=?", (sid,))
        c.execute("DELETE FROM tags WHERE path=? AND id NOT IN (SELECT tag_id FROM photo_tags)", (SERIES_TAG + s["name"],))
        c.commit()
    return {"ok": True}


@app.get("/api/series/{sid}/board")
def series_board(sid: int):
    s = _series(sid)
    c = db.conn()
    frames = [dict(r) for r in c.execute("SELECT id,label,x,y,w,h,maybe FROM series_frames WHERE series_id=? ORDER BY id", (sid,)).fetchall()]
    pos = {r["photo_id"]: r for r in c.execute("SELECT photo_id,x,y,ord FROM series_items WHERE series_id=?", (sid,)).fetchall()}
    items = photo_dicts(list(pos.keys()))
    for it in items:
        r = pos[it["id"]]
        it["x"], it["y"], it["ord"] = r["x"], r["y"], r["ord"]
    return {"series": {"id": s["id"], "name": s["name"]}, "frames": frames, "items": items}


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
    if new and config.write_series_tags:
        set_meta({"ids": new, "attach": [SERIES_TAG + s["name"]]})
    return {"added": len(new)}


@app.post("/api/series/{sid}/remove")
def series_remove(sid: int, body: Dict[str, Any] = Body(...)):
    s = _series(sid)
    ids = [int(i) for i in body.get("ids") or []]
    with db.write_lock:
        c = db.conn()
        for pid in ids:
            c.execute("DELETE FROM series_items WHERE series_id=? AND photo_id=?", (sid, pid))
        c.commit()
    if ids and config.write_series_tags:
        set_meta({"ids": ids, "detach": [SERIES_TAG + s["name"]]})
    return {"ok": True}


@app.put("/api/series/{sid}/board")
def series_save(sid: int, body: Dict[str, Any] = Body(...)):
    _series(sid)
    with db.write_lock:
        c = db.conn()
        for it in body.get("items") or []:
            c.execute("UPDATE series_items SET x=?, y=?, ord=? WHERE series_id=? AND photo_id=?",
                      (float(it.get("x", 0)), float(it.get("y", 0)), it.get("ord"), sid, int(it["id"])))
        if "frames" in body:
            keep = []
            for f in body["frames"]:
                vals = (f.get("label") or "", float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"]), int(bool(f.get("maybe"))))
                if f.get("id"):
                    c.execute("UPDATE series_frames SET label=?,x=?,y=?,w=?,h=?,maybe=? WHERE id=? AND series_id=?", vals + (int(f["id"]), sid))
                    keep.append(int(f["id"]))
                else:
                    cur = c.execute("INSERT INTO series_frames(label,x,y,w,h,maybe,series_id) VALUES(?,?,?,?,?,?,?)", vals + (sid,))
                    keep.append(cur.lastrowid)
            if keep:
                c.execute("DELETE FROM series_frames WHERE series_id=? AND id NOT IN (%s)" % ",".join(str(k) for k in keep), (sid,))
            else:
                c.execute("DELETE FROM series_frames WHERE series_id=?", (sid,))
        c.commit()
    return series_board(sid)


# ---------------------------------------------------------------- agents + jobs (hand-off to darktable)
def list_agents() -> List[dict]:
    out = []
    t = time.time()
    for r in db.conn().execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall():
        d = dict(r)
        d["state"] = json.loads(d["state"] or "{}")
        d["online"] = (t - (d["last_seen"] or 0)) < AGENT_ONLINE_SECONDS
        out.append(d)
    return out


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
    with db.write_lock:
        c = db.conn()
        c.execute("INSERT INTO agents(id,name,os,last_seen,state) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                  "name=excluded.name, os=excluded.os, last_seen=excluded.last_seen, state=excluded.state",
                  (aid, body.get("name"), body.get("os"), time.time(), json.dumps(body.get("state") or {})))
        jobs = c.execute("SELECT * FROM jobs WHERE status='queued' AND (agent_id=? OR agent_id IS NULL) ORDER BY id", (aid,)).fetchall()
        out = []
        for j in jobs:
            c.execute("UPDATE jobs SET status='taken', taken=?, agent_id=? WHERE id=?", (time.time(), aid, j["id"]))
            out.append({"id": j["id"], "kind": j["kind"], "payload": json.loads(j["payload"] or "{}")})
        c.commit()
    return {"jobs": out, "server_time": time.time()}


@app.post("/api/jobs")
def job_create(body: Dict[str, Any] = Body(...)):
    kind = body.get("kind")
    if kind not in ("edit", "sync", "release", "open"):
        raise HTTPException(400, "unknown job")
    agent_id = body.get("agent_id")
    if not agent_id:
        online = [a for a in list_agents() if a["online"]]
        agent_id = online[0]["id"] if len(online) == 1 else None
    with db.write_lock:
        c = db.conn()
        cur = c.execute("INSERT INTO jobs(agent_id,kind,payload,created) VALUES(?,?,?,?)",
                        (agent_id, kind, json.dumps(body.get("payload") or {}), time.time()))
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


@app.post("/api/photos/{pid}/xmp")
async def put_xmp(pid: int, request: Request, checkout_ts: float = 0):
    """Sync-back from the agent. darktable's edit always wins; for rating/labels/tags the LPL values win
    if they were changed in LPL after the checkout."""
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
    with db.write_lock:
        xmp.atomic_write(sc, data, config.backup_dir, r["relpath"])
        x = xmp.parse(data)
        x["mtime"] = sc.stat().st_mtime
        scanner.apply_xmp_to_db(pid, x, touch=False)
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
