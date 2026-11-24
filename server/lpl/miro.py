# SPDX-License-Identifier: GPL-3.0-or-later
"""Import from Miro: a Miro board becomes a new LPL series laid out the same way.

Flow (Series view → "Import from Miro…"):
  1. POST /api/miro/preview {url, token?, remember?, scope?} reads the board through Miro's REST API (v2, a personal access
     token with boards:read) and matches its pictures to photos in the library. Nothing is written. The answer says
     how many matched and how, lists what did not, and carries a `key` for step 2.
  2. POST /api/miro/import {key, name} creates the series: photos where they were, frames (nested ones too, Miro's
     colours mapped to LPL's), sticky notes, text and shapes with text as notes, connectors as arrows. A picture
     with no photo leaves a grey note "Not found: <title>" where it was, so the layout keeps its gaps.

Matching a picture. Miro's REST API gives no title (and no size) for pictures dropped onto a board, although
Miro shows their file names; so:
  - by name, when there is one: the API title, or the file name in the link to the original (when Miro's storage
    link gives it away). The name without extension against the photos' file names without extension,
    case-insensitive (DSCF4821.jpg finds DSCF4821.RAF); export suffixes like "_01", " (2)", "-edit" tried off.
  - by picture otherwise (and to tell apart several photos with one name): Miro's preview against LPL's thumbnails,
    a 256-bit difference hash of the contrast-stretched grey image. LPL's side is hashed at several centre crops,
    because an exported edit is often cropped; the hashes are kept in table photo_phash (per preview version), so
    only the first run over a library is slow. Where to look: the folder / dates the user gives, else the folders
    the name matches come from, else the whole library. Close and clearly closer than the next photo: placed.
    A bit less sure: placed but listed to check. Unsure: not placed, listed with its best guess.
The preview runs in a thread (POST starts it, GET /api/miro/preview/{key} reports progress, then the result).

Places: Miro gives item centres (pictures dropped onto a board have no size: the spacing between neighbours
stands in for picture + gap), relative to the board or to the parent frame's top-left; LPL keeps top-left
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


def _resource(item: dict, token: str, fmt: str) -> Optional[str]:
    """The real address of a picture's file (Miro answers a little JSON with it when asked with redirect=false)."""
    url = (item.get("data") or {}).get("imageUrl")
    if not url:
        return None
    url = re.sub(r"format=\w+", "format=" + fmt, url) if "format=" in url else url + ("&" if "?" in url else "?") + "format=" + fmt
    url = re.sub(r"redirect=\w+", "redirect=false", url) if "redirect=" in url else url + "&redirect=false"
    try:
        r = fetch(url, token)
        return r.get("url") if isinstance(r, dict) else None
    except Exception as e:
        log.info("miro resource %s: %s", item.get("id"), e)
        return None


NAMEISH = re.compile(r"\.(jpe?g|png|tiff?|webp|heic|avif)$", re.I)


def original_name(item: dict, token: str) -> str:
    """The uploaded file's name, when the address of the original gives it away (a file name in the path, or a
    content-disposition in a signed link). Miro's API has no title for pictures dropped onto a board."""
    link = _resource(item, token, "original")
    if not link:
        return ""
    u = urllib.parse.urlparse(link)
    for k, vs in urllib.parse.parse_qs(u.query).items():
        if "disposition" in k.lower():
            m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", " ".join(vs), re.I)
            if m and NAMEISH.search(urllib.parse.unquote(m.group(1))):
                return urllib.parse.unquote(m.group(1))
    base = urllib.parse.unquote(u.path.rsplit("/", 1)[-1])
    if NAMEISH.search(base) and not re.fullmatch(r"[0-9a-f\-]{16,}\.\w+", base, re.I):
        return base
    return ""


def image_bytes(item: dict, token: str) -> Optional[bytes]:
    """Miro's preview of a picture."""
    link = _resource(item, token, "preview")
    if not link:
        return None
    try:
        with urllib.request.urlopen(link, timeout=30) as r:
            return r.read()
    except Exception as e:
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


