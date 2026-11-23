"""Local mode: send photos from this PC's library to the NAS.

Each photo goes to <parent>/<YYYY-MM-DD>_<descriptor>/<filename> on the NAS (one folder per capture day,
or one folder for the whole send). The RAW is uploaded once; its XMP is uploaded every time it changed
since the last send. Series that contain sent photos come along, with their board layout.
Optionally the local copies are deleted, but only after the NAS confirmed an identical file (sha256)."""
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from . import db, media, scanner, xmp
from .config import config

log = logging.getLogger("lpl.transfer")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_")

state = {"running": False, "phase": "", "done": 0, "total": 0, "bytes_done": 0, "bytes_total": 0,
         "sent_files": 0, "sent_xmps": 0, "skipped": 0, "deleted": 0, "errors": [], "finished": None, "dest_folders": []}
_lock = threading.Lock()


# ------------------------------------------------------------------ NAS connection
class Nas:
    def __init__(self):
        if not config.nas_url:
            raise RuntimeError("No NAS address set (run the agent's --setup)")
        self.base = config.nas_url
        self.headers = {"x-lpl-token": config.nas_password} if config.nas_password else {}

    def _req(self, method, path, data=None, ctype=None, timeout=60, extra=None):
        h = dict(self.headers)
        if ctype:
            h["content-type"] = ctype
        if extra:
            h.update(extra)
        return urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, method=method, headers=h), timeout=timeout)

    def json(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        try:
            with self._req(method, path, data, "application/json" if data else None, timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(_http_error(e))

    def put_file(self, path, f, size, on_chunk):
        """Stream a file up in chunks (works for 100 MB RAWs over a slow connection)."""
        class Body:
            def __init__(self):
                self.h = hashlib.sha256()

            def __iter__(self):
                while True:
                    chunk = f.read(1024 * 512)
                    if not chunk:
                        break
                    self.h.update(chunk)
                    on_chunk(len(chunk))
                    yield chunk
        body = Body()
        try:
            with self._req("PUT", path, body, "application/octet-stream", timeout=600, extra={"content-length": str(size)}) as r:
                res = json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(_http_error(e))
        res["local_sha256"] = body.h.hexdigest()
        return res

    def put_bytes(self, path, data, ctype):
        try:
            with self._req("PUT", path, data, ctype, timeout=120) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(_http_error(e))


def _http_error(e):
    try:
        return "%s: %s" % (e.code, json.loads(e.read().decode()).get("detail"))
    except Exception:
        return "HTTP %s" % e.code


def nas_status() -> dict:
    try:
        st = Nas().json("GET", "/api/status", timeout=8)
        return {"ok": True, "url": config.nas_url, "photos": st.get("photos")}
    except Exception as e:
        return {"ok": False, "url": config.nas_url, "error": str(e)}


def nas_folders() -> List[str]:
    return [f["path"] for f in Nas().json("GET", "/api/folders")["folders"] if f["path"]]


# ------------------------------------------------------------------ planning
def default_descriptor(folder: str) -> str:
    """'Trips/2026-12-03_Lisbon' -> 'Lisbon'; 'Lisbon 2026' -> 'Lisbon 2026'."""
    name = folder.rstrip("/").split("/")[-1]
    name = re.sub(r"^\d{4}[-_.]?\d{2}[-_.]?\d{2}[ _-]*", "", name)
    return name.strip() or "Photos"


def suggest_parent(nas_paths: List[str], year: str) -> str:
    """Where this year's YYYY-MM-DD_* folders go on the NAS.

    Learns the pattern from existing dated folders: if they sit in a folder named after their year
    (e.g. 2025/2025-09-20_Porto, Photos/2025/...), use the same layout with this year, even if that
    year folder doesn't exist yet. Otherwise use the most common parent, else a year folder."""
    existing = set(nas_paths)
    by_year = Counter()   # layouts like "<prefix>/<year>"
    plain = Counter()
    for p in nas_paths:
        leaf = p.split("/")[-1]
        if not DATE_RE.match(leaf):
            continue
        parent = p.rsplit("/", 1)[0] if "/" in p else ""
        y = leaf[:4]
        pparts = parent.split("/") if parent else []
        if pparts and pparts[-1] == y:
            by_year["/".join(pparts[:-1])] += 3 if y == year else 1
        else:
            plain[parent] += 3 if y == year else 1
    if by_year and sum(by_year.values()) >= sum(plain.values()):
        prefix = by_year.most_common(1)[0][0]
        return (prefix + "/" + year) if prefix else year
    if plain:
        return plain.most_common(1)[0][0]
    for p in existing:
        if p.split("/")[-1] == year:
            return p
    return ""


def _day(row) -> str:
    if row["taken"]:
        return row["taken"][:10]
    return time.strftime("%Y-%m-%d", time.localtime(row["mtime"] or time.time()))


def plan(ids: List[int], parent: str, descriptor: str, split_by_day: bool) -> List[dict]:
    rows = db.conn().execute("SELECT id, relpath, filename, size, taken, mtime FROM photos WHERE missing=0 AND id IN (%s) ORDER BY taken, filename"
                             % ",".join(str(int(i)) for i in ids)).fetchall() if ids else []
    descriptor = re.sub(r'[\\/:*?"<>|]+', "-", (descriptor or "Photos").strip()) or "Photos"
    parent = (parent or "").replace("\\", "/").strip("/")
    first_day = _day(rows[0]) if rows else time.strftime("%Y-%m-%d")
    sent = {r["photo_id"]: dict(r) for r in db.conn().execute("SELECT * FROM sent").fetchall()}
    used = set()
    out = []
    for r in rows:
        if r["id"] in sent:  # already on the NAS: keep its place there
            dest = sent[r["id"]]["dest"]
        else:
            folder = "%s_%s" % (_day(r) if split_by_day else first_day, descriptor)
            base = (parent + "/" if parent else "") + folder + "/"
            name = r["filename"]
            dest = base + name
            k = 2
            while dest.lower() in used:
                stem, dot, ext = name.rpartition(".")
                dest = base + "%s_%d.%s" % (stem, k, ext)
                k += 1
        used.add(dest.lower())
        out.append({"id": r["id"], "relpath": r["relpath"], "dest": dest, "size": r["size"],
                    "already_sent": r["id"] in sent})
    return out


def overview() -> dict:
    """Per local folder: photos, how many are on the NAS, how many have edits not sent yet."""
    c = db.conn()
    rows = c.execute(
        "SELECT p.folder, COUNT(*) n, SUM(CASE WHEN s.photo_id IS NOT NULL THEN 1 ELSE 0 END) sent, "
        "SUM(CASE WHEN s.photo_id IS NOT NULL AND COALESCE(p.xmp_mtime,0) > COALESCE(s.xmp_mtime,0) + 1 THEN 1 ELSE 0 END) changed, "
        "MIN(p.taken) first, MAX(p.taken) last FROM photos p LEFT JOIN sent s ON s.photo_id=p.id WHERE p.missing=0 GROUP BY p.folder ORDER BY p.folder"
    ).fetchall()
    return {"folders": [dict(r, descriptor=default_descriptor(r["folder"])) for r in rows]}


# ------------------------------------------------------------------ sending
def _series_mod():
    from . import series  # (imported late: series imports api, which may import this module)
    return series


def _series_payload(dest_by_id: Dict[int, str]) -> list:
    c = db.conn()
    out = []
    for s in c.execute("SELECT * FROM series").fetchall():
        items = c.execute("SELECT photo_id, x, y, ord FROM series_items WHERE series_id=?", (s["id"],)).fetchall()
        mine = [i for i in items if i["photo_id"] in dest_by_id]
        if not mine:
            continue
        frames = [dict(f) for f in c.execute("SELECT label,x,y,w,h,maybe,color,border FROM series_frames WHERE series_id=?", (s["id"],)).fetchall()]
        entry = {"name": s["name"], "frames": frames, "shapes": _shapes_payload(s["id"], dest_by_id),
                 "items": [{"dest": dest_by_id[i["photo_id"]], "x": i["x"], "y": i["y"], "ord": i["ord"]} for i in mine]}
        # series folders (a NAS that does not know them ignores these and gets the series at the top level, as before)
        entry.update(color=s["color"] or "", linked=bool(s["linked"]), pos=s["pos"])
        if s["folder_id"]:
            # `folder` is the top-level folder (a NAS that knows one level of folders gets the series flattened into
            # it); folder_path names the folders down to the series' own, folder_meta their place and colour
            path = [c.execute("SELECT name, linked, pos, color FROM series_folders WHERE id=?", (p["id"],)).fetchone() for p in _series_mod().folder_path(c, s["folder_id"])]
            if path:
                entry.update(folder=path[0]["name"], folder_linked=bool(path[0]["linked"]), folder_path=[f["name"] for f in path],
                             folder_meta=[{"pos": f["pos"], "color": f["color"] or ""} for f in path])
        out.append(entry)
    return out


def _folders_payload(dest_by_id: Dict[int, str], series: list) -> list:
    """The shared board of every series folder that has a series in this send: frames (a frame that is a series
    names it), the places of the sent photos, notes and arrows."""
    c = db.conn()
    out = []
    for name in sorted({se["folder"] for se in series if se.get("folder")}):
        f = c.execute("SELECT * FROM series_folders WHERE name=?", (name,)).fetchone()
        if not f:
            continue
        frames = [dict(r) for r in c.execute(
            "SELECT ff.label, ff.x, ff.y, ff.w, ff.h, ff.color, ff.border, s.name series FROM folder_frames ff LEFT JOIN series s ON s.id=ff.series_id "
            "WHERE ff.folder_id=? AND ff.sub_id IS NULL ORDER BY ff.id", (f["id"],)).fetchall()]
        groups = [dict(r) for r in c.execute(  # the group frames of its subfolders (apart, so that an older NAS does not take them for frames)
            "SELECT ff.label, ff.x, ff.y, ff.w, ff.h, ff.color, ff.border, sf.name sub FROM folder_frames ff JOIN series_folders sf ON sf.id=ff.sub_id "
            "WHERE ff.folder_id=? ORDER BY ff.id", (f["id"],)).fetchall()]
        items = [{"dest": dest_by_id[r["photo_id"]], "x": r["x"], "y": r["y"]}
                 for r in c.execute("SELECT photo_id, x, y FROM folder_items WHERE folder_id=?", (f["id"],)).fetchall() if r["photo_id"] in dest_by_id]
        out.append({"name": f["name"], "linked": bool(f["linked"]), "frames": frames, "groups": groups, "items": items,
                    "shapes": _shapes_payload(f["id"], dest_by_id, "folder_shapes", "folder_id")})
    return out


def _shapes_payload(sid: int, dest_by_id: Dict[int, str], table: str = "series_shapes", key: str = "series_id") -> list:
    """Board notes and arrows; arrow ends on a photo name it by its NAS path (ids differ there)."""
    r = db.conn().execute("SELECT data FROM %s WHERE %s=?" % (table, key), (sid,)).fetchone()
    try:
        shapes = json.loads(r["data"]) if r else []
    except ValueError:
        return []
    for sh in shapes:
        for end in ("a", "b"):
            e = sh.get(end)
            if isinstance(e, dict) and e.get("photo") is not None:
                pid = e.pop("photo")
                if pid in dest_by_id:
                    e["dest"] = dest_by_id[pid]
    return shapes


def local_version_files(img) -> dict:
    """{n: path} of the version sidecars (IMG_01_02.RAF.xmp, two digits, three from 100) beside a photo, n >= 1."""
    from . import versions
    out = {}
    for f in img.parent.glob("*.xmp"):
        pn = versions.parse_name(f.name)
        if pn and pn[0] == img.name:
            out[pn[1]] = f
    return out


def _delete_local(pid: int) -> None:
    r = db.conn().execute("SELECT relpath FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        return
    img = scanner.abs_of(r["relpath"])
    for f in [img, xmp.sidecar_path(img)] + sorted(local_version_files(img).values()):
        try:
            if f.exists():
                f.unlink()
        except OSError as e:
            raise RuntimeError("could not delete %s: %s" % (f, e))
    c = db.conn()
    for t in ("photo_labels", "photo_tags", "series_items", "folder_items", "photo_versions", "photo_version_hw"):
        c.execute("DELETE FROM %s WHERE photo_id=?" % t, (pid,))
    c.execute("DELETE FROM photos WHERE id=?", (pid,))
    c.execute("DELETE FROM sent WHERE photo_id=?", (pid,))
    for pv in media.preview_paths(pid):
        try:
            pv.unlink()
        except OSError:
            pass


def _run(items: List[dict], delete_after: bool) -> None:
    nas = Nas()
    state.update(phase="checking what is already on the NAS")
    planned = {p["dest"]: p for p in nas.json("POST", "/api/import/plan", {"items": [{"dest": i["dest"], "size": i["size"]} for i in items]})["items"]}
    sent_rows = {r["photo_id"]: dict(r) for r in db.conn().execute("SELECT * FROM sent").fetchall()}
    state.update(phase="sending", bytes_total=sum(i["size"] or 0 for i in items if planned[i["dest"]]["status"] == "new"))
    verified = []
    dest_by_id = {}
    for it in items:
        pid, dest = it["id"], it["dest"]
        try:
            img = scanner.abs_of(it["relpath"])
            pl = planned[dest]
            q = urllib.parse.urlencode({"dest": dest, "size": it["size"]})
            if pl["status"] == "conflict":
                raise RuntimeError("a different file named %s is already on the NAS" % dest)
            if pl["status"] == "new":
                with open(img, "rb") as f:
                    res = nas.put_file("/api/import/file?" + q, f, it["size"], lambda n: state.__setitem__("bytes_done", state["bytes_done"] + n))
                sha_nas, sha_local = res.get("sha256"), res.get("local_sha256")
                state["sent_files"] += 1
            else:
                sha_nas = pl.get("sha256")
                sha_local = sent_rows.get(pid, {}).get("sha256") or _file_sha(img)
                state["skipped"] += 1
            if sha_nas != sha_local:
                raise RuntimeError("the copy on the NAS differs from the local file (%s)" % dest)
            sc = xmp.sidecar_path(img)
            xm = sc.stat().st_mtime if sc.is_file() else None
            if xm and (pid not in sent_rows or abs((sent_rows[pid].get("xmp_mtime") or 0) - xm) > 1 or pl["status"] == "new"):
                nas.put_bytes("/api/import/xmp?" + urllib.parse.urlencode({"dest": dest}), sc.read_bytes(), "application/rdf+xml")
                state["sent_xmps"] += 1
            for n, vf in sorted(local_version_files(img).items()):  # versions go with the photo, before the local copy may be deleted
                nas.put_bytes("/api/import/xmp?" + urllib.parse.urlencode({"dest": dest, "version": n}), vf.read_bytes(), "application/rdf+xml")
                state["sent_xmps"] += 1
            with db.write_lock:
                db.conn().execute("INSERT INTO sent(photo_id,dest,size,sha256,xmp_mtime,ts) VALUES(?,?,?,?,?,?) ON CONFLICT(photo_id) DO UPDATE SET "
                                  "dest=excluded.dest,size=excluded.size,sha256=excluded.sha256,xmp_mtime=excluded.xmp_mtime,ts=excluded.ts",
                                  (pid, dest, it["size"], sha_local, xm, time.time()))
                db.conn().commit()
            verified.append(pid)
            dest_by_id[pid] = dest
        except Exception as e:
            log.warning("send failed for %s: %s", dest, e)
            state["errors"].append("%s: %s" % (dest, e))
        state["done"] += 1
    if dest_by_id:
        state.update(phase="adding to the NAS library")
        series = _series_payload(dest_by_id)
        res = nas.json("POST", "/api/import/index", {"dests": list(dest_by_id.values()), "series": series,
                                                     "series_folders": _folders_payload(dest_by_id, series)}, timeout=900)
        state["series"] = res.get("series", [])
    if delete_after and verified:
        state.update(phase="deleting local copies")
        with db.write_lock:
            for pid in verified:
                try:
                    _delete_local(pid)
                    state["deleted"] += 1
                except Exception as e:
                    state["errors"].append(str(e))
            db.conn().commit()


def _file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def start(ids: List[int], parent: str, descriptor: str, split_by_day: bool, delete_after: bool) -> dict:
    with _lock:
        if state["running"]:
            raise RuntimeError("a send is already running")
        items = plan(ids, parent, descriptor, split_by_day)
        if not items:
            raise RuntimeError("nothing to send")
        state.update(running=True, phase="starting", done=0, total=len(items), bytes_done=0, bytes_total=0, sent_files=0,
                     sent_xmps=0, skipped=0, deleted=0, errors=[], finished=None, series=[],
                     dest_folders=sorted({i["dest"].rsplit("/", 1)[0] for i in items}))

    def worker():
        try:
            _run(items, delete_after)
        except Exception as e:
            log.exception("send failed")
            state["errors"].append(str(e))
        finally:
            state.update(running=False, phase="", finished=time.time())
    threading.Thread(target=worker, name="send-to-nas", daemon=True).start()
    return {"total": len(items), "folders": state["dest_folders"]}
