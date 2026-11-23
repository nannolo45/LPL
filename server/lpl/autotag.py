# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-tagging: LPL tags photos by itself, with rules the user sets in Settings > Auto-tagging. The first rule is
"Black & white". Auto tags are ordinary tags (default under "auto|"), written to the DB and the XMP sidecar the same
way as any tag set in LPL, so darktable and the filters see them, and nothing else writes them behind LPL's back.

How the B&W rule decides, per photo (each source can be switched off):
  1. darktable's rendered preview: the JPEG the bridge sends back after an edit (photos.preview_src = 'rendered').
     It is used only while the edit it shows is still the current one (rendered_hist = the history at that time).
  2. the edit history in the XMP: only the latest state of each module instance up to history_end counts. B&W when
     "monochrome" is on, or "color calibration" (channelmixerrgb) has its gray tab in use. Other ways to make a B&W
     edit (color balance rgb at -100 % chroma, LUTs, ...) can not be read from the history: those need source 1.
  3. the camera's preview (the JPEG inside the raw, or the image itself for JPEG/TIFF/PNG) for photos with no edit:
     it shows what was shot in B&W in camera.
Pixels are measured on LPL's own small preview, in YCbCr: neutral when the colour (Cb, Cr) is nearly zero; toned
(sepia, split-toning) when the colour depends only on brightness. Near the limit the photo is "unsure" and waits for
the user (Settings > Auto-tagging > Preview run). A user's correction is kept and never changed by a later run.

Tables: autotag_results (one row per photo and rule: the last verdict and why) and autotag_overrides (the user's
corrections). Settings: the JSON under settings key "autotag".
"""
import base64
import hashlib
import json
import logging
import struct
import threading
import time
import zlib
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Body, HTTPException

from . import db, media, scanner, xmp
from .config import config

log = logging.getLogger("lpl.autotag")
router = APIRouter()

RULE = "bnw"
# bumped when the way a photo is judged changes, so verdicts stored by an older LPL are looked at again
ENGINE = 3
# words in a darktable style name or a LUT file name that mean black & white (lower case)
BNW_WORDS = ("black and white", "black & white", "black&white", "b&w", "b & w", "bw ", " bw", "monochrom", "mono ", " mono",
             "acros", "neopan", "ilford", "delta 100", "delta 400", "delta 3200", "hp5", "fp4", "pan f", "panf", "tri-x", "trix",
             "t-max", "tmax", "noir", "grayscale", "greyscale", "schwarzweiss", "schwarz-weiss", "bianco e nero")
VERDICTS = ("bnw", "colour", "unsure", "none")
DEFAULTS: Dict[str, Any] = {
    "auto": True,  # check new and changed photos by themselves (only once the rule is switched on)
    "bnw": {
        "enabled": False,
        "tag_bnw": "auto|look|bnw",
        "tag_toned": "auto|look|toned",
        "toned": "bnw+toned",       # sepia / split-toned count as: "bnw+toned" | "bnw" | "colour"
        "use_rendered": True,
        "use_history": True,
        "use_camera": True,
        "disagree_review": True,    # sources disagree: unsure instead of a tag
        "camera_over_edit": True,   # edited, no darktable render to look at, no B&W module found, camera shot B&W: B&W
        "strictness": 50,           # 0 strict (any tint is colour) .. 100 lenient
        "band": 30,                 # how wide "unsure" is above the limit, 0..100
        "folders": [],              # [] = whole library, else these library folders (with their subfolders)
        "remove_stale": True,       # take the rule's tags off a photo that no longer matches
        "managed": [],              # tag names the rule wrote before a rename (cleaned up too)
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS autotag_results (
    photo_id INTEGER NOT NULL,
    rule TEXT NOT NULL,
    verdict TEXT NOT NULL,
    toned INTEGER DEFAULT 0,
    source TEXT,
    score REAL,
    detail TEXT,
    sig TEXT,
    applied TEXT DEFAULT '[]',
    checked REAL,
    PRIMARY KEY (photo_id, rule)
);
CREATE INDEX IF NOT EXISTS idx_autotag_verdict ON autotag_results(rule, verdict);
CREATE TABLE IF NOT EXISTS autotag_overrides (
    photo_id INTEGER NOT NULL,
    rule TEXT NOT NULL,
    verdict TEXT NOT NULL,
    toned INTEGER DEFAULT 0,
    ts REAL,
    PRIMARY KEY (photo_id, rule)
);
"""


