# SPDX-License-Identifier: GPL-3.0-or-later
"""Import from Miro: a Miro board becomes a new LPL series laid out the same way.

Flow (Series view → "Import from Miro…"):
  1. POST /api/miro/preview {url, token?, remember?} reads the board through Miro's REST API (v2, a personal access
     token with boards:read) and matches its pictures to photos in the library. Nothing is written. The answer says
     how many matched and how, lists what did not, and carries a `key` for step 2.
  2. POST /api/miro/import {key, name} creates the series: photos where they were, frames (nested ones too, Miro's
     colours mapped to LPL's), sticky notes, text and shapes with text as notes, connectors as arrows. A picture
     with no photo leaves a grey note "Not found: <title>" where it was, so the layout keeps its gaps.

Matching a picture (its Miro title, usually the file name it was uploaded with):
  - by name: the title without extension against the photos' file names without extension, case-insensitive
    (DSCF4821.jpg finds DSCF4821.RAF). Export suffixes like "_01", "-1", " (2)", "-edit" are tried off as well.
    Several photos with that name: the RAW, then the one in the folders most of the board comes from, then the
    one that looks most like the picture (a difference hash of Miro's preview against LPL's thumbnail).
  - by picture: a picture whose name finds nothing is compared with every photo in the folders the matched ones
    come from (at most MAX_VISUAL photos), and taken only when the two are clearly the same.

Places: Miro gives item centres, relative to the board or to the parent frame's top-left; LPL keeps top-left
corners. Everything is scaled so the typical picture is as wide as an LPL tile (150 px), then moved so the board
starts at (40, 40). The series is linked, so its order is read from the board as usual.
"""
import html
import io
import json
import logging
import re
import secrets
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException

from . import db, media
from .config import RAW_EXTS, config

log = logging.getLogger("lpl.miro")
router = APIRouter()

API = "https://api.miro.com/v2"
TOKEN_KEY = "miro.token"
MAX_VISUAL = 4000          # photos compared by picture, at most
SAME_PICTURE = 10          # dhash bits (of 64) that may differ for "the same photo"
SURE_GAP = 6               # ... and the next best must be at least this much further away
CACHE_TTL = 1800

# LPL's board colours (web/src/components/Board.tsx FRAME_COLORS / NOTE_COLORS)
FRAME_COLORS = ["#8a8a92", "#e5c547", "#f0a35a", "#e5636a", "#e07bb0", "#a07be0", "#5b9be6", "#5cc08a"]
NOTE_COLORS = ["#f5d76e", "#f6b26b", "#f4a6c0", "#b9a6f0", "#9cc8f0", "#a8dba0", "#d8d8dc"]
NOTE_GREY = "#d8d8dc"
STICKY = {  # Miro's named sticky-note colours
    "light_yellow": 0, "yellow": 0, "orange": 1, "light_pink": 2, "pink": 2, "red": 2, "violet": 3, "dark_violet": 3,
    "light_blue": 4, "blue": 4, "cyan": 4, "dark_blue": 4, "light_green": 5, "green": 5, "dark_green": 5,
    "gray": 6, "black": 6, "white": 6,
}
SUFFIX = re.compile(r"(?:[ _-]\(?\d{1,2}\)?|[ _-](?:edit(?:ed)?|copy|export(?:ed)?|final|web|small|hd|dt))$", re.I)

_cache: Dict[str, dict] = {}
_cache_lock = threading.Lock()


# ------------------------------------------------------------------ Miro
def board_id(url: str) -> str:
    """The board id from a board link (https://miro.com/app/board/uXjVO1234=/?...) or the id itself."""
    u = (url or "").strip()
    m = re.search(r"/board/([^/?#]+)", u)
    bid = urllib.parse.unquote(m.group(1) if m else u)
    if not re.fullmatch(r"[A-Za-z0-9_=\-]{6,}", bid):
        raise HTTPException(400, "That does not look like a Miro board link")
    return bid