# pictures: a 256-bit difference hash of the grey image (contrast stretched, so exposure and colour edits change
# little) plus the aspect ratio. LPL's hashes are kept in photo_phash, per preview version.
def phash(data_or_img) -> Optional[Tuple[bytes, float]]:
    from PIL import Image, ImageOps
    try:
        img = data_or_img if isinstance(data_or_img, Image.Image) else Image.open(io.BytesIO(data_or_img))
        img.draft("L", (128, 128))
        img = ImageOps.exif_transpose(img)
        ar = img.width / max(1, img.height)
        g = ImageOps.autocontrast(img.convert("L").resize((17, 16), Image.LANCZOS), cutoff=1)
    except Exception:
        return None
    px = list(g.getdata())
    v = 0
    for row in range(16):
        for col in range(16):
            v = (v << 1) | (px[row * 17 + col] > px[row * 17 + col + 1])
    return v.to_bytes(32, "big"), ar


CROPS = (0, .03, .06, .09, .12, .16)   # an edit is often cropped: LPL's side is hashed at several centre crops


def phash_crops(data: bytes) -> Optional[Tuple[bytes, float]]:
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        img.draft("RGB", (256, 256))
        img = ImageOps.exif_transpose(img).convert("L")
    except Exception:
        return None
    ar = img.width / max(1, img.height)
    parts = []
    for c in CROPS:
        w, h = img.size
        hh = phash(img.crop((round(w * c), round(h * c), round(w * (1 - c)), round(h * (1 - c)))))
        if not hh:
            return None
        parts.append(hh[0])
    return b"".join(parts), ar