def migrate(c) -> None:
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(photos)").fetchall()}
    # where the preview came from: '' / 'embedded' (camera JPEG or the image itself) or 'rendered' (darktable, via the
    # bridge); when it came; and the edit it showed (a hash of the history; NULL = not known yet)
    for col, decl in (("preview_src", "TEXT DEFAULT ''"), ("preview_ts", "REAL"), ("rendered_hist", "TEXT")):
        if col not in cols:
            c.execute("ALTER TABLE photos ADD COLUMN %s %s" % (col, decl))


# ---------------------------------------------------------------- settings
def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v)
        elif k in base:
            out[k] = v
    return out


def get_settings() -> dict:
    try:
        stored = json.loads(db.get_setting("autotag", "") or "{}")
    except ValueError:
        stored = {}
    return _merge(DEFAULTS, stored if isinstance(stored, dict) else {})


def _clean_tag(t: Any, fallback: str) -> str:
    t = "|".join(p.strip() for p in str(t or "").split("|") if p.strip())
    return t or fallback


def save_settings(new: dict) -> dict:
    old = get_settings()
    st = _merge(old, new or {})
    b = st["bnw"]
    b["tag_bnw"] = _clean_tag(b["tag_bnw"], DEFAULTS["bnw"]["tag_bnw"])
    b["tag_toned"] = _clean_tag(b["tag_toned"], DEFAULTS["bnw"]["tag_toned"])
    if b["toned"] not in ("bnw+toned", "bnw", "colour"):
        b["toned"] = "bnw+toned"
    b["strictness"] = max(0, min(100, int(b.get("strictness") or 0)))
    b["band"] = max(0, min(100, int(b.get("band") or 0)))
    b["folders"] = [str(f).strip("/") for f in b.get("folders") or [] if str(f).strip("/")]
    # a renamed tag: the old name is still on photos; remember it so the rule can take it off again
    managed = set(b.get("managed") or [])
    for k in ("tag_bnw", "tag_toned"):
        if old["bnw"][k] != b[k]:
            managed.add(old["bnw"][k])
    managed -= {b["tag_bnw"], b["tag_toned"]}
    b["managed"] = sorted(managed)
    db.set_setting("autotag", json.dumps(st))
    return st


def _sig_settings(b: dict) -> str:
    keys = ("use_rendered", "use_history", "use_camera", "disagree_review", "camera_over_edit", "strictness", "band")
    return hashlib.sha1(json.dumps([ENGINE] + [b[k] for k in keys]).encode()).hexdigest()[:10]


# ---------------------------------------------------------------- the edit history
def decode_params(v: Optional[str]) -> Optional[bytes]:
    """darktable:params: hex of the module's settings struct, or "gz" + two digits + base64 of the zlib-packed struct."""
    if not v:
        return None
    try:
        if v.startswith("gz"):
            return zlib.decompress(base64.b64decode(v[4:]))
        return bytes.fromhex(v)
    except (ValueError, zlib.error):
        return None


def _gray_in_use(params: Optional[bytes], version: int) -> Optional[bool]:
    """color calibration (channelmixerrgb): every version so far (1..4; darktable 5.x writes 4) starts with red[4],
    green[4], blue[4], saturation[4], lightness[4], grey[4], all floats, and only appends after that. darktable
    applies the gray tab when any of grey[0..2] is not 0. None: the settings could not be read."""
    if params is None or len(params) < 92:
        return None
    g = struct.unpack_from("<3f", params, 80)
    return any(abs(x) > 1e-6 for x in g)


def _mixer_gray(params: Optional[bytes]) -> bool:
    """The old channel mixer (channelmixer, used by t3mujinpack's B&W films): red[7], green[7], blue[7]; index 6 of
    each is the gray output, and any of them not 0 turns the picture monochrome."""
    if params is None or len(params) < 84:
        return False
    return any(abs(struct.unpack_from("<f", params, off)[0]) > 1e-6 for off in (24, 52, 80))


def _bnw_name(text: str) -> bool:
    t = " %s " % (text or "").lower().replace("_", " ").replace("-", " ").replace("/", " ").replace("\\", " ").replace(".", " ")
    return any(w.replace("-", " ") in t for w in BNW_WORDS)


def _lut_name(params: Optional[bytes]) -> str:
    """The LUT file a lut3d instance uses (a text field at the start of its settings)."""
    if not params:
        return ""
    return params[:512].split(b"\0", 1)[0].decode("utf-8", "replace")


def _chroma_off(params: Optional[bytes]) -> bool:
    """color balance rgb (every version shares the start of its settings): global chroma at -100% takes all colour
    out. It is the 18th float (shadows/midtones/highlights/global Y,C,H, three weights, then chroma shadows,
    highlights, global)."""
    if params is None or len(params) < 72:
        return False
    return struct.unpack_from("<f", params, 68)[0] <= -0.99