def _request(url: str, token: str, raw: bool = False):
    """GET from Miro, waiting out rate limits a few times. Separate so tests can stand in for Miro."""
    for attempt in range(5):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json" if not raw else "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "")
            if raw:
                return data, ctype
            return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(min(30, float(e.headers.get("Retry-After") or 2 * (attempt + 1))))
                continue
            if e.code in (401, 403):
                raise HTTPException(400, "Miro refused the token (%d). Check that it has board read access and was made for the team that has this board." % e.code)
            if e.code == 404:
                raise HTTPException(400, "Miro does not know this board, or the token's team can't see it")
            raise HTTPException(502, "Miro answered %d" % e.code)
        except urllib.error.URLError as e:
            raise HTTPException(502, "Could not reach Miro from this server: %s" % e.reason)
    raise HTTPException(502, "Miro kept saying 'too many requests'; try again in a minute")


fetch: Callable = _request


def read_board(bid: str, token: str) -> dict:
    q = urllib.parse.quote(bid, safe="")
    info = fetch("%s/boards/%s" % (API, q), token)
    items: List[dict] = []
    cursor = ""
    while True:
        page = fetch("%s/boards/%s/items?limit=50%s" % (API, q, "&cursor=" + urllib.parse.quote(cursor) if cursor else ""), token)
        items += page.get("data") or []
        cursor = page.get("cursor") or ""
        if not cursor or not page.get("data"):
            break
    conns: List[dict] = []
    cursor = ""
    while True:
        page = fetch("%s/boards/%s/connectors?limit=50%s" % (API, q, "&cursor=" + urllib.parse.quote(cursor) if cursor else ""), token)
        conns += page.get("data") or []
        cursor = page.get("cursor") or ""
        if not cursor or not page.get("data"):
            break
    return {"name": info.get("name") or "Miro board", "items": items, "connectors": conns}


def image_bytes(item: dict, token: str) -> Optional[bytes]:
    """Miro's preview of a picture. Its imageUrl answers either the image or a little JSON with the real link."""
    url = (item.get("data") or {}).get("imageUrl")
    if not url:
        return None
    try:
        url = re.sub(r"format=original", "format=preview", url)
        data, ctype = fetch(url, token, raw=True)
        if "json" in ctype or data[:1] == b"{":
            link = json.loads(data.decode("utf-8")).get("url")
            if not link:
                return None
            with urllib.request.urlopen(link, timeout=30) as r:
                data = r.read()
        return data
    except Exception as e:  # a picture that can't be read is simply not compared
        log.info("miro image %s: %s", item.get("id"), e)
        return None


# ------------------------------------------------------------------ geometry
def absolute(items: List[dict]) -> Dict[str, dict]:
    """id -> {cx, cy, w, h} on the whole board. A child's position may be relative to its parent's top-left."""
    by = {str(it.get("id")): it for it in items}
    out: Dict[str, dict] = {}

    def get(iid: str, depth: int = 0) -> Optional[dict]:
        if iid in out:
            return out[iid]
        it = by.get(iid)
        if it is None or depth > 20:
            return None
        pos, geo = it.get("position") or {}, it.get("geometry") or {}
        w, h = float(geo.get("width") or 0), float(geo.get("height") or 0)
        if not h and w and it.get("type") == "image":
            h = w * 2 / 3
        x, y = float(pos.get("x") or 0), float(pos.get("y") or 0)
        if (pos.get("origin") or "center") != "center":
            x, y = x + w / 2, y + h / 2
        par = str((it.get("parent") or {}).get("id") or "")
        if par and (pos.get("relativeTo") or "") == "parent_top_left":
            p = get(par, depth + 1)
            if p:
                x, y = x + p["cx"] - p["w"] / 2, y + p["cy"] - p["h"] / 2
        out[iid] = {"cx": x, "cy": y, "w": w, "h": h}
        return out[iid]

    for iid in by:
        get(iid)
    return out