def _hash_table(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS photo_phash (photo_id INTEGER PRIMARY KEY, ver INTEGER, h BLOB, ar REAL)")


def pool_hashes(ids: List[int], progress: Callable[[int, int], None] = lambda d, t: None):
    """(ids, hashes as an N x 32 uint8 array, aspect ratios) for these photos, computing what is not cached."""
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor
    c = db.conn()
    with db.write_lock:
        _hash_table(c)
        c.commit()
    ver = {}
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        for r in c.execute("SELECT id, preview_ver FROM photos WHERE id IN (%s)" % ",".join(str(int(x)) for x in part)).fetchall():
            ver[r["id"]] = r["preview_ver"] or 0
    have: Dict[int, Tuple[bytes, float]] = {}
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        for r in c.execute("SELECT photo_id, ver, h, ar FROM photo_phash WHERE photo_id IN (%s)" % ",".join(str(int(x)) for x in part)).fetchall():
            if ver.get(r["photo_id"]) == r["ver"] and r["h"] and len(r["h"]) == 32 * len(CROPS):
                have[r["photo_id"]] = (bytes(r["h"]), r["ar"])
    todo = [i for i in ids if i in ver and i not in have]

    def one(pid):
        t, _ = media.preview_paths(pid)
        try:
            return pid, phash_crops(t.read_bytes()) if t.exists() else None
        except OSError:
            return pid, None

    done = len(ids) - len(todo)
    progress(done, len(ids))
    batch = []
    with ThreadPoolExecutor(4) as ex:
        for pid, h in ex.map(one, todo):
            done += 1
            if h:
                have[pid] = h
                batch.append((pid, ver[pid], h[0], h[1]))
            if len(batch) >= 500 or (done == len(ids) and batch):
                with db.write_lock:
                    db.conn().executemany("INSERT OR REPLACE INTO photo_phash(photo_id, ver, h, ar) VALUES(?,?,?,?)", batch)
                    db.conn().commit()
                batch = []
            if done % 200 == 0:
                progress(done, len(ids))
    if batch:
        with db.write_lock:
            db.conn().executemany("INSERT OR REPLACE INTO photo_phash(photo_id, ver, h, ar) VALUES(?,?,?,?)", batch)
            db.conn().commit()
    progress(len(ids), len(ids))
    got = [i for i in ids if i in have]
    H = np.frombuffer(b"".join(have[i][0] for i in got), dtype=np.uint8).reshape(-1, len(CROPS), 32) if got else np.zeros((0, len(CROPS), 32), np.uint8)
    AR = np.array([have[i][1] for i in got], dtype=float)
    return got, H, AR


SURE, LIKELY, MARGIN_SURE, MARGIN_LIKELY = 50, 76, 16, 10   # bits of 256 (unrelated photos: about 110-130)


def np_hashes(hs: List[Tuple[bytes, float]]):
    """Single hashes as the N x crops x 32 array closest() takes (the same hash for every crop)."""
    import numpy as np
    return np.frombuffer(b"".join(h[0] * len(CROPS) for h in hs), dtype=np.uint8).reshape(-1, len(CROPS), 32)


_POP = __import__("numpy").array([bin(i).count("1") for i in range(256)], dtype=__import__("numpy").uint8)


def closest(h: Tuple[bytes, float], ids: List[int], H, AR) -> List[Tuple[int, int]]:
    """(distance, photo id) of the best three; a different aspect ratio (a crop) counts against a photo."""
    import numpy as np
    if not ids:
        return []
    q = np.frombuffer(h[0], dtype=np.uint8)
    d = _POP[np.bitwise_xor(H, q)].sum(axis=2, dtype=np.int32).min(axis=1).astype(float)
    d += np.where(np.abs(np.log(AR / h[1])) > 0.25, 16, 0)  # portrait against landscape
    best = np.argsort(d)[:3]
    return [(int(d[i]), ids[int(i)]) for i in best]


def scope_ids(scope: Dict[str, Any]) -> List[int]:
    """The library photos to compare pictures with: all, or those in folders whose path contains a text and/or
    taken between two dates."""
    where, args = ["COALESCE(missing,0)=0"], []
    t = str((scope or {}).get("folder") or "").strip()
    if t:
        where.append("folder LIKE ? COLLATE NOCASE")
        args.append("%" + t.replace("%", "") + "%")
    if (scope or {}).get("date_from"):
        where.append("taken >= ?")
        args.append(str(scope["date_from"]))
    if (scope or {}).get("date_to"):
        where.append("taken < ?")
        args.append(str(scope["date_to"]) + "T99")
    return [r["id"] for r in db.conn().execute("SELECT id FROM photos WHERE %s" % " AND ".join(where), args).fetchall()]


def match(pictures: List[dict], token: str, scope: Optional[Dict[str, Any]] = None,
          progress: Callable[[str, int, int], None] = lambda s, d, t: None, picture_bytes: Callable = None,
          names: Callable = None) -> Tuple[Dict[str, dict], Dict[str, bytes]]:
    """miro id -> {photo, how, dist?, guess?}. how: name / name+picture / picture / likely (placed, worth a look);
    photo None with a guess: not placed, but there is a candidate. Also the pictures downloaded, for the review."""
    picture_bytes = picture_bytes or image_bytes
    names = names or original_name
    from concurrent.futures import ThreadPoolExecutor
    idx = photo_index()
    titles = {it["id"]: text_of((it.get("data") or {}).get("title") or "") for it in pictures}
    untitled = [it for it in pictures if not titles[it["id"]]]
    if untitled and names(untitled[0], token):  # the file names can be had from the originals' links
        progress("names", 0, len(untitled))
        with ThreadPoolExecutor(4) as ex:
            for k, (it, n) in enumerate(zip(untitled, ex.map(lambda it: names(it, token), untitled))):
                titles[it["id"]] = n
                if k % 20 == 0:
                    progress("names", k, len(untitled))
    out: Dict[str, dict] = {}
    pending: List[Tuple[dict, List[dict]]] = []
    for it in pictures:
        cands: List[dict] = []
        for s in stems(titles[it["id"]]) if titles[it["id"]] else []:
            cands = idx.get(s) or []
            if cands:
                break
        raws = [c for c in cands if (c["ext"] or "").lower().lstrip(".") in RAW_EXTS]
        cands = raws or cands
        if len(cands) == 1:
            out[it["id"]] = {"photo": cands[0]["id"], "how": "name", "folder": cands[0]["folder"]}
        else:
            pending.append((it, cands))
    folders: Dict[str, int] = {}
    for m in out.values():
        folders[m["folder"]] = folders.get(m["folder"], 0) + 1
    still = []
    for it, cands in pending:
        near = [c for c in cands if c["folder"] in folders]
        if len(near) == 1:
            out[it["id"]] = {"photo": near[0]["id"], "how": "name"}
        else:
            still.append((it, near or cands))
    pics: Dict[str, bytes] = {}
    if not still:
        return out, pics
    # the rest by picture: Miro's previews against LPL's thumbnails
    if scope and any(scope.get(k) for k in ("folder", "date_from", "date_to")):
        pool = scope_ids(scope)
    elif folders:
        pool = [r["id"] for r in db.conn().execute("SELECT id FROM photos WHERE COALESCE(missing,0)=0 AND folder IN (%s)" % ",".join("?" * len(folders)), list(folders)).fetchall()]
    else:
        pool = scope_ids({})
    ids, H, AR = pool_hashes(pool, lambda d, t: progress("library", d, t))
    progress("pictures", 0, len(still))
    with ThreadPoolExecutor(6) as ex:
        got = list(ex.map(lambda x: picture_bytes(x[0], token), still))
    for k, ((it, cands), data) in enumerate(zip(still, got)):
        if k % 20 == 0:
            progress("pictures", k, len(still))
        h = phash(data) if data else None
        if h is None:
            out[it["id"]] = {"photo": None}
            continue
        pics[it["id"]] = data
        if cands:  # several photos with this name: the one that looks like it
            cid = [c["id"] for c in cands]
            sub = [i for i, x in enumerate(ids) if x in set(cid)]
            best = closest(h, [ids[i] for i in sub], H[sub], AR[sub]) if sub else []
            if best:
                out[it["id"]] = {"photo": best[0][1], "how": "name+picture", "dist": best[0][0]}
            else:
                out[it["id"]] = {"photo": cands[0]["id"], "how": "likely", "dist": None}
            continue
        best = closest(h, ids, H, AR)
        if not best:
            out[it["id"]] = {"photo": None}
            continue
        d0 = best[0][0]
        gap = (best[1][0] - d0) if len(best) > 1 else 99
        if d0 <= SURE and gap >= MARGIN_SURE:
            out[it["id"]] = {"photo": best[0][1], "how": "picture", "dist": d0}
        elif d0 <= LIKELY and gap >= MARGIN_LIKELY:
            out[it["id"]] = {"photo": best[0][1], "how": "likely", "dist": d0}
        else:
            out[it["id"]] = {"photo": None, "guess": best[0][1], "dist": d0}
    # the same photo found for two pictures: keep the closer one sure, the other only "likely"
    taken: Dict[int, str] = {}
    for mid, m in out.items():
        p = m.get("photo")
        if p is None or m.get("how") == "name":
            continue
        if p in taken:
            other = out[taken[p]]
            loser = mid if (m.get("dist") or 0) >= (other.get("dist") or 0) else taken[p]
            out[loser]["how"] = "likely"
            if loser == taken[p]:
                taken[p] = mid
        else:
            taken[p] = mid
    progress("pictures", len(still), len(still))
    return out, pics


# ------------------------------------------------------------------ layout
def _tile(p: dict) -> Tuple[int, int]:
    from .api import _tile as tile
    return tile(p.get("width"), p.get("height"), p.get("orientation"))


def spacing(gs: List[dict]) -> float:
    """The usual distance from a picture's centre to its nearest neighbour's (sideways neighbours count most)."""
    pts = [(g["cx"], g["cy"]) for g in gs]
    if len(pts) < 2:
        return 165.0
    nn = []
    for i, (x, y) in enumerate(pts):
        best = None
        for j, (x2, y2) in enumerate(pts):
            if i != j:
                d = ((x - x2) ** 2 + (2 * (y - y2)) ** 2) ** .5  # rows: the vertical gap counts double, so a row neighbour wins
                if best is None or d < best[0]:
                    best = (d, abs(x - x2), abs(y - y2))
        nn.append(max(best[1], best[2] * 1.4))
    return max(1.0, statistics.median(nn))


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
    sized = sum(1 for it in pics if (g := geo.get(str(it["id"]))) and g["w"] > 0)
    if ratios and sized * 2 >= len(pics):
        k = statistics.median(ratios)
    else:  # Miro gives no sizes for pictures dropped onto a board: neighbours' spacing is about a picture and a gap
        k = 165 / spacing([geo[str(it["id"])] for it in pics if str(it["id"]) in geo]) if pics else 1.0

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


def _report(job: dict) -> dict:
    board, matches = job["board"], job["matches"]
    lay = layout(board, matches)
    pics = [it for it in board["items"] if it.get("type") == "image"]
    how: Dict[str, int] = {}
    for m in matches.values():
        if m.get("photo"):
            how[m["how"]] = how.get(m["how"], 0) + 1
    check = [{"miro_id": mid, "photo": m["photo"], "dist": m.get("dist"), "placed": True}
             for mid, m in matches.items() if m.get("photo") and m.get("how") == "likely"]
    check += [{"miro_id": mid, "photo": m["guess"], "dist": m.get("dist"), "placed": False}
              for mid, m in matches.items() if not m.get("photo") and m.get("guess")]
    check.sort(key=lambda x: (x["placed"], -(x["dist"] or 0)))
    taken = db.conn().execute("SELECT 1 FROM series WHERE name=?", (board["name"],)).fetchone()
    return {"state": "done", "key": job["key"], "name": board["name"], "name_taken": bool(taken), "pictures": len(pics), "matched": len(lay["items"]),
            "how": how, "duplicates": lay["duplicates"], "unmatched": lay["unplaced"], "frames": len(lay["frames"]),
            "notes": sum(1 for x in lay["shapes"] if x["kind"] == "note") - len(lay["unplaced"]),
            "arrows": sum(1 for x in lay["shapes"] if x["kind"] == "arrow"), "skipped": lay["skipped"],
            "sample": [i["id"] for i in lay["items"][:12]], "check": check[:400]}


def _run(job: dict, bid: str, token: str, scope: Dict[str, Any]) -> None:
    def prog(stage, done, total):
        job.update(stage=stage, done=done, total=total)
    try:
        prog("board", 0, 0)
        board = read_board(bid, token)
        pics = [it for it in board["items"] if it.get("type") == "image"]
        matches, data = match(pics, token, scope, prog)
        job.update(board=board, matches=matches, pics=data, state="done")
    except HTTPException as e:
        job.update(state="error", error=e.detail)
    except Exception as e:  # shown in the dialog
        log.exception("miro import")
        job.update(state="error", error=str(e))


def _job(key: str) -> dict:
    with _cache_lock:
        job = _cache.get(key)
    if not job:
        raise HTTPException(410, "The board was read too long ago: read it again")
    return job


@router.post("/api/miro/preview")
def miro_preview(body: Dict[str, Any] = Body(...)):
    """Starts reading the board and matching its pictures; GET /api/miro/preview/{key} says how far it got."""
    bid = board_id(body.get("url"))
    token = _token(body)
    key = secrets.token_urlsafe(12)
    now = time.time()
    job = {"key": key, "ts": now, "state": "running", "stage": "board", "done": 0, "total": 0}
    with _cache_lock:
        for k in [k for k, v in _cache.items() if now - v["ts"] > CACHE_TTL]:
            del _cache[k]
        _cache[key] = job
    t = threading.Thread(target=_run, args=(job, bid, token, body.get("scope") or {}), daemon=True)
    t.start()
    if body.get("wait"):
        t.join()
    return miro_progress(key)


@router.get("/api/miro/preview/{key}")
def miro_progress(key: str):
    job = _job(key)
    if job["state"] == "error":
        raise HTTPException(400, job.get("error") or "Reading the board failed")
    if job["state"] != "done":
        return {"state": "running", "key": key, "stage": job.get("stage"), "done": job.get("done", 0), "total": job.get("total", 0)}
    return _report(job)


@router.get("/api/miro/pic/{key}/{mid}")
def miro_pic(key: str, mid: str):
    """Miro's preview of a picture, for the review pairs in the dialog."""
    from fastapi.responses import Response
    data = (_job(key).get("pics") or {}).get(mid)
    if not data:
        raise HTTPException(404, "no picture")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=1800"})


@router.post("/api/miro/import")
def miro_import(body: Dict[str, Any] = Body(...)):
    """key, name, missing_notes, use: {miro id: photo id or null} (the user's answers in the review)."""
    from . import api, series as series_mod
    job = _job(str(body.get("key") or ""))
    if job.get("state") != "done":
        raise HTTPException(409, "Still reading the board")
    matches = {k: dict(v) for k, v in job["matches"].items()}
    for mid, pid in (body.get("use") or {}).items():
        if mid in matches:
            matches[mid] = {"photo": int(pid), "how": "checked"} if pid else {"photo": None}
    lay = layout(job["board"], matches)
    keep_missing = bool(body.get("missing_notes", True))
    r = series_mod.create_series({"name": body.get("name") or job["board"]["name"]})
    sid = r["id"]
    shapes = [x for x in lay["shapes"] if keep_missing or x.get("kind") != "note" or not x.get("text", "").startswith("Not found: ")]
    keys = {x["key"] for x in shapes if x["kind"] == "note"}
    shapes = [x for x in shapes if x["kind"] != "arrow" or all(e.get("note") is None or e["note"] in keys for e in (x["a"], x["b"]))]
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