def read_history(desc) -> Dict[str, Any]:
    """The module instances in the edit (latest state of each, up to history_end), what that says about B&W, and a
    hash of the edit. bnw: True (monochrome, or color calibration's gray tab), False (edited, nothing B&W found),
    None (no edit). why: the module that decided."""
    el = desc.find(xmp.q("darktable", "history"))
    items = list(el.iter(xmp.q("rdf", "li"))) if el is not None else []
    end = xmp._get_attr_or_child(desc, "darktable", "history_end")
    end = xmp._int(end, len(items)) if end is not None else len(items)
    state: Dict[tuple, dict] = {}
    for pos, li in enumerate(items):
        op = xmp._item_value(li, "operation")
        if not op:
            continue
        num = xmp._int(xmp._item_value(li, "num"), pos)
        if num >= end:
            continue
        prio = xmp._int(xmp._item_value(li, "multi_priority"), 0)
        state[(op, prio)] = {"op": op, "prio": prio, "enabled": xmp._item_value(li, "enabled") not in ("0", "false", "False"),
                             "version": xmp._int(xmp._item_value(li, "modversion"), 0), "params": xmp._item_value(li, "params") or "",
                             "name": xmp._item_value(li, "multi_name") or ""}
    if not state:
        return {"bnw": None, "why": "", "sig": "", "unknown": False}
    sig = hashlib.sha1(json.dumps(sorted([m["op"], m["prio"], m["enabled"], m["params"]] for m in state.values())).encode()).hexdigest()[:16]
    bnw, why, unknown = False, "", False
    for m in state.values():
        if not m["enabled"]:
            continue
        if m["op"] == "monochrome":
            bnw, why = True, "monochrome"
            break
        if m["op"] == "channelmixerrgb":
            g = _gray_in_use(decode_params(m["params"]), m["version"])
            if g:
                bnw, why = True, "color calibration (gray tab)"
                break
            if g is None:
                unknown = True
        if m["op"] == "colorbalancergb" and _chroma_off(decode_params(m["params"])):
            bnw, why = True, "color balance rgb (chroma -100%)"
            break
        if m["op"] == "channelmixer" and _mixer_gray(decode_params(m["params"])):
            bnw, why = True, "channel mixer (gray)"
            break
        if m["op"] == "lut3d":
            lut = _lut_name(decode_params(m["params"]))
            if _bnw_name(lut):
                bnw, why = True, "LUT 3D (%s)" % lut.replace("\\", "/").split("/")[-1]
                break
        if m["name"] and _bnw_name(m["name"]):
            bnw, why = True, "%s instance \"%s\"" % (m["op"], m["name"])
            break
    if not bnw:  # a darktable style applied to the photo leaves a tag darktable|style|<group>|<name>
        for t in xmp._list_items(desc, "lr", "hierarchicalSubject") + xmp._list_items(desc, "dc", "subject"):
            if t.startswith("darktable|style|") and _bnw_name(t[16:]):
                bnw, why = True, "style " + t[16:].replace("|", " › ")
                break
    if bnw:
        unknown = False
    return {"bnw": bnw, "why": why, "sig": sig, "unknown": unknown}


def history_of(relpath: str) -> Dict[str, Any]:
    try:
        img = scanner.abs_of(relpath)
    except ValueError:
        return {"bnw": None, "why": "", "sig": "", "unknown": False}
    for p in (xmp.sidecar_path(img), xmp.alt_sidecar_path(img)):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        try:
            return read_history(xmp._description(xmp._parse_bytes(data)))
        except Exception:
            break
    return {"bnw": None, "why": "", "sig": "", "unknown": False}


# ---------------------------------------------------------------- pixels
def measure(img) -> Optional[Dict[str, float]]:
    """Colour in a picture, in 8-bit Cb/Cr units (0 = grey). neutral: how far most pixels are from grey (95th
    percentile); resid: the same after taking out the colour each brightness has on average (a toned picture has
    one colour per brightness, so this stays small); tint: the strongest colour of a brightness."""
    import numpy as np
    im = img.convert("RGB")
    im.thumbnail((200, 200))
    a = np.asarray(im.convert("YCbCr"), dtype=np.float32).reshape(-1, 3)
    if not len(a):
        return None
    y, cb, cr = a[:, 0], a[:, 1] - 128.0, a[:, 2] - 128.0
    mid = (y > 16) & (y < 245)  # clipped shadows and highlights carry no colour worth judging
    if mid.sum() >= max(50, len(a) * .05):
        y, cb, cr = y[mid], cb[mid], cr[mid]
    c = np.hypot(cb, cr)
    neutral = float(np.percentile(c, 95))
    bins = np.minimum((y / 256.0 * 12).astype(int), 11)
    mcb, mcr = np.zeros(12), np.zeros(12)
    tint = 0.0
    for k in range(12):
        sel = bins == k
        n = int(sel.sum())
        if n:
            mcb[k], mcr[k] = cb[sel].mean(), cr[sel].mean()
            if n >= len(c) * .02:
                tint = max(tint, float(np.hypot(mcb[k], mcr[k])))
    resid = float(np.percentile(np.hypot(cb - mcb[bins], cr - mcr[bins]), 95))
    return {"neutral": round(neutral, 2), "resid": round(resid, 2), "tint": round(tint, 2)}