def text_of(s: Any) -> str:
    s = str(s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>\s*<p[^>]*>|</li>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def _hex(c: str) -> Optional[Tuple[int, int, int]]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (c or "").strip())
    if not m:
        return None
    v = m.group(1)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def nearest(c: str, palette: List[str], blank_light: bool) -> str:
    """The LPL colour closest in hue to a Miro colour; '' (none) for white / transparent frames."""
    rgb = _hex(c)
    if rgb is None:
        return ""
    if blank_light and min(rgb) > 235:
        return ""
    def dist(p):  # "redmean": RGB distance weighted the way the eye roughly sees it
        q = _hex(p)
        rm = (rgb[0] + q[0]) / 2
        dr, dg, db_ = rgb[0] - q[0], rgb[1] - q[1], rgb[2] - q[2]
        return (2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db_ * db_
    best = min(palette, key=dist)
    return best


def note_color(style: dict) -> str:
    fc = str((style or {}).get("fillColor") or "")
    if fc in STICKY:
        return NOTE_COLORS[STICKY[fc]]
    rgb = _hex(fc)
    if rgb is None or max(rgb) - min(rgb) < 30:  # white, grey, black, none
        return NOTE_GREY
    return nearest(fc, NOTE_COLORS, False) or NOTE_GREY


# ------------------------------------------------------------------ matching
def stem(name: str) -> str:
    n = (name or "").strip().rsplit("/", 1)[-1]
    if "." in n and len(n.rsplit(".", 1)[1]) <= 5:
        n = n.rsplit(".", 1)[0]
    return n.strip().lower()


def stems(title: str) -> List[str]:
    """The names to try for a title: itself, then without export suffixes (one at a time)."""
    out = []
    s = stem(title)
    while s and s not in out:
        out.append(s)
        s2 = SUFFIX.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    return out


def photo_index() -> Dict[str, List[dict]]:
    idx: Dict[str, List[dict]] = {}
    for r in db.conn().execute("SELECT id, filename, folder, ext FROM photos WHERE COALESCE(missing,0)=0").fetchall():
        idx.setdefault(stem(r["filename"]), []).append(dict(r))
    return idx


def dhash(data_or_img) -> Optional[int]:
    from PIL import Image, ImageOps
    try:
        img = data_or_img if isinstance(data_or_img, Image.Image) else Image.open(io.BytesIO(data_or_img))
        img = ImageOps.exif_transpose(img).convert("L").resize((9, 8), Image.LANCZOS)
    except Exception:
        return None
    px = list(img.getdata())
    v = 0
    for row in range(8):
        for col in range(8):
            v = (v << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return v


def photo_hash(pid: int, memo: Dict[int, Optional[int]]) -> Optional[int]:
    if pid not in memo:
        t, _ = media.preview_paths(pid)
        memo[pid] = dhash(t.read_bytes()) if t.exists() else None
    return memo[pid]


def bits(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def match(pictures: List[dict], token: str, picture_bytes: Callable = image_bytes) -> Dict[str, dict]:
    """miro id -> {photo, how} (how: name / name+picture / picture), or {photo: None}."""
    idx = photo_index()
    out: Dict[str, dict] = {}
    pending: List[Tuple[dict, List[dict]]] = []
    for it in pictures:
        title = (it.get("data") or {}).get("title") or ""
        cands: List[dict] = []
        for s in stems(text_of(title)):
            cands = idx.get(s) or []
            if cands:
                break
        raws = [c for c in cands if (c["ext"] or "").lower().lstrip(".") in RAW_EXTS]
        cands = raws or cands
        if len(cands) == 1:
            out[it["id"]] = {"photo": cands[0]["id"], "how": "name", "folder": cands[0]["folder"]}
        else:
            pending.append((it, cands))
    # the folders the board mostly comes from settle most of the rest
    folders: Dict[str, int] = {}
    for m in out.values():
        folders[m["folder"]] = folders.get(m["folder"], 0) + 1
    memo: Dict[int, Optional[int]] = {}
    pool: Optional[List[int]] = None
    for it, cands in pending:
        if cands:
            near = [c for c in cands if c["folder"] in folders]
            if len(near) == 1:
                out[it["id"]] = {"photo": near[0]["id"], "how": "name", "folder": near[0]["folder"]}
                continue
            h = dhash(picture_bytes(it, token) or b"")
            if h is not None:
                scored = sorted((bits(h, ph), c["id"], c["folder"]) for c in (near or cands) if (ph := photo_hash(c["id"], memo)) is not None)
                if scored and scored[0][0] <= SAME_PICTURE * 2:
                    out[it["id"]] = {"photo": scored[0][1], "how": "name+picture", "folder": scored[0][2]}
                    continue
            # still unsure: the one in the busiest folder
            best = max(cands, key=lambda c: (folders.get(c["folder"], 0), -c["id"]))
            out[it["id"]] = {"photo": best["id"], "how": "name (guess)", "folder": best["folder"]}
            continue
        if not folders:
            out[it["id"]] = {"photo": None}
            continue
        if pool is None:
            marks = ",".join("?" * len(folders))
            pool = [r["id"] for r in db.conn().execute(
                "SELECT id FROM photos WHERE COALESCE(missing,0)=0 AND folder IN (%s) LIMIT %d" % (marks, MAX_VISUAL), list(folders)).fetchall()]
        h = dhash(picture_bytes(it, token) or b"")
        if h is None:
            out[it["id"]] = {"photo": None}
            continue
        scored = sorted((bits(h, ph), pid) for pid in pool if (ph := photo_hash(pid, memo)) is not None)
        if scored and scored[0][0] <= SAME_PICTURE and (len(scored) == 1 or scored[1][0] - scored[0][0] >= SURE_GAP):
            out[it["id"]] = {"photo": scored[0][1], "how": "picture"}
        else:
            out[it["id"]] = {"photo": None}
    return out


# ------------------------------------------------------------------ layout
def _tile(p: dict) -> Tuple[int, int]:
    from .api import _tile as tile
    return tile(p.get("width"), p.get("height"), p.get("orientation"))


def layout(board: dict, matches: Dict[str, dict]) -> dict:
    """LPL board contents from the Miro items: {items, frames, shapes, unplaced, skipped}."""
    items = board["items"]
    geo = absolute(items)
    pics = [it for it in items if it.get("type") == "image"]
    pids = list({m["photo"] for m in matches.values() if m.get("photo")})
    photos = {}
    if pids:
        for r in db.conn().execute("SELECT id, width, height, orientation FROM photos WHERE id IN (%s)" % ",".join(str(int(p)) for p in pids)).fetchall():
            photos[r["id"]] = dict(r)
    # scale: the typical picture as wide as its LPL tile
    ratios = []
    for it in pics:
        m = matches.get(it["id"]) or {}
        g = geo.get(str(it["id"]))
        if m.get("photo") in photos and g and g["w"] > 0:
            ratios.append(_tile(photos[m["photo"]])[0] / g["w"])
    if not ratios:
        ratios = [150 / g["w"] for it in pics if (g := geo.get(str(it["id"]))) and g["w"] > 0] or [1.0]
    k = statistics.median(ratios)

    def box(iid):
        g = geo[str(iid)]
        return g["cx"] * k, g["cy"] * k, g["w"] * k, g["h"] * k

    out_items: List[dict] = []
    frames: List[dict] = []
    shapes: List[dict] = []
    ends: Dict[str, dict] = {}   # miro id -> arrow end
    seen: set = set()
    dup = 0
    skipped: Dict[str, int] = {}
    unplaced: List[dict] = []
    for it in items:
        t, iid = it.get("type"), str(it.get("id"))
        if iid not in geo:
            continue
        cx, cy, w, h = box(iid)
        if t == "frame":
            frames.append({"label": text_of((it.get("data") or {}).get("title"))[:120] or "Frame", "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h,
                           "color": nearest(str((it.get("style") or {}).get("fillColor") or ""), FRAME_COLORS, True), "area": w * h})
        elif t == "image":
            m = matches.get(iid) or {}
            pid = m.get("photo")
            if pid in photos and pid not in seen:
                seen.add(pid)
                tw, th = _tile(photos[pid])
                out_items.append({"id": pid, "x": cx - tw / 2, "y": cy - th / 2})
                ends[iid] = {"photo": pid, "x": cx, "y": cy}
            elif pid in photos:
                dup += 1
                ends[iid] = {"photo": pid, "x": cx, "y": cy}
            else:
                title = text_of((it.get("data") or {}).get("title")) or "(no title)"
                key = "m" + secrets.token_hex(5)
                shapes.append({"key": key, "kind": "note", "x": cx - 75, "y": cy - 50, "w": 150, "h": 100, "text": "Not found: " + title, "color": NOTE_GREY})
                ends[iid] = {"note": key, "x": cx, "y": cy}
                unplaced.append({"title": title, "miro_id": iid})
        elif t in ("sticky_note", "text", "shape", "card"):
            d = it.get("data") or {}
            txt = text_of(d.get("content") or d.get("title") or "")
            if t == "shape" and not txt:
                skipped["shape without text"] = skipped.get("shape without text", 0) + 1
                continue
            nw, nh = max(w, 120), max(h, 70)
            key = "m" + secrets.token_hex(5)
            color = note_color(it.get("style") or {}) if t in ("sticky_note", "shape", "card") else NOTE_GREY
            shapes.append({"key": key, "kind": "note", "x": cx - nw / 2, "y": cy - nh / 2, "w": nw, "h": nh, "text": txt, "color": color})
            ends[iid] = {"note": key, "x": cx, "y": cy}
        else:
            skipped[t or "?"] = skipped.get(t or "?", 0) + 1
    for cn in board.get("connectors") or []:
        a = ends.get(str((cn.get("startItem") or {}).get("id")))
        b = ends.get(str((cn.get("endItem") or {}).get("id")))
        if not a or not b:
            skipped["connector to something not imported"] = skipped.get("connector to something not imported", 0) + 1
            continue
        arr = {"key": "m" + secrets.token_hex(5), "kind": "arrow", "a": dict(a), "b": dict(b)}
        cap = " ".join(text_of(c.get("content")) for c in cn.get("captions") or [] if c.get("content")).strip()
        if cap:
            arr["text"] = cap
        shapes.append(arr)
    # start the board at (40, 40), whole pixels; outer frames first, so a frame inside another is drawn on top
    xs = [i["x"] for i in out_items] + [f["x"] for f in frames] + [s["x"] for s in shapes if s["kind"] == "note"]
    ys = [i["y"] for i in out_items] + [f["y"] for f in frames] + [s["y"] for s in shapes if s["kind"] == "note"]
    dx, dy = 40 - min(xs or [40]), 40 - min(ys or [40])
    r = round
    for i in out_items:
        i["x"], i["y"] = r(i["x"] + dx), r(i["y"] + dy)
    frames.sort(key=lambda f: -f.pop("area"))
    for f in frames:
        f["x"], f["y"], f["w"], f["h"] = r(f["x"] + dx), r(f["y"] + dy), max(60, r(f["w"])), max(40, r(f["h"]))
    for s in shapes:
        if s["kind"] == "note":
            s["x"], s["y"], s["w"], s["h"] = r(s["x"] + dx), r(s["y"] + dy), r(s["w"]), r(s["h"])
        else:
            for e in (s["a"], s["b"]):
                e["x"], e["y"] = r(e["x"] + dx), r(e["y"] + dy)
    return {"items": out_items, "frames": frames, "shapes": shapes, "unplaced": unplaced, "duplicates": dup, "skipped": skipped, "scale": k}


# ------------------------------------------------------------------ API
def _token(body: Dict[str, Any]) -> str:
    t = str(body.get("token") or "").strip()
    if t and body.get("remember", True):
        db.set_setting(TOKEN_KEY, t)
    t = t or db.get_setting(TOKEN_KEY)
    if not t:
        raise HTTPException(400, "A Miro access token is needed")
    return t


@router.get("/api/miro")
def miro_state():
    return {"has_token": bool(db.get_setting(TOKEN_KEY))}


@router.delete("/api/miro/token")
def miro_forget():
    with db.write_lock:
        db.conn().execute("DELETE FROM settings WHERE key=?", (TOKEN_KEY,))
        db.conn().commit()
    return {"ok": True}


@router.post("/api/miro/preview")
def miro_preview(body: Dict[str, Any] = Body(...)):
    bid = board_id(body.get("url"))
    token = _token(body)
    board = read_board(bid, token)
    pics = [it for it in board["items"] if it.get("type") == "image"]
    matches = match(pics, token)
    lay = layout(board, matches)
    how: Dict[str, int] = {}
    for m in matches.values():
        if m.get("photo"):
            how[m["how"]] = how.get(m["how"], 0) + 1
    key = secrets.token_urlsafe(12)
    now = time.time()
    with _cache_lock:
        for k in [k for k, v in _cache.items() if now - v["ts"] > CACHE_TTL]:
            del _cache[k]
        _cache[key] = {"ts": now, "layout": lay, "name": board["name"]}
    names = {}
    for f in lay["frames"]:
        names[f["label"]] = names.get(f["label"], 0) + 1
    taken = db.conn().execute("SELECT 1 FROM series WHERE name=?", (board["name"],)).fetchone()
    return {"key": key, "name": board["name"], "name_taken": bool(taken), "pictures": len(pics), "matched": len(lay["items"]), "how": how,
            "duplicates": lay["duplicates"], "unmatched": lay["unplaced"], "frames": len(lay["frames"]),
            "notes": sum(1 for s in lay["shapes"] if s["kind"] == "note") - len(lay["unplaced"]),
            "arrows": sum(1 for s in lay["shapes"] if s["kind"] == "arrow"), "skipped": lay["skipped"],
            "sample": [i["id"] for i in lay["items"][:12]]}


@router.post("/api/miro/import")
def miro_import(body: Dict[str, Any] = Body(...)):
    from . import api, series as series_mod
    with _cache_lock:
        got = _cache.get(str(body.get("key") or ""))
    if not got:
        raise HTTPException(410, "The board was read too long ago: read it again")
    lay = got["layout"]
    keep_missing = bool(body.get("missing_notes", True))
    r = series_mod.create_series({"name": body.get("name") or got["name"]})
    sid = r["id"]
    shapes = [s for s in lay["shapes"] if keep_missing or not s.get("text", "").startswith("Not found: ") or s.get("kind") != "note"]
    keys = {s["key"] for s in shapes if s["kind"] == "note"}
    shapes = [s for s in shapes if s["kind"] != "arrow" or all(e.get("note") is None or e["note"] in keys for e in (s["a"], s["b"]))]
    with db.write_lock:
        c = db.conn()
        c.execute("DELETE FROM series_frames WHERE series_id=?", (sid,))  # the empty "01" a new series starts with
        for f in lay["frames"]:
            c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,0,?,'')",
                      (sid, f["label"], f["x"], f["y"], f["w"], f["h"], f["color"]))
        for it in lay["items"]:
            c.execute("INSERT OR IGNORE INTO series_items(series_id,photo_id,x,y,ord) VALUES(?,?,?,?,NULL)", (sid, it["id"], it["x"], it["y"]))
        if shapes:
            c.execute("INSERT INTO series_shapes(series_id,data) VALUES(?,?) ON CONFLICT(series_id) DO UPDATE SET data=excluded.data",
                      (sid, json.dumps(shapes, ensure_ascii=False)))
        series_mod.sync_series(c, sid)
        c.commit()
    ids = [it["id"] for it in lay["items"]]
    if ids and config.write_series_tags:
        s = api._series(sid)
        api.set_meta({"ids": ids, "attach": [series_mod.tag_of(s)]})
    with _cache_lock:
        _cache.pop(str(body.get("key")), None)
    return {"id": sid, "photos": len(ids), "frames": len(lay["frames"]), "shapes": len(shapes)}