def judge(m: Dict[str, float], b: dict) -> Dict[str, Any]:
    """bnw (toned or not), colour, or unsure, from measure()'s numbers and the strictness and band settings."""
    limit = 3.0 + b["strictness"] * 0.07          # 3 .. 10 Cb/Cr units
    high = limit * (1 + b["band"] / 100.0)          # up to twice the limit: unsure
    if m["neutral"] <= limit:
        return {"verdict": "bnw", "toned": 0, "score": m["neutral"]}
    if m["resid"] <= limit and m["tint"] <= 45:
        return {"verdict": "bnw", "toned": 1, "score": m["resid"]}
    best = min(m["neutral"], m["resid"] if m["tint"] <= 45 else m["neutral"])
    return {"verdict": "unsure" if best <= high else "colour", "toned": 0, "score": best}


def measure_preview(pid: int) -> Optional[Dict[str, float]]:
    from PIL import Image
    t, _l = media.preview_paths(pid)
    if not t.is_file():
        return None
    try:
        with Image.open(t) as im:
            im.draft("RGB", (200, 200))  # JPEG: decode at a fraction of the size, much faster on a NAS CPU
            return measure(im)
    except Exception as e:
        log.debug("measure failed for %s: %s", pid, e)
        return None


# ---------------------------------------------------------------- one photo
def classify(row, b: dict, h: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the B&W rule says about one photo (a photos row). Returns verdict, toned, source, score, detail, sig."""
    if h is None:
        h = history_of(row["relpath"])
    edited = h["bnw"] is not None
    src = row["preview_src"] or ""
    rendered = src == "rendered" and (not row["rendered_hist"] or row["rendered_hist"] == h["sig"])
    sig = "|".join([_sig_settings(b), str(row["preview_ver"]), src, h["sig"]])
    out: Dict[str, Any] = {"verdict": "none", "toned": 0, "source": "", "score": None, "detail": "", "sig": sig}
    if edited:
        if b["use_rendered"] and rendered and row["preview_state"] == 1:
            m = measure_preview(row["id"])
            if m is not None:
                j = judge(m, b)
                out.update(j, source="rendered", detail=_pix_detail(j, m))
                if b["use_history"] and h["bnw"] and j["verdict"] == "colour":
                    out["detail"] = "darktable edit: %s, but the rendered preview has colour" % h["why"]
                    if b["disagree_review"]:
                        out["verdict"] = "unsure"
                return out
        if b["use_history"] and h["bnw"]:
            out.update(verdict="bnw", source="history", detail="darktable edit: %s" % h["why"])
            return out
        if b["use_camera"] and b["camera_over_edit"] and not rendered and src != "rendered" and row["preview_state"] == 1:
            # the preview LPL has is the camera's own: if the camera shot B&W, the photo is meant as B&W
            m = measure_preview(row["id"])
            if m is not None:
                j = judge(m, b)
                if j["verdict"] == "bnw":
                    out.update(j, source="camera", detail="Shot B&W in camera (camera preview); the darktable edit has no B&W module LPL knows")
                    return out
        if b["use_history"]:
            if h["unknown"]:  # could not read color calibration: let the user say
                out.update(verdict="unsure", source="history", detail="darktable edit: color calibration could not be read")
            else:
                out.update(verdict="colour", source="history", detail="darktable edit: no B&W module found")
        return out
    if b["use_camera"] and row["preview_state"] == 1:
        m = measure_preview(row["id"])
        if m is not None:
            j = judge(m, b)
            raw = row["ext"] in config_raw()
            out.update(j, source="camera" if raw else "pixels", detail=_pix_detail(j, m, "Camera preview" if raw else "Image"))
    return out


def config_raw():
    from .config import RAW_EXTS
    return RAW_EXTS


def _pix_detail(j, m, what="Rendered preview") -> str:
    if j["verdict"] == "bnw":
        return "%s: %s" % (what, "toned, one colour per brightness" if j["toned"] else "no colour")
    if j["verdict"] == "unsure":
        return "%s: very little colour (%.1f)" % (what, j["score"])
    return "%s: colour" % what


def wanted(verdict: str, toned: int, b: dict) -> List[str]:
    if verdict != "bnw":
        return []
    if toned and b["toned"] == "colour":
        return []
    if toned and b["toned"] == "bnw+toned":
        return [b["tag_bnw"], b["tag_toned"]]
    return [b["tag_bnw"]]


def _in_scope(row, b: dict) -> bool:
    fs = b["folders"]
    if not fs:
        return True
    rel = row["relpath"]
    return any(rel == f or rel.startswith(f + "/") for f in fs)


# ---------------------------------------------------------------- running
status: Dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "apply": False, "error": "", "finished": 0}
_run_lock = threading.Lock()
_dirty: set = set()
_dirty_lock = threading.Lock()
_wake = threading.Event()


def _rows(ids: Optional[Iterable[int]] = None):
    c = db.conn()
    cols = "id, relpath, ext, preview_ver, preview_state, preview_src, rendered_hist"
    if ids is None:
        return c.execute("SELECT %s FROM photos WHERE missing=0 ORDER BY id" % cols).fetchall()
    ids = [int(i) for i in ids]
    out = []
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        out += c.execute("SELECT %s FROM photos WHERE missing=0 AND id IN (%s)" % (cols, ",".join("?" * len(part))), part).fetchall()
    return out


def _store(pid: int, r: Dict[str, Any]) -> None:
    db.conn().execute(
        "INSERT INTO autotag_results(photo_id, rule, verdict, toned, source, score, detail, sig, checked) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(photo_id, rule) DO UPDATE SET verdict=excluded.verdict, toned=excluded.toned, source=excluded.source, "
        "score=excluded.score, detail=excluded.detail, sig=excluded.sig, checked=excluded.checked",
        (pid, RULE, r["verdict"], int(r["toned"] or 0), r["source"], r["score"], r["detail"], r["sig"], time.time()))


def _one(row, b: dict, known: Dict[int, str], force: bool):
    """Work for one photo, safe to run in a worker thread: (row id, rendered_hist to remember or None, result or None)."""
    h = history_of(row["relpath"])  # read once per photo
    remember = None
    if row["preview_src"] == "rendered" and not row["rendered_hist"] and h["sig"]:
        # the rendered preview's edit is remembered the first time it is looked at (put_preview / the bridge clear it)
        remember = h["sig"]
        row = dict(row)
        row["rendered_hist"] = h["sig"]
    if not force and row["id"] in known:
        sig = "|".join([_sig_settings(b), str(row["preview_ver"]), row["preview_src"] or "", h["sig"]])
        if sig == known[row["id"]]:
            return row["id"], remember, None  # same inputs as last time: keep the stored verdict
    return row["id"], remember, classify(row, b, h)


def _workers() -> int:
    import os
    return max(1, min(4, (os.cpu_count() or 2)))


def evaluate(ids: Optional[Iterable[int]] = None, force: bool = False, progress: bool = False) -> List[int]:
    """Classify photos (all, or these) and store the verdicts. Photos whose inputs did not change keep their row.
    Returns the ids that were looked at. Photos are read and measured in a few worker threads (the image decoding
    and numpy work let go of the GIL); the database is written in batches."""
    from concurrent.futures import ThreadPoolExecutor
    st = get_settings()
    b = st["bnw"]
    rows = [r for r in _rows(ids) if _in_scope(r, b)]
    known = {r["photo_id"]: r["sig"] for r in db.conn().execute("SELECT photo_id, sig FROM autotag_results WHERE rule=?", (RULE,)).fetchall()}
    t0 = time.time()
    if progress:
        status.update(total=len(rows), done=0, rate=0.0)
    done: List[int] = []
    pending: List[tuple] = []

    def flush():
        if not pending:
            return
        with db.write_lock:
            for pid, remember, r in pending:
                if remember:
                    db.conn().execute("UPDATE photos SET rendered_hist=? WHERE id=?", (remember, pid))
                if r is not None:
                    _store(pid, r)
            db.conn().commit()
        pending.clear()

    with ThreadPoolExecutor(max_workers=_workers(), thread_name_prefix="autotag-w") as ex:
        for n, (pid, remember, r) in enumerate(ex.map(lambda row: _one(row, b, known, force), rows, chunksize=16)):
            if remember or r is not None:
                pending.append((pid, remember, r))
                if len(pending) >= 200:
                    flush()
            done.append(pid)
            if progress:
                status["done"] = n + 1
                el = time.time() - t0
                if el > 0:
                    status["rate"] = round((n + 1) / el, 1)
    flush()
    el = time.time() - t0
    if len(rows) > 50:
        log.info("auto-tagging looked at %d photos in %.1fs (%.1f/s)", len(rows), el, len(rows) / el if el else 0)
    return done


def _overrides(ids=None) -> Dict[int, Any]:
    rows = db.conn().execute("SELECT photo_id, verdict, toned FROM autotag_overrides WHERE rule=?", (RULE,)).fetchall()
    return {r["photo_id"]: r for r in rows if ids is None or r["photo_id"] in ids}


def apply(ids: Optional[Iterable[int]] = None) -> Dict[str, int]:
    """Put the rule's tags on (and, if set, take them off) the photos as their verdicts and the user's corrections say.
    Unsure photos are left alone. A tag the rule put on and the user took off counts as a correction."""
    from . import api
    st = get_settings()
    b = st["bnw"]
    mine = {b["tag_bnw"], b["tag_toned"], *b["managed"]}
    c = db.conn()
    q = "SELECT photo_id, verdict, toned, applied FROM autotag_results WHERE rule=?"
    res = c.execute(q, (RULE,)).fetchall()
    want_ids = None if ids is None else {int(i) for i in ids}
    if want_ids is not None:
        res = [r for r in res if r["photo_id"] in want_ids]
    ov = _overrides()
    added = removed = 0
    touched: List[int] = []
    with db.write_lock:
        for r in res:
            pid = r["photo_id"]
            have = set(db.photo_tags(pid))
            try:
                applied = set(json.loads(r["applied"] or "[]"))
            except ValueError:
                applied = set()
            o = ov.get(pid)
            if o is None and applied and r["verdict"] == "bnw" and not (applied & have):
                # the user took the auto tag off: from now on that is their answer
                c.execute("INSERT OR REPLACE INTO autotag_overrides(photo_id, rule, verdict, toned, ts) VALUES(?,?,?,?,?)",
                          (pid, RULE, "colour", 0, time.time()))
                o = {"verdict": "colour", "toned": 0}
            if o is not None:
                want = set(wanted(o["verdict"], o["toned"], b))
            elif r["verdict"] in ("bnw", "colour", "none"):
                want = set(wanted(r["verdict"], r["toned"], b))
            else:  # unsure: wait for the user
                continue
            if r["verdict"] == "none" and o is None and not b["remove_stale"]:
                continue
            new = set(have) | want
            if b["remove_stale"] or o is not None:
                new -= (mine - want) & (have | applied)
            if new != have:
                added += len(new - have)
                removed += len(have - new)
                db.set_photo_meta(pid, tags=sorted(new))
                touched.append(pid)
            c.execute("UPDATE autotag_results SET applied=? WHERE photo_id=? AND rule=?", (json.dumps(sorted(want)), pid, RULE))
        c.commit()
        if touched:
            api.write_xmps(touched)
            c.commit()
    return {"added": added, "removed": removed, "photos": len(touched)}


def _job(apply_after: bool, force: bool) -> None:
    try:
        status.update(state="running", apply=apply_after, error="")
        evaluate(force=force, progress=True)
        if apply_after:
            status["last_apply"] = apply()
        status.update(state="idle", finished=time.time())
    except Exception as e:  # pragma: no cover
        log.exception("auto-tagging failed")
        status.update(state="error", error=str(e))
    finally:
        _run_lock.release()


def start(apply_after: bool = False, force: bool = False, wait: bool = False) -> bool:
    if not _run_lock.acquire(blocking=False):
        return False
    if wait:
        _job(apply_after, force)
    else:
        threading.Thread(target=_job, args=(apply_after, force), name="autotag", daemon=True).start()
    return True


# ---------------------------------------------------------------- new and changed photos, by themselves
def mark(ids: Iterable[int]) -> None:
    """These photos (new, a new preview, a changed XMP) should be looked at again when auto-tagging runs by itself."""
    st = get_settings()
    if not (st["auto"] and st["bnw"]["enabled"]):
        return
    with _dirty_lock:
        _dirty.update(int(i) for i in ids)
    _wake.set()


def run_dirty() -> int:
    with _dirty_lock:
        ids = list(_dirty)
        _dirty.clear()
    if not ids:
        return 0
    if not _run_lock.acquire(blocking=False):
        with _dirty_lock:
            _dirty.update(ids)
        return 0
    try:
        done = evaluate(ids)
        apply(done)
    finally:
        _run_lock.release()
    return len(ids)


_worker_started = False


def start_background() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    # verdicts stored by an older way of judging: look at every photo again once (unchanged ones are cheap)
    try:
        if get_settings()["bnw"]["enabled"] and int(db.get_setting("autotag_engine", "0") or 0) < ENGINE:
            if start(apply_after=True):
                db.set_setting("autotag_engine", str(ENGINE))
    except Exception:  # pragma: no cover
        log.exception("auto-tagging (re-check after an update)")

    def loop():
        while True:
            _wake.wait(60)
            _wake.clear()
            time.sleep(3)  # let a burst (a scan, a batch of previews) come together
            try:
                run_dirty()
            except Exception:  # pragma: no cover
                log.exception("auto-tagging (new photos)")
    threading.Thread(target=loop, name="autotag-auto", daemon=True).start()


def preview_changed(pid: int, rendered: bool) -> None:
    """A new preview: from darktable (rendered) or from the file (embedded). Caller holds write_lock."""
    db.conn().execute("UPDATE photos SET preview_src=?, preview_ts=?, rendered_hist=NULL WHERE id=?",
                      ("rendered" if rendered else "embedded", time.time(), pid))


def xmp_arrived(pid: int) -> None:
    """An edit arrived for this photo. Right after a rendered preview it belongs to it (the bridge sends both, in
    either order): forget the remembered edit so it is read from the new XMP. Caller holds write_lock."""
    db.conn().execute("UPDATE photos SET rendered_hist=NULL WHERE id=? AND preview_src='rendered' AND preview_ts>?",
                      (pid, time.time() - 600))


# ---------------------------------------------------------------- API
def photo_info(pid: int) -> Optional[dict]:
    st = get_settings()
    on = bool(st["bnw"]["enabled"])
    r = db.conn().execute("SELECT verdict, toned, source, detail, checked FROM autotag_results WHERE photo_id=? AND rule=?", (pid, RULE)).fetchone()
    o = db.conn().execute("SELECT verdict, toned FROM autotag_overrides WHERE photo_id=? AND rule=?", (pid, RULE)).fetchone()
    if not r and not o:
        return {"rule": RULE, "enabled": on, "verdict": None, "tags": [st["bnw"]["tag_bnw"], st["bnw"]["tag_toned"]]}
    return {"rule": RULE, "enabled": on, "verdict": r["verdict"] if r else None, "toned": int(r["toned"]) if r else 0, "source": r["source"] if r else "",
            "detail": r["detail"] if r else "", "checked": r["checked"] if r else None,
            "override": {"verdict": o["verdict"], "toned": int(o["toned"])} if o else None,
            "tags": [st["bnw"]["tag_bnw"], st["bnw"]["tag_toned"]]}


@router.get("/api/autotag")
def get_all():
    return {"settings": get_settings(), "status": status, "counts": counts()}


@router.put("/api/autotag")
def put_settings(body: Dict[str, Any] = Body(...)):
    st = save_settings(body)
    return {"settings": st, "counts": counts()}


def counts() -> Dict[str, int]:
    b = get_settings()["bnw"]
    c = db.conn()
    out = {"bnw": 0, "toned": 0, "colour": 0, "unsure": 0, "none": 0, "camera": 0, "checked": 0, "corrected": 0}
    for r in c.execute("SELECT verdict, toned, source, COUNT(*) n FROM autotag_results r JOIN photos p ON p.id=r.photo_id "
                       "WHERE r.rule=? AND p.missing=0 GROUP BY verdict, toned, source", (RULE,)).fetchall():
        v = r["verdict"]
        if v == "bnw" and r["toned"] and b["toned"] == "colour":
            out["colour"] += r["n"]
        elif v == "bnw":
            out["bnw"] += r["n"]
            if r["toned"]:
                out["toned"] += r["n"]
        else:
            out[v] = out.get(v, 0) + r["n"]
        if v == "bnw" and r["source"] == "camera":
            out["camera"] += r["n"]
        out["checked"] += r["n"]
    out["corrected"] = c.execute("SELECT COUNT(*) n FROM autotag_overrides WHERE rule=?", (RULE,)).fetchone()["n"]
    out["photos"] = c.execute("SELECT COUNT(*) n FROM photos WHERE missing=0").fetchone()["n"]
    return out


@router.post("/api/autotag/run")
def run(body: Dict[str, Any] = Body(default={})):
    """Check the photos in the rule's scope (a preview: nothing is written) or check and apply."""
    if not get_settings()["bnw"]["enabled"] and body.get("apply"):
        raise HTTPException(400, "switch the rule on first")
    if not start(apply_after=bool(body.get("apply")), force=bool(body.get("force")), wait=bool(body.get("wait"))):
        raise HTTPException(409, "auto-tagging is already running")
    return {"status": status}


@router.get("/api/autotag/status")
def get_status():
    return {"status": status, "counts": counts()}


@router.get("/api/autotag/review")
def review(kind: str = "unsure", offset: int = 0, limit: int = 120):
    """What a run found: the counts, and a page of photos of one kind (unsure, bnw, toned, removals) with why."""
    b = get_settings()["bnw"]
    c = db.conn()
    mine = [b["tag_bnw"], b["tag_toned"], *b["managed"]]
    ov = _overrides()
    tagged = {}
    if mine:
        for r in c.execute("SELECT pt.photo_id, t.path FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.path IN (%s)" % ",".join("?" * len(mine)), mine).fetchall():
            tagged.setdefault(r["photo_id"], set()).add(r["path"])
    rows = c.execute("SELECT r.photo_id id, r.verdict, r.toned, r.source, r.score, r.detail FROM autotag_results r JOIN photos p ON p.id=r.photo_id "
                     "WHERE r.rule=? AND p.missing=0 ORDER BY r.score IS NULL, r.score, r.photo_id", (RULE,)).fetchall()
    groups: Dict[str, list] = {"unsure": [], "bnw": [], "toned": [], "removals": [], "adds": []}
    for r in rows:
        o = ov.get(r["id"])
        v, t = (o["verdict"], o["toned"]) if o else (r["verdict"], r["toned"])
        if v == "unsure":
            groups["unsure"].append(r)
            continue
        want = set(wanted(v, t, b))
        have = tagged.get(r["id"], set())
        if v == "bnw" and want:
            groups["bnw"].append(r)
            if t and b["toned"] == "bnw+toned":
                groups["toned"].append(r)
        if want - have:
            groups["adds"].append(r)
        if (b["remove_stale"] or o) and (have - want):
            groups["removals"].append(r)
    if kind == "unsure":  # closest to the line first
        groups["unsure"].sort(key=lambda r: (r["score"] is None, r["score"] if r["score"] is not None else 0))
    page = groups.get(kind, [])[offset:offset + limit]
    from . import api
    photos = {p["id"]: p for p in api.photo_dicts([r["id"] for r in page])}
    items = []
    for r in page:
        p = photos.get(r["id"])
        if p:
            o = ov.get(r["id"])
            items.append({**p, "autotag": {"verdict": r["verdict"], "toned": r["toned"], "source": r["source"], "score": r["score"], "detail": r["detail"],
                                           "override": {"verdict": o["verdict"], "toned": o["toned"]} if o else None}})
    return {"counts": {k: len(v) for k, v in groups.items()}, "total": len(groups.get(kind, [])), "items": items, "status": status,
            "settings": get_settings()}


@router.post("/api/autotag/correct")
def correct(body: Dict[str, Any] = Body(...)):
    """The user's answer for photos: verdict bnw | toned | colour, or clear (back to what the rule says). Applied at once
    when the rule is on (apply: false only records it, for the review page's Apply)."""
    ids = [int(i) for i in body.get("ids") or []]
    v = str(body.get("verdict") or "")
    if v not in ("bnw", "toned", "colour", "clear"):
        raise HTTPException(400, "verdict: bnw, toned, colour or clear")
    with db.write_lock:
        c = db.conn()
        for pid in ids:
            if v == "clear":
                c.execute("DELETE FROM autotag_overrides WHERE photo_id=? AND rule=?", (pid, RULE))
            else:
                c.execute("INSERT OR REPLACE INTO autotag_overrides(photo_id, rule, verdict, toned, ts) VALUES(?,?,?,?,?)",
                          (pid, RULE, "colour" if v == "colour" else "bnw", 1 if v == "toned" else 0, time.time()))
            if not c.execute("SELECT 1 FROM autotag_results WHERE photo_id=? AND rule=?", (pid, RULE)).fetchone():
                c.execute("INSERT INTO autotag_results(photo_id, rule, verdict, source, detail, sig, checked) VALUES(?,?,?,?,?,?,?)",
                          (pid, RULE, "none", "", "", "", time.time()))
        c.commit()
    out = {"ok": True}
    # the user's own answer puts (or takes off) the tags at once, also while the rule itself is switched off
    if body.get("apply", True):
        out.update(apply(ids))
    from . import api
    out["items"] = api.photo_dicts(ids)
    return out


@router.post("/api/autotag/apply")
def apply_all(body: Dict[str, Any] = Body(default={})):
    """The review page's Apply: switch the rule on if needed, then apply every verdict and correction."""
    if body.get("enable"):
        save_settings({"bnw": {"enabled": True}})
    if not get_settings()["bnw"]["enabled"]:
        raise HTTPException(400, "switch the rule on first")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "auto-tagging is already running")
    try:
        r = apply()
    finally:
        _run_lock.release()
    return {**r, "counts": counts()}
