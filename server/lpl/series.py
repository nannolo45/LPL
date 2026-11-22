# SPDX-License-Identifier: GPL-3.0-or-later
"""Series folders, the Strip, and the link between a board and the order of its series.

The one place where membership and order are worked out, for the desktop, the phone and any other client:

- A series is a named set of photos with an order (series_items.ord). It sits at the top level or in one
  folder (series.folder_id); inside a folder a photo is in at most one series.
- A top-level series has its own board (series_frames, series_items.x/y). While that board is *linked*
  (series.linked), the order is read from it: frames in reading order, rows inside a frame, left to right.
- A folder has one shared board (folder_frames, folder_items, folder_shapes). While it is linked
  (series_folders.linked), each frame IS one of the folder's series: a photo belongs to the series whose
  frame it lies in, and its place inside the frame is its place in that series.
- Not linked: the board is a free moodboard; only the strip (the ord numbers) says what the order is.
- A series inside a folder keeps its own board as a free moodboard (never linked): its order comes from
  the folder.

Strip edits go through `strip/move`; on a linked board they re-flow the frame in the grid that "Add photos"
uses (CELL_W x CELL_H cells; a frame grows, what lies below is pushed down) and the order is then read back
from the board, so the two can not disagree."""
import json
import math
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()

from . import api, db, query  # noqa: E402  (api imports this module at its end; the router must exist first)
from .config import config  # noqa: E402

CELL_W, CELL_H = api.CELL_W, api.CELL_H
ROW_GAP = 55  # photos whose centres are closer than this (vertically) are in one row
PALETTE = ["#5b9be6", "#e5c547", "#f0a35a", "#5cc08a", "#e07bb0", "#a07be0", "#e5636a", "#8a8a92"]  # lane / frame colours (the board's frame colours)
FRAME_COLS = "id,label,x,y,w,h,maybe,color,border"


# ---------------------------------------------------------------- rows
def clean_name(v) -> str:
    return (v or "").strip().replace("|", "/")


def folder_row(fid: int):
    r = db.conn().execute("SELECT * FROM series_folders WHERE id=?", (fid,)).fetchone()
    if not r:
        raise HTTPException(404, "folder not found")
    return r


def series_in(c, fid: Optional[int]) -> list:
    if fid is None:
        return c.execute("SELECT * FROM series WHERE folder_id IS NULL ORDER BY pos NULLS LAST, name COLLATE NOCASE").fetchall()
    return c.execute("SELECT * FROM series WHERE folder_id=? ORDER BY pos NULLS LAST, name COLLATE NOCASE", (fid,)).fetchall()


def tag_of(s, folder_name: Optional[str] = None) -> str:
    """The tag that mirrors a series for darktable: series|<name>, or series|<folder>|<name> inside a folder."""
    fid = s["folder_id"]
    if fid:
        if folder_name is None:
            f = db.conn().execute("SELECT name FROM series_folders WHERE id=?", (fid,)).fetchone()
            folder_name = f["name"] if f else None
        if folder_name:
            return "%s%s|%s" % (api.SERIES_TAG, folder_name, s["name"])
    return api.SERIES_TAG + s["name"]


def is_linked(s) -> bool:
    """Is this series' own board the source of its order? Never inside a folder."""
    return bool(s["linked"]) and not s["folder_id"]


def retag(moves: list) -> None:
    """moves: (photo id, tag to take off or None, tag to put on or None). One XMP write per photo."""
    if not config.write_series_tags:
        return
    groups: Dict[tuple, list] = {}
    for pid, old, new in moves:
        if old != new:
            groups.setdefault((old, new), []).append(pid)
    for (old, new), ids in groups.items():
        body: Dict[str, Any] = {"ids": ids}
        if old:
            body["detach"] = [old]
        if new:
            body["attach"] = [new]
        api.set_meta(body)


def order_of(c, sid: int) -> List[int]:
    return [r["photo_id"] for r in c.execute("SELECT photo_id FROM series_items WHERE series_id=? ORDER BY ord NULLS LAST, photo_id", (sid,)).fetchall()]


def write_order(c, sid: int, ids: List[int], unordered: List[int] = ()) -> None:
    have = {r["photo_id"]: r["ord"] for r in c.execute("SELECT photo_id, ord FROM series_items WHERE series_id=?", (sid,)).fetchall()}
    for k, pid in enumerate(ids):
        if have.get(pid, -1) != k + 1:
            c.execute("UPDATE series_items SET ord=? WHERE series_id=? AND photo_id=?", (k + 1, sid, pid))
    for pid in unordered:
        if have.get(pid) is not None:
            c.execute("UPDATE series_items SET ord=NULL WHERE series_id=? AND photo_id=?", (sid, pid))


def nums_of(c, sid: int) -> Dict[int, int]:
    """The number shown on a photo: its place among the series' photos that are in the order and not hidden."""
    rows = c.execute("SELECT si.photo_id FROM series_items si JOIN photos p ON p.id=si.photo_id WHERE si.series_id=? AND si.ord IS NOT NULL "
                     "AND p.missing=0 AND COALESCE(p.hidden,0)=0 ORDER BY si.ord, si.photo_id", (sid,)).fetchall()
    return {r["photo_id"]: k + 1 for k, r in enumerate(rows)}


def own_slots(c, sid: int, n: int) -> list:
    """Free places on a series' own board for n more photos: rows of eight below everything (as "Add to series" does)."""
    maxy = c.execute("SELECT MAX(y) m FROM series_items WHERE series_id=?", (sid,)).fetchone()["m"]
    fr = c.execute("SELECT MAX(y+h) m FROM series_frames WHERE series_id=?", (sid,)).fetchone()["m"]
    base_y = max((maxy or 0) + 150, (fr or 0) + 40)
    return [(40 + (k % 8) * 165, base_y + (k // 8) * 140) for k in range(n)]


# ---------------------------------------------------------------- board geometry
def _round(v: float) -> int:
    return math.floor(v + 0.5)


def frames_sorted(frames: list) -> list:
    """Frames in reading order: top to bottom in bands of 100, then left to right (as the web board always did)."""
    return sorted(frames, key=lambda f: (_round(f["y"] / 100), f["x"]))


def inside(it: dict, f: dict) -> bool:
    cx, cy = it["x"] + it["tw"] / 2, it["y"] + it["th"] / 2
    return f["x"] <= cx <= f["x"] + f["w"] and f["y"] <= cy <= f["y"] + f["h"]


def owner(it: dict, fr: list, innermost: bool = False) -> Optional[dict]:
    """The frame a photo lies in (its centre). In several: the first in reading order (a series' own board, as
    always), or the smallest (a folder's board: a frame drawn inside a series' frame takes its photos)."""
    hit = None
    for f in fr:
        if inside(it, f):
            if not innermost:
                return f
            if hit is None or f["w"] * f["h"] < hit["w"] * hit["h"]:
                hit = f
    return hit


def in_rows(items: list) -> list:
    """Reading order of photos lying together: rows from the top, each row left to right. A row is found from
    the photos themselves (centres less than ROW_GAP apart), so it does not matter where on the board it lies."""
    its = sorted(items, key=lambda it: (it["y"] + it["th"] / 2, it["x"]))
    out: list = []
    row: list = []
    base = None
    for it in its:
        cy = it["y"] + it["th"] / 2
        if base is None or cy - base > ROW_GAP:
            out += sorted(row, key=lambda i: (i["x"], i["id"]))
            row, base = [], cy
        row.append(it)
    return out + sorted(row, key=lambda i: (i["x"], i["id"]))


def _overlap(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


class Board:
    """The frames and photo places of one board: a series' own ('s') or a folder's shared one ('f')."""

    def __init__(self, c, kind: str, bid: int):
        self.c, self.kind, self.id = c, kind, bid
        self.ft, self.it, self.key = ("series_frames", "series_items", "series_id") if kind == "s" else ("folder_frames", "folder_items", "folder_id")
        cols = FRAME_COLS + (",series_id" if kind == "f" else "")
        self.frames = [dict(r) for r in c.execute("SELECT %s FROM %s WHERE %s=? ORDER BY id" % (cols, self.ft, self.key), (bid,)).fetchall()]
        self.items: Dict[int, dict] = {}
        for r in c.execute("SELECT bi.photo_id id, bi.x, bi.y, p.width, p.height, p.orientation FROM %s bi LEFT JOIN photos p ON p.id=bi.photo_id "
                           "WHERE bi.%s=?" % (self.it, self.key), (bid,)).fetchall():
            tw, th = api._tile(r["width"], r["height"], r["orientation"])
            self.items[r["id"]] = {"id": r["id"], "x": r["x"] or 0, "y": r["y"] or 0, "tw": tw, "th": th}

    def _dims(self, pid: int):
        r = self.c.execute("SELECT width, height, orientation FROM photos WHERE id=?", (pid,)).fetchone()
        return api._tile(r["width"], r["height"], r["orientation"]) if r else (150, 100)

    def set_pos(self, pid: int, x: float, y: float) -> None:
        it = self.items.get(pid)
        if it is None:
            tw, th = self._dims(pid)
            it = self.items[pid] = {"id": pid, "x": x, "y": y, "tw": tw, "th": th}
            if self.kind == "f":
                self.c.execute("INSERT OR IGNORE INTO folder_items(folder_id,photo_id,x,y) VALUES(?,?,?,?)", (self.id, pid, x, y))
        it["x"], it["y"] = x, y
        self.c.execute("UPDATE %s SET x=?, y=? WHERE %s=? AND photo_id=?" % (self.it, self.key), (x, y, self.id, pid))

    def drop(self, pid: int) -> None:
        """Take a photo off a folder's board (a series' own board holds exactly its photos)."""
        if self.kind == "f" and self.items.pop(pid, None) is not None:
            self.c.execute("DELETE FROM folder_items WHERE folder_id=? AND photo_id=?", (self.id, pid))

    def bottom(self) -> float:
        return max([f["y"] + f["h"] for f in self.frames] + [it["y"] + it["th"] for it in self.items.values()] + [0])

    def new_frame(self, label: str, n: int, color: str = "", series_id: Optional[int] = None) -> dict:
        """A frame below everything, big enough for n photos in the grid."""
        cols = min(8, max(5, int((max(n, 1) * 1.5) ** 0.5) + 1))
        rows_n = max(1, (n + cols - 1) // cols)
        f = {"label": label, "x": 40, "y": self.bottom() + 40 if (self.frames or self.items) else 40,
             "w": 32 + cols * CELL_W, "h": 50 + rows_n * CELL_H, "maybe": 0, "color": color or "", "border": ""}
        if self.kind == "f":
            f["series_id"] = series_id
            f["id"] = self.c.execute("INSERT INTO folder_frames(folder_id,series_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,?,0,?,'')",
                                     (self.id, series_id, label, f["x"], f["y"], f["w"], f["h"], f["color"])).lastrowid
        else:
            f["id"] = self.c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,0,?,'')",
                                     (self.id, label, f["x"], f["y"], f["w"], f["h"], f["color"])).lastrowid
        self.frames.append(f)
        return f

    def delete_frame(self, f: dict) -> None:
        self.c.execute("DELETE FROM %s WHERE id=?" % self.ft, (f["id"],))
        self.frames = [x for x in self.frames if x is not f]

    def frame_of_series(self, sid: int) -> Optional[dict]:
        return next((f for f in self.frames if f.get("series_id") == sid), None)

    def lay(self, f: dict, pids: List[int], append: bool = False) -> None:
        """Put photos into frame f in the grid, in this order: from the frame's top (a re-flow of everything in
        it), or below what is already there (append). The frame grows to fit; whatever lies below it (frames
        with their content, loose photos) moves down by as much. f may be a place without a frame
        ({"virtual": True}): then nothing grows."""
        fr = frames_sorted(self.frames)
        own = {i: owner(it, fr, self.kind == "f") for i, it in self.items.items()}  # who holds what, before anything moves
        moving = set(pids)
        cols = max(1, int((f["w"] - 32) // CELL_W))
        start = f["y"] + 40
        if append:
            mine = [it for i, it in self.items.items() if own[i] is f and i not in moving]
            start = max([start] + [it["y"] + it["th"] + 14 for it in mine])
        for k, pid in enumerate(pids):
            tw, th = (self.items[pid]["tw"], self.items[pid]["th"]) if pid in self.items else self._dims(pid)
            self.set_pos(pid, round(f["x"] + 16 + (k % cols) * CELL_W + (150 - tw) // 2), round(start + (k // cols) * CELL_H + (105 - th) // 2))
        rows_n = (len(pids) + cols - 1) // cols
        grow = start + rows_n * CELL_H + 10 - (f["y"] + f["h"])
        if f.get("virtual"):
            return
        # another frame lying in or over this one (not simply below it) would take the photos laid here:
        # it goes under everything, with what is in it
        area = dict(f, h=f["h"] + max(grow, 0))
        for g in self.frames:
            if g is f or g["y"] >= f["y"] + f["h"] - 1 or not _overlap(g, area):
                continue
            dy = self.bottom() + 40 - g["y"]
            g["y"] += dy
            self.c.execute("UPDATE %s SET y=? WHERE id=?" % self.ft, (g["y"], g["id"]))
            for i, it in list(self.items.items()):
                if own.get(i) is g and i not in moving:
                    self.set_pos(i, it["x"], it["y"] + dy)
        if grow > 0:
            self._grow(f, grow, own, moving)

    def _grow(self, f: dict, grow: float, own: dict, skip: set) -> None:
        old_bottom = f["y"] + f["h"]
        f["h"] += grow
        self.c.execute("UPDATE %s SET h=? WHERE id=?" % self.ft, (f["h"], f["id"]))
        pushed = [(f, old_bottom)]
        moved = {id(f)}
        queue = [(f, old_bottom)]
        while queue:  # frames below a grown or pushed frame (and sharing some width with it) move down too
            h, ob = queue.pop(0)
            for g in self.frames:
                if id(g) in moved or not (g["y"] >= ob - 1 and g["x"] < h["x"] + h["w"] and h["x"] < g["x"] + g["w"]):
                    continue
                gob = g["y"] + g["h"]
                g["y"] += grow
                self.c.execute("UPDATE %s SET y=? WHERE id=?" % self.ft, (g["y"], g["id"]))
                moved.add(id(g))
                pushed.append((g, gob))
                queue.append((g, gob))
        for i, it in list(self.items.items()):
            if i in skip:
                continue
            o = own.get(i)
            if o is f:
                continue
            if o is not None:
                if id(o) in moved:
                    self.set_pos(i, it["x"], it["y"] + grow)
            elif any(it["y"] >= ob - 1 and it["x"] < h["x"] + h["w"] and h["x"] < it["x"] + it["tw"] for h, ob in pushed):
                self.set_pos(i, it["x"], it["y"] + grow)

    def place_below(self, pids: List[int]) -> None:
        """Loose places under everything, eight to a row."""
        base = self.bottom() + 40
        for k, pid in enumerate(pids):
            self.set_pos(pid, 40 + (k % 8) * 165, base + (k // 8) * 140)


# ---------------------------------------------------------------- order from a board (the link)
def sync_series(c, sid: int) -> None:
    """A linked single series: its order is its board's reading order. Frames in order, rows inside a frame,
    then the photos outside every frame; photos in a "maybe" frame have no place in the order."""
    b = Board(c, "s", sid)
    fr = frames_sorted(b.frames)
    groups: Dict[int, list] = {}
    outside = []
    for it in b.items.values():
        f = owner(it, fr)
        if f is None:
            outside.append(it)
        else:
            groups.setdefault(id(f), []).append(it)
    order: List[int] = []
    maybe: List[int] = []
    for f in fr:
        ids = [it["id"] for it in in_rows(groups.get(id(f), []))]
        if f["maybe"]:
            maybe += ids
        else:
            order += ids
    order += [it["id"] for it in in_rows(outside)]
    write_order(c, sid, order, maybe)


def members(c, fid: int):
    """photo -> series, and photo -> ord, for the series of a folder."""
    mem: Dict[int, int] = {}
    ords: Dict[int, Optional[int]] = {}
    for r in c.execute("SELECT si.photo_id, si.series_id, si.ord FROM series_items si JOIN series s ON s.id=si.series_id WHERE s.folder_id=?", (fid,)).fetchall():
        mem[r["photo_id"]] = r["series_id"]
        ords[r["photo_id"]] = r["ord"]
    return mem, ords


def set_member(c, pid: int, old: Optional[int], new: Optional[int]) -> None:
    if old:
        c.execute("DELETE FROM series_items WHERE series_id=? AND photo_id=?", (old, pid))
    if new:
        x, y = own_slots(c, new, 1)[0]
        c.execute("INSERT OR IGNORE INTO series_items(series_id,photo_id,x,y,ord) VALUES(?,?,?,?,NULL)", (new, pid, x, y))


def derive_folder(c, fid: int, b: Board, free_as_series: bool = False):
    """What a folder's board says: for each photo the series (or free frame) it belongs to, and each series' order.
    A photo inside a frame belongs to that frame's series; outside every frame it keeps its series and comes
    last; a series' photos that are not on the board at all come after those."""
    sids = [s["id"] for s in series_in(c, fid)]
    mem, ords = members(c, fid)

    def key(f):
        return f["series_id"] if f.get("series_id") in sids else ("new", f["id"])
    fr = frames_sorted([f for f in b.frames if f.get("series_id") in sids or free_as_series])
    target: Dict[int, Any] = dict(mem)
    by_frame: Dict[Any, list] = {}
    outside: Dict[int, list] = {}
    for pid, it in b.items.items():
        f = owner(it, fr, True)
        if f is not None:
            by_frame.setdefault(key(f), []).append(it)
            target[pid] = key(f)
        elif pid in mem:
            outside.setdefault(mem[pid], []).append(it)

    def prev(pid):
        o = ords.get(pid)
        return (o is None, o or 0)
    order: Dict[Any, List[int]] = {k: [it["id"] for it in in_rows(v)] for k, v in by_frame.items()}
    for sid in sids:
        out = sorted(outside.get(sid, []), key=lambda it: prev(it["id"]) + (it["y"], it["x"]))
        off = sorted([p for p, s in mem.items() if s == sid and p not in b.items], key=lambda p: prev(p) + (p,))
        order[sid] = order.get(sid, []) + [it["id"] for it in out] + off
    return target, order, mem


def sync_folder(c, fid: int, b: Optional[Board] = None) -> list:
    """A linked folder: membership and order of its series, read from its board. Returns the tag changes."""
    b = b or Board(c, "f", fid)
    target, order, mem = derive_folder(c, fid, b)
    tags = {s["id"]: tag_of(s) for s in series_in(c, fid)}
    moves = []
    for pid, new in target.items():
        old = mem.get(pid)
        if old != new:
            set_member(c, pid, old, new)
            moves.append((pid, tags.get(old), tags.get(new)))
    for sid, ids in order.items():
        write_order(c, sid, ids)
    return moves


def ensure_frames(c, fid: int, b: Board) -> list:
    """Every series of a linked folder has its frame on the folder's board (made below everything, with the
    series' photos laid into it in order)."""
    made = []
    for s in series_in(c, fid):
        if b.frame_of_series(s["id"]) is None:
            ids = order_of(c, s["id"])
            f = b.new_frame(s["name"], len(ids), s["color"] or "", s["id"])
            if ids:
                b.lay(f, ids)
            made.append(s["name"])
    return made


def unique_name(c, base: str) -> str:
    name, k = base, 2
    while (c.execute("SELECT 1 FROM series WHERE name=?", (name,)).fetchone()
           or c.execute("SELECT 1 FROM series_folders WHERE name=?", (name,)).fetchone()):
        name = "%s %d" % (base, k)
        k += 1
    return name


def adopt_free_frames(c, fid: int, b: Board) -> list:
    """Linked folder: a frame that is no series yet becomes one (named after the frame)."""
    sids = {s["id"] for s in series_in(c, fid)}
    made = []
    for f in b.frames:
        if f.get("series_id") in sids:
            continue
        name = unique_name(c, clean_name(f["label"]) or "Series")
        n = len(sids)
        color = f.get("color") or PALETTE[n % len(PALETTE)]
        pos = (c.execute("SELECT MAX(pos) m FROM series WHERE folder_id=?", (fid,)).fetchone()["m"] or 0) + 1
        sid = c.execute("INSERT INTO series(name, created, folder_id, pos, color, linked) VALUES(?,?,?,?,?,0)", (name, time.time(), fid, pos, color)).lastrowid
        c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe) VALUES(?,?,?,?,?,?,0)", (sid, "01", 40, 40, 900, 320))
        f["series_id"], f["label"], f["color"] = sid, name, color
        c.execute("UPDATE folder_frames SET series_id=?, label=?, color=? WHERE id=?", (sid, name, color, f["id"]))
        sids.add(sid)
        made.append(name)
    return made


# ---------------------------------------------------------------- hooks for the series endpoints in api.py
def list_extra(s) -> dict:
    return {"folder_id": s["folder_id"], "pos": s["pos"], "color": s["color"] or "", "linked": bool(is_linked(s))}


def folders_list() -> list:
    c = db.conn()
    out = []
    for f in c.execute("SELECT * FROM series_folders ORDER BY pos NULLS LAST, name COLLATE NOCASE").fetchall():
        r = c.execute("SELECT COUNT(*) n, COUNT(DISTINCT si.series_id) k FROM series_items si JOIN series s ON s.id=si.series_id WHERE s.folder_id=?", (f["id"],)).fetchone()
        ns = c.execute("SELECT COUNT(*) n FROM series WHERE folder_id=?", (f["id"],)).fetchone()["n"]
        out.append({"id": f["id"], "name": f["name"], "pos": f["pos"], "linked": bool(f["linked"]), "count": r["n"], "series_count": ns})
    return out


def create_series(body: Dict[str, Any]) -> dict:
    name = clean_name(body.get("name"))
    if not name:
        raise HTTPException(400, "name required")
    fid = body.get("folder_id")
    fid = int(fid) if fid else None
    with db.write_lock:
        c = db.conn()
        if fid is not None:
            folder_row(fid)
        if c.execute("SELECT 1 FROM series WHERE name=?", (name,)).fetchone():
            raise HTTPException(409, "a series with this name exists")
        if fid is None and c.execute("SELECT 1 FROM series_folders WHERE name=?", (name,)).fetchone():
            raise HTTPException(409, "a folder has this name")
        if fid is None:
            pos = (c.execute("SELECT MAX(pos) m FROM series WHERE folder_id IS NULL").fetchone()["m"] or 0) + 1
            color = str(body.get("color") or "")[:20]
        else:
            n = c.execute("SELECT COUNT(*) n FROM series WHERE folder_id=?", (fid,)).fetchone()["n"]
            pos = (c.execute("SELECT MAX(pos) m FROM series WHERE folder_id=?", (fid,)).fetchone()["m"] or 0) + 1
            color = str(body.get("color") or "")[:20] or PALETTE[n % len(PALETTE)]
        sid = c.execute("INSERT INTO series(name, created, folder_id, pos, color, linked) VALUES(?,?,?,?,?,?)",
                        (name, time.time(), fid, pos, color, 0 if fid else 1)).lastrowid
        c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe) VALUES(?,?,?,?,?,?,0)", (sid, "01", 40, 40, 900, 320))
        if fid is not None and folder_row(fid)["linked"]:
            ensure_frames(c, fid, Board(c, "f", fid))
        c.commit()
    ids = [int(i) for i in body.get("ids") or []]
    if ids:
        api.series_add(sid, {"ids": ids})
    return {"id": sid}


def patch_series(sid: int, body: Dict[str, Any]) -> dict:
    """PATCH /api/series/{id}: name (the tag is renamed with it) and/or color (its lane and, in a folder, its frame)."""
    s = api._series(sid)
    if "color" in body:
        color = str(body.get("color") or "")[:20]
        with db.write_lock:
            c = db.conn()
            c.execute("UPDATE series SET color=? WHERE id=?", (color, sid))
            c.execute("UPDATE folder_frames SET color=? WHERE series_id=?", (color, sid))
            c.commit()
    name = clean_name(body.get("name"))
    if not name or name == s["name"]:
        return {"ok": True}
    with db.write_lock:
        c = db.conn()
        if c.execute("SELECT 1 FROM series WHERE name=? AND id<>?", (name, sid)).fetchone():
            raise HTTPException(409, "a series with this name exists")
        if not s["folder_id"] and c.execute("SELECT 1 FROM series_folders WHERE name=?", (name,)).fetchone():
            raise HTTPException(409, "a folder has this name")
        old_tag = tag_of(s)
        c.execute("UPDATE series SET name=? WHERE id=?", (name, sid))
        c.execute("UPDATE folder_frames SET label=? WHERE series_id=?", (name, sid))
        c.commit()
        new_tag = tag_of(api._series(sid))
    if config.write_series_tags:
        api.tag_rename({"old": old_tag, "new": new_tag})
    return {"ok": True}


def on_delete(c, s) -> None:
    """A series is being deleted (caller holds the lock and commits): its frame and its photos leave the folder's board."""
    if s["folder_id"]:
        c.execute("DELETE FROM folder_items WHERE folder_id=? AND photo_id IN (SELECT photo_id FROM series_items WHERE series_id=?)", (s["folder_id"], s["id"]))
        c.execute("DELETE FROM folder_frames WHERE series_id=?", (s["id"],))


def after_add(sid: int, new: List[int]) -> None:
    """Photos were added to a series (rows exist, no order yet). Inside a folder a photo is in one series only,
    so they leave the folder's other series; on a linked board they go into the series' frame."""
    if not new:
        return
    moves = []
    with db.write_lock:
        c = db.conn()
        s = api._series(sid)
        fid = s["folder_id"]
        if fid:
            f = folder_row(fid)
            sibs = {x["id"]: x for x in series_in(c, fid) if x["id"] != sid}
            for pid in new:
                for r in c.execute("SELECT series_id FROM series_items WHERE photo_id=?", (pid,)).fetchall():
                    if r["series_id"] in sibs:
                        c.execute("DELETE FROM series_items WHERE series_id=? AND photo_id=?", (r["series_id"], pid))
                        moves.append((pid, tag_of(sibs[r["series_id"]], f["name"]), None))
            b = Board(c, "f", fid)
            if f["linked"]:
                ensure_frames(c, fid, b)
                b.lay(b.frame_of_series(sid), new, append=True)
                moves += sync_folder(c, fid, b)
            else:
                b.place_below([p for p in new if p not in b.items])
                _order_at_end(c, sid, new)
        elif s["linked"]:
            sync_series(c, sid)
        else:
            _order_at_end(c, sid, new)
        c.commit()
    retag(moves)


def _order_at_end(c, sid: int, new: List[int]) -> None:
    mx = c.execute("SELECT MAX(ord) m FROM series_items WHERE series_id=?", (sid,)).fetchone()["m"] or 0
    for k, pid in enumerate(new):
        c.execute("UPDATE series_items SET ord=? WHERE series_id=? AND photo_id=? AND ord IS NULL", (mx + k + 1, sid, pid))


def moved_count(sid: int, ids: List[int]) -> int:
    """How many of these photos are in another series of the same folder (adding them here moves them)."""
    s = api._series(sid)
    if not s["folder_id"] or not ids:
        return 0
    return db.conn().execute(
        "SELECT COUNT(DISTINCT si.photo_id) n FROM series_items si JOIN series x ON x.id=si.series_id WHERE x.folder_id=? AND x.id<>? AND si.photo_id IN (%s)"
        % ",".join(str(int(i)) for i in ids), (s["folder_id"], sid)).fetchone()["n"]


def after_remove(sid: int, ids: List[int]) -> None:
    """Photos left a series: on a linked folder board they leave the board too (there, the frame is the series)."""
    s = api._series(sid)
    if not s["folder_id"] or not ids:
        return
    with db.write_lock:
        c = db.conn()
        if folder_row(s["folder_id"])["linked"]:
            for pid in ids:
                c.execute("DELETE FROM folder_items WHERE folder_id=? AND photo_id=?", (s["folder_id"], pid))
            c.commit()


def after_board_save(sid: int) -> None:
    s = api._series(sid)
    if is_linked(s):
        with db.write_lock:
            sync_series(db.conn(), sid)
            db.conn().commit()


def board_extra(s, items: List[dict]) -> dict:
    """Extra fields of GET /api/series/{id}/board: linked, the folder, and each photo's number."""
    c = db.conn()
    nums = nums_of(c, s["id"])
    for it in items:
        it["num"] = nums.get(it["id"])
        it["series_id"] = s["id"]
    f = c.execute("SELECT id, name, linked FROM series_folders WHERE id=?", (s["folder_id"],)).fetchone() if s["folder_id"] else None
    return {"linked": is_linked(s), "folder": {"id": f["id"], "name": f["name"], "linked": bool(f["linked"])} if f else None,
            "color": s["color"] or ""}


def photo_series(pid: int) -> list:
    """The series a photo is in, with its folder and its number there (for the inspector)."""
    c = db.conn()
    out = []
    for r in c.execute("SELECT s.id, s.name, s.folder_id, s.color, f.name folder FROM series_items si JOIN series s ON s.id=si.series_id "
                       "LEFT JOIN series_folders f ON f.id=s.folder_id WHERE si.photo_id=?", (pid,)).fetchall():
        d = dict(r)
        nums = nums_of(c, r["id"])
        d["num"], d["count"] = nums.get(pid), len(nums)
        out.append(d)
    return out


# ---------------------------------------------------------------- folders
@router.post("/api/series-folders")
def folder_create(body: Dict[str, Any] = Body(...)):
    name = clean_name(body.get("name"))
    if not name:
        raise HTTPException(400, "name required")
    with db.write_lock:
        c = db.conn()
        if c.execute("SELECT 1 FROM series_folders WHERE name=?", (name,)).fetchone():
            raise HTTPException(409, "a folder with this name exists")
        if c.execute("SELECT 1 FROM series WHERE name=? AND folder_id IS NULL", (name,)).fetchone():
            raise HTTPException(409, "a series has this name")
        pos = (c.execute("SELECT MAX(pos) m FROM series_folders").fetchone()["m"] or 0) + 1
        fid = c.execute("INSERT INTO series_folders(name, pos, linked, created) VALUES(?,?,1,?)", (name, pos, time.time())).lastrowid
        c.commit()
    return {"id": fid}


@router.patch("/api/series-folders/{fid}")
def folder_rename(fid: int, body: Dict[str, Any] = Body(...)):
    f = folder_row(fid)
    name = clean_name(body.get("name"))
    if not name or name == f["name"]:
        return {"ok": True}
    with db.write_lock:
        c = db.conn()
        if c.execute("SELECT 1 FROM series_folders WHERE name=? AND id<>?", (name, fid)).fetchone():
            raise HTTPException(409, "a folder with this name exists")
        if c.execute("SELECT 1 FROM series WHERE name=? AND folder_id IS NULL", (name,)).fetchone():
            raise HTTPException(409, "a series has this name")
        c.execute("UPDATE series_folders SET name=? WHERE id=?", (name, fid))
        c.commit()
    res = {"ok": True}
    if config.write_series_tags:  # every series|<old>|... tag moves to series|<new>|... (the tag rename writes the XMP files)
        r = api.tag_rename({"old": api.SERIES_TAG + f["name"], "new": api.SERIES_TAG + name})
        res.update(photos=r["photos"], errors=r["errors"])
    return res


@router.delete("/api/series-folders/{fid}")
def folder_delete(fid: int):
    """Delete a folder. Its series move to the top level (nothing in them changes but their tag); the folder's
    shared board (layout, notes) goes with the folder."""
    f = folder_row(fid)
    errors: list = []
    with db.write_lock:
        c = db.conn()
        sers = series_in(c, fid)
        for s in sers:
            if c.execute("SELECT 1 FROM series_folders WHERE name=? AND id<>?", (s["name"], fid)).fetchone():
                raise HTTPException(409, "the series “%s” has the name of another folder: rename one of them first" % s["name"])
        pos = c.execute("SELECT MAX(pos) m FROM series WHERE folder_id IS NULL").fetchone()["m"] or 0
        for k, s in enumerate(sers):
            c.execute("UPDATE series SET folder_id=NULL, pos=?, linked=0 WHERE id=?", (pos + k + 1, s["id"]))
        for t in ("folder_frames", "folder_items", "folder_shapes"):
            c.execute("DELETE FROM %s WHERE folder_id=?" % t, (fid,))
        c.execute("DELETE FROM series_folders WHERE id=?", (fid,))
        c.execute("DELETE FROM settings WHERE key=?", (_add_key(fid),))
        c.commit()
        if config.write_series_tags:
            for s in sers:
                if c.execute("SELECT 1 FROM series_items WHERE series_id=? LIMIT 1", (s["id"],)).fetchone():
                    r = api.tag_rename({"old": "%s%s|%s" % (api.SERIES_TAG, f["name"], s["name"]), "new": api.SERIES_TAG + s["name"]})
                    errors += r["errors"]
            c.execute("DELETE FROM tags WHERE (path=? OR path LIKE ? ESCAPE '\\') AND id NOT IN (SELECT tag_id FROM photo_tags)",
                      (api.SERIES_TAG + f["name"], (api.SERIES_TAG + f["name"]).replace("%", "\\%").replace("_", "\\_") + "|%"))
            c.commit()
    return {"ok": True, "series": [s["id"] for s in sers], "errors": errors}


@router.post("/api/series/order")
def series_order(body: Dict[str, Any] = Body(...)):
    """The order of the series in a folder (folder_id) or at the top level (folder_id null): ids, first to last."""
    fid = body.get("folder_id")
    with db.write_lock:
        c = db.conn()
        have = [s["id"] for s in series_in(c, int(fid) if fid else None)]
        ids = [int(i) for i in body.get("ids") or [] if int(i) in have]
        for k, sid in enumerate(ids + [i for i in have if i not in ids]):
            c.execute("UPDATE series SET pos=? WHERE id=?", (k + 1, sid))
        c.commit()
    return {"ok": True}


@router.post("/api/series/{sid}/move")
def series_move(sid: int, body: Dict[str, Any] = Body(...)):
    """Move a series into a folder (folder_id) or out to the top level (folder_id null); "before": the series it
    goes in front of (else last). Its tag moves with it (series|<folder>|<name>)."""
    s = api._series(sid)
    fid = body.get("folder_id")
    fid = int(fid) if fid else None
    before = body.get("before")
    with db.write_lock:
        c = db.conn()
        target = folder_row(fid) if fid is not None else None
        old_tag = tag_of(s)
        if fid != s["folder_id"]:
            if fid is None and c.execute("SELECT 1 FROM series_folders WHERE name=?", (s["name"],)).fetchone():
                raise HTTPException(409, "a folder is named “%s” too: rename one of them first" % s["name"])
            if fid is not None:
                clash = c.execute(
                    "SELECT x.name, COUNT(*) n FROM series_items a JOIN series_items b ON b.photo_id=a.photo_id JOIN series x ON x.id=b.series_id "
                    "WHERE a.series_id=? AND x.folder_id=? GROUP BY x.id ORDER BY n DESC", (sid, fid)).fetchone()
                if clash:
                    raise HTTPException(409, "%d photo%s of “%s” %s also in “%s” of that folder. Inside a folder a photo is in one series: take %s out of one of the two first."
                                        % (clash["n"], "" if clash["n"] == 1 else "s", s["name"], "is" if clash["n"] == 1 else "are", clash["name"],
                                           "it" if clash["n"] == 1 else "them"))
            if s["folder_id"]:  # leaving a folder: off its board
                on_delete(c, s)
            color = s["color"] or ""
            if fid is not None and not color:
                color = PALETTE[c.execute("SELECT COUNT(*) n FROM series WHERE folder_id=?", (fid,)).fetchone()["n"] % len(PALETTE)]
            # in a folder the order comes from the folder; a series that leaves one keeps its order and stays unlinked
            c.execute("UPDATE series SET folder_id=?, linked=0, color=?, pos=NULL WHERE id=?", (fid, color, sid))
            write_order(c, sid, order_of(c, sid))  # every photo gets a place (a "maybe" pile had none)
        order = [x["id"] for x in series_in(c, fid) if x["id"] != sid]
        k = order.index(int(before)) if before and int(before) in order else len(order)
        order.insert(k, sid)
        for i, x in enumerate(order):
            c.execute("UPDATE series SET pos=? WHERE id=?", (i + 1, x))
        if target is not None and target["linked"] and fid != s["folder_id"]:
            ensure_frames(c, fid, Board(c, "f", fid))
        c.commit()
        new_tag = tag_of(api._series(sid))
    res: Dict[str, Any] = {"ok": True}
    if config.write_series_tags and new_tag != old_tag:
        r = api.tag_rename({"old": old_tag, "new": new_tag})
        res.update(photos=r["photos"], errors=r["errors"])
    return res


# ---------------------------------------------------------------- a folder's board
def _add_key(fid: int) -> str:
    return "series_folder_add.%d" % fid


def _lanes(c, fid: int) -> list:
    out = []
    for s in series_in(c, fid):
        n = c.execute("SELECT COUNT(*) n FROM series_items si JOIN photos p ON p.id=si.photo_id WHERE si.series_id=? AND p.missing=0", (s["id"],)).fetchone()["n"]
        out.append({"id": s["id"], "name": s["name"], "color": s["color"] or "", "count": n})
    return out


@router.get("/api/series-folders/{fid}/board")
def folder_board(fid: int):
    f = folder_row(fid)
    c = db.conn()
    frames = [dict(r) for r in c.execute("SELECT %s,series_id FROM folder_frames WHERE folder_id=? ORDER BY id" % FRAME_COLS, (fid,)).fetchall()]
    pos = {r["photo_id"]: r for r in c.execute("SELECT photo_id,x,y FROM folder_items WHERE folder_id=?", (fid,)).fetchall()}
    items = api.photo_dicts(list(pos.keys()))
    mem, ords = members(c, fid)
    nums = {sid: nums_of(c, sid) for sid in set(mem.values())}
    for it in items:
        r = pos[it["id"]]
        sid = mem.get(it["id"])
        it["x"], it["y"], it["ord"], it["series_id"] = r["x"], r["y"], ords.get(it["id"]), sid
        it["num"] = nums[sid].get(it["id"]) if sid else None
    try:
        last_add = json.loads(db.get_setting(_add_key(fid)) or "null")
    except ValueError:
        last_add = None
    r = c.execute("SELECT data FROM folder_shapes WHERE folder_id=?", (fid,)).fetchone()
    try:
        shapes = json.loads(r["data"]) if r else []
    except ValueError:
        shapes = []
    return {"folder": {"id": f["id"], "name": f["name"], "linked": bool(f["linked"])}, "linked": bool(f["linked"]), "lanes": _lanes(c, fid),
            "frames": frames, "items": items, "last_add": last_add, "shapes": shapes}


@router.put("/api/series-folders/{fid}/shapes")
def folder_shapes_save(fid: int, body: Dict[str, Any] = Body(...)):
    folder_row(fid)
    shapes = body.get("shapes")
    if not isinstance(shapes, list):
        raise HTTPException(400, "shapes must be a list")
    data = json.dumps(shapes[:5000], ensure_ascii=False)
    if len(data) > 5_000_000:
        raise HTTPException(413, "too much on this board")
    with db.write_lock:
        c = db.conn()
        c.execute("INSERT INTO folder_shapes(folder_id,data) VALUES(?,?) ON CONFLICT(folder_id) DO UPDATE SET data=excluded.data", (fid, data))
        c.commit()
    return {"ok": True, "n": len(shapes)}


@router.put("/api/series-folders/{fid}/board")
def folder_board_save(fid: int, body: Dict[str, Any] = Body(...)):
    """Save places and frames of a folder's board. Linked: membership and order of the folder's series are then
    read from it; a new frame becomes a new series, a renamed frame renames its series, a frame's colour is the
    series' colour. A series' frame can not be dropped by leaving it out (delete the series instead).

    items: [{id,x,y}] (photos not on the board yet are put on it); frames: the whole list; remove: photo ids to
    take off the board; restore: true when an undo/redo puts an earlier state back (a series that was made by
    drawing a frame and is empty again goes away with its frame)."""
    folder_row(fid)
    warnings: List[str] = []
    moves: list = []
    renames: list = []
    with db.write_lock:
        c = db.conn()
        f = folder_row(fid)
        linked = bool(f["linked"])
        sers = {s["id"]: s for s in series_in(c, fid)}
        have = {r["photo_id"] for r in c.execute("SELECT photo_id FROM folder_items WHERE folder_id=?", (fid,)).fetchall()}
        for it in body.get("items") or []:
            pid, x, y = int(it["id"]), float(it.get("x", 0)), float(it.get("y", 0))
            if pid in have:
                c.execute("UPDATE folder_items SET x=?, y=? WHERE folder_id=? AND photo_id=?", (x, y, fid, pid))
            elif c.execute("SELECT 1 FROM photos WHERE id=?", (pid,)).fetchone():
                c.execute("INSERT INTO folder_items(folder_id,photo_id,x,y) VALUES(?,?,?,?)", (fid, pid, x, y))
        gone = [int(i) for i in body.get("remove") or []]
        if gone:
            mem, _ = members(c, fid)
            for pid in gone:
                c.execute("DELETE FROM folder_items WHERE folder_id=? AND photo_id=?", (fid, pid))
                if linked and pid in mem:  # on a linked board, off the board is out of the series
                    set_member(c, pid, mem[pid], None)
                    moves.append((pid, tag_of(sers[mem[pid]], f["name"]), None))
        if "frames" in body:
            keep = []
            claimed = set()
            for fr in body["frames"]:
                vals = (fr.get("label") or "", float(fr["x"]), float(fr["y"]), float(fr["w"]), float(fr["h"]),
                        str(fr.get("color") or "")[:20], str(fr.get("border") or "")[:10])
                row = c.execute("SELECT id, series_id FROM folder_frames WHERE id=? AND folder_id=?", (int(fr["id"]), fid)).fetchone() if fr.get("id") else None
                if row:
                    c.execute("UPDATE folder_frames SET label=?,x=?,y=?,w=?,h=?,color=?,border=? WHERE id=?", vals + (row["id"],))
                    keep.append(row["id"])
                    claimed.add(row["series_id"])
                else:  # new, or brought back by undo: it is its old series again if that still exists and has no frame
                    sid = fr.get("series_id")
                    sid = int(sid) if sid and int(sid) in sers and int(sid) not in claimed and not c.execute(
                        "SELECT 1 FROM folder_frames WHERE series_id=?", (int(sid),)).fetchone() else None
                    keep.append(c.execute("INSERT INTO folder_frames(label,x,y,w,h,color,border,folder_id,series_id,maybe) VALUES(?,?,?,?,?,?,?,?,?,0)",
                                          vals + (fid, sid)).lastrowid)
                    claimed.add(sid)
            dropped = []
            for r in c.execute("SELECT id, series_id FROM folder_frames WHERE folder_id=?", (fid,)).fetchall():
                if r["id"] in keep:
                    continue
                if linked and r["series_id"] in sers:
                    if body.get("restore"):
                        dropped.append(r["series_id"])
                        c.execute("DELETE FROM folder_frames WHERE id=?", (r["id"],))
                    continue  # a series' frame stays
                c.execute("DELETE FROM folder_frames WHERE id=?", (r["id"],))
        else:
            dropped = []
        if linked:
            b = Board(c, "f", fid)
            for fr in b.frames:  # a frame's name and colour are its series'
                s = sers.get(fr.get("series_id"))
                if not s:
                    continue
                label = clean_name(fr["label"])
                if label and label != s["name"]:
                    if c.execute("SELECT 1 FROM series WHERE name=? AND id<>?", (label, s["id"])).fetchone():
                        warnings.append("A series named “%s” exists already; “%s” keeps its name." % (label, s["name"]))
                        c.execute("UPDATE folder_frames SET label=? WHERE id=?", (s["name"], fr["id"]))
                    else:
                        renames.append((tag_of(s, f["name"]), "%s%s|%s" % (api.SERIES_TAG, f["name"], label)))
                        c.execute("UPDATE series SET name=? WHERE id=?", (label, s["id"]))
                        c.execute("UPDATE folder_frames SET label=? WHERE id=?", (label, fr["id"]))
                elif not label:
                    c.execute("UPDATE folder_frames SET label=? WHERE id=?", (s["name"], fr["id"]))
                if (fr.get("color") or "") != (s["color"] or ""):
                    c.execute("UPDATE series SET color=? WHERE id=?", (fr.get("color") or "", s["id"]))
            adopt_free_frames(c, fid, b)
            moves += sync_folder(c, fid, b)
            for sid in dropped:  # undo of "draw a frame": the series it made goes if nothing is in it
                if not c.execute("SELECT 1 FROM series_items WHERE series_id=? LIMIT 1", (sid,)).fetchone():
                    c.execute("DELETE FROM series_frames WHERE series_id=?", (sid,))
                    c.execute("DELETE FROM series_shapes WHERE series_id=?", (sid,))
                    c.execute("DELETE FROM settings WHERE key=?", (api._add_criteria_key(sid),))
                    c.execute("DELETE FROM series WHERE id=?", (sid,))
            ensure_frames(c, fid, Board(c, "f", fid))
        c.commit()
    if config.write_series_tags:
        for old, new in renames:
            api.tag_rename({"old": old, "new": new})
    retag(moves)
    out = folder_board(fid)
    out["warnings"] = warnings
    return out


@router.post("/api/series-folders/{fid}/remove")
def folder_board_remove(fid: int, body: Dict[str, Any] = Body(...)):
    """Take photos off a folder's board. Linked: they leave their series too. Not linked: only the board."""
    return folder_board_save(fid, {"remove": body.get("ids") or []})


def _match_folder(fid: int, filters: Dict[str, Any], sort: List[Dict[str, str]]):
    f = dict(filters or {})
    for k in ("series", "ids", "not_in_series", "series_folder"):
        f.pop(k, None)
    if not any(f.get(k) for k in ("folders", "folder", "ratings", "labels", "tags", "tag", "lenses", "cameras", "date_from", "date_to", "q", "edited", "month")):
        raise HTTPException(400, "pick at least one folder or criterion")
    c = db.conn()
    where, args, join = query.build_where(f)
    total = c.execute("SELECT COUNT(*) n FROM photos p%s WHERE %s" % (join, where), args).fetchone()["n"]
    where += " AND p.id NOT IN (SELECT si.photo_id FROM series_items si JOIN series sx ON sx.id=si.series_id WHERE sx.folder_id=%d)" % int(fid)
    order = query.build_order(sort or [{"f": "taken", "d": "asc"}, {"f": "name", "d": "asc"}], 1, False)
    rows = c.execute("SELECT p.id, p.folder, p.preview_ver FROM photos p%s WHERE %s ORDER BY %s" % (join, where, order), args).fetchall()
    return total, rows


@router.post("/api/series-folders/{fid}/match")
def folder_match(fid: int, body: Dict[str, Any] = Body(...)):
    folder_row(fid)
    total, rows = _match_folder(fid, body.get("filters") or {}, body.get("sort") or [])
    folders = {r["folder"] for r in rows}
    return {"total": total, "new": len(rows), "limit": api.ADD_LIMIT, "folders": len(folders),
            "sample": [{"id": r["id"], "v": r["preview_ver"]} for r in rows[:14]]}


@router.post("/api/series-folders/{fid}/add-matching")
def folder_add_matching(fid: int, body: Dict[str, Any] = Body(...)):
    """Add every matching photo that is in none of the folder's series yet. target: {"series": <id>} (one of the
    folder's series), {"new": true, "label": name} (a new series), or {"per_folder": true} (a new series per
    photo folder, named after it)."""
    folder_row(fid)
    filters = body.get("filters") or {}
    target = body.get("target") or {"new": True}
    _, rows = _match_folder(fid, filters, body.get("sort") or [])
    rows = rows[:api.ADD_LIMIT]
    if not rows:
        return {"added": 0}
    c = db.conn()
    if target.get("series"):
        sid = int(target["series"])
        if api._series(sid)["folder_id"] != fid:
            raise HTTPException(400, "that series is not in this folder")
        groups = [(sid, [r["id"] for r in rows])]
    elif target.get("per_folder"):
        by: Dict[str, list] = {}
        for r in rows:
            by.setdefault(r["folder"], []).append(r["id"])
        groups = []
        for folder, ids in by.items():
            with db.write_lock:
                name = unique_name(c, clean_name(folder.rsplit("/", 1)[-1] or folder or "Root"))
            groups.append((create_series({"name": name, "folder_id": fid})["id"], ids))
    else:
        with db.write_lock:
            name = unique_name(c, clean_name(target.get("label")) or "Series")
        groups = [(create_series({"name": name, "folder_id": fid})["id"], [r["id"] for r in rows])]
    for sid, ids in groups:
        api.series_add(sid, {"ids": ids})
    db.set_setting(_add_key(fid), json.dumps({"filters": filters, "target": target}))
    return {"added": len(rows), "series": [g[0] for g in groups]}


# ---------------------------------------------------------------- strip
def _photos(c, ids: List[int], sid: Optional[int], nums: Dict[int, int], ords: Dict[int, Any]) -> list:
    out = api.photo_dicts(ids)
    for p in out:
        p["ord"], p["num"], p["series_id"] = ords.get(p["id"]), nums.get(p["id"]), sid
    return out


@router.get("/api/series-folders/{fid}/strip")
def folder_strip(fid: int):
    """A folder as lanes: one per series, in the folder's order, each with its photos in order."""
    f = folder_row(fid)
    c = db.conn()
    framed = {r["series_id"] for r in c.execute("SELECT series_id FROM folder_frames WHERE folder_id=?", (fid,)).fetchall()}
    _, ords = members(c, fid)
    lanes = []
    for s in series_in(c, fid):
        lanes.append({"key": "s%d" % s["id"], "series": {"id": s["id"], "name": s["name"], "color": s["color"] or ""},
                      "label": s["name"], "color": s["color"] or "", "maybe": 0, "frame": bool(f["linked"]) and s["id"] in framed,
                      "photos": _photos(c, order_of(c, s["id"]), s["id"], nums_of(c, s["id"]), ords)})
    return {"folder": {"id": f["id"], "name": f["name"], "linked": bool(f["linked"])}, "linked": bool(f["linked"]), "lanes": lanes}


@router.post("/api/series-folders/{fid}/strip/move")
def folder_strip_move(fid: int, body: Dict[str, Any] = Body(...)):
    """Put photos (ids, in the order they should stand) into the lane of series `series`, in front of photo
    `before` (null: at the end). Photos from the folder's other series move to this one. Linked: the series'
    frame on the board is re-flowed in the new order."""
    sid = int(body.get("series") or 0)
    before = body.get("before")
    moves = []
    with db.write_lock:
        c = db.conn()
        f = folder_row(fid)
        sers = {s["id"]: s for s in series_in(c, fid)}
        if sid not in sers:
            raise HTTPException(400, "that series is not in this folder")
        ids = []
        for i in body.get("ids") or []:
            if int(i) not in ids and c.execute("SELECT 1 FROM photos WHERE id=?", (int(i),)).fetchone():
                ids.append(int(i))
        mem, _ = members(c, fid)
        cur = [p for p in order_of(c, sid) if p not in ids]
        k = cur.index(int(before)) if before is not None and int(before) in cur else len(cur)
        new = cur[:k] + ids + cur[k:]
        for pid in ids:
            old = mem.get(pid)
            if old != sid:
                set_member(c, pid, old, sid)
                moves.append((pid, tag_of(sers[old], f["name"]) if old else None, tag_of(sers[sid], f["name"])))
        write_order(c, sid, new)
        if f["linked"]:
            b = Board(c, "f", fid)
            ensure_frames(c, fid, b)
            b.lay(b.frame_of_series(sid), new)
            moves += sync_folder(c, fid, b)  # the board has the last word; after a re-flow it says the same
        c.commit()
    retag(moves)
    return folder_strip(fid)


def series_lanes(c, s) -> list:
    """A single series as lanes: (key, frame or None, photo ids). Linked: its frames in reading order, then the
    photos outside every frame, then "maybe" frames. Otherwise one lane."""
    sid = s["id"]
    if not is_linked(s):
        return [("all", None, order_of(c, sid))]
    b = Board(c, "s", sid)
    fr = frames_sorted(b.frames)
    groups: Dict[int, list] = {}
    outside = []
    for it in b.items.values():
        f = owner(it, fr)
        if f is None:
            outside.append(it)
        else:
            groups.setdefault(id(f), []).append(it)
    lanes = [("f%d" % f["id"], f, [it["id"] for it in in_rows(groups.get(id(f), []))]) for f in fr if not f["maybe"]]
    if outside or not lanes:
        lanes.append(("out", None, [it["id"] for it in in_rows(outside)]))
    lanes += [("f%d" % f["id"], f, [it["id"] for it in in_rows(groups.get(id(f), []))]) for f in fr if f["maybe"]]
    return lanes


@router.get("/api/series/{sid}/strip")
def series_strip(sid: int):
    s = api._series(sid)
    c = db.conn()
    nums = nums_of(c, sid)
    ords = {r["photo_id"]: r["ord"] for r in c.execute("SELECT photo_id, ord FROM series_items WHERE series_id=?", (sid,)).fetchall()}
    lanes = []
    for key, f, ids in series_lanes(c, s):
        lanes.append({"key": key, "series": None, "label": f["label"] if f else ("Outside the frames" if key == "out" else s["name"]),
                      "color": (f.get("color") or "") if f else (s["color"] or ""), "maybe": int(bool(f and f["maybe"])), "frame": f is not None,
                      "photos": _photos(c, ids, sid, nums, ords)})
    extra = board_extra(s, [])
    return {"series": {"id": s["id"], "name": s["name"], "folder_id": s["folder_id"], "color": s["color"] or ""}, "folder": extra["folder"],
            "linked": extra["linked"], "lanes": lanes}


@router.post("/api/series/{sid}/strip/move")
def series_strip_move(sid: int, body: Dict[str, Any] = Body(...)):
    """Put photos (ids, in order) into lane `lane` (a key from GET strip: "f<frame id>", "out", "all") in front
    of photo `before` (null: at the end). Linked: that frame is re-flowed and the order read back from the board."""
    lane = str(body.get("lane") or "all")
    before = body.get("before")
    s = api._series(sid)
    if s["folder_id"]:  # its order is the folder's business (a linked folder board re-flows this series' frame)
        have = set(order_of(db.conn(), sid))
        folder_strip_move(s["folder_id"], {"ids": [i for i in body.get("ids") or [] if int(i) in have], "series": sid, "before": before})
        return series_strip(sid)
    with db.write_lock:
        c = db.conn()
        s = api._series(sid)
        have = set(order_of(c, sid))
        ids = []
        for i in body.get("ids") or []:
            if int(i) in have and int(i) not in ids:
                ids.append(int(i))
        lanes = series_lanes(c, s)
        hit = next((x for x in lanes if x[0] == lane), None)
        if hit is None:
            raise HTTPException(400, "that frame no longer exists")
        cur = [p for p in hit[2] if p not in ids]
        k = cur.index(int(before)) if before is not None and int(before) in cur else len(cur)
        new = cur[:k] + ids + cur[k:]
        if is_linked(s):
            b = Board(c, "s", sid)
            f = hit[1]
            if f is None:  # the photos outside every frame: a block under the frames
                f = {"x": 24, "y": max([x["y"] + x["h"] for x in b.frames] + [0]), "w": 32 + 8 * CELL_W, "h": 0, "virtual": True}
            else:
                f = next(x for x in b.frames if x["id"] == f["id"])
            b.lay(f, new)
            sync_series(c, sid)
        else:
            write_order(c, sid, new)
        c.commit()
    return series_strip(sid)


# ---------------------------------------------------------------- link / unlink
def folder_link_state(c, fid: int) -> dict:
    """How far a folder's board and its strip are apart: what each way of linking them again would do."""
    b = Board(c, "f", fid)
    sers = series_in(c, fid)
    sids = {s["id"] for s in sers}
    target, order, mem = derive_folder(c, fid, b, free_as_series=True)
    free = [{"id": f["id"], "label": f["label"], "photos": sum(1 for v in target.values() if v == ("new", f["id"]))}
            for f in b.frames if f.get("series_id") not in sids]
    missing = [s["name"] for s in sers if b.frame_of_series(s["id"]) is None]
    moves = sum(1 for pid, v in target.items() if mem.get(pid) != v)
    reorder = sum(1 for s in sers if order.get(s["id"], []) != order_of(c, s["id"]))
    loose_ids = [pid for pid in b.items if pid not in mem]
    loose = [{"id": p["id"], "filename": p["filename"]} for p in api.photo_dicts(loose_ids)]
    off = sum(1 for pid in mem if pid not in b.items)
    return {"frames": len(b.frames), "series": len(sers), "free_frames": free, "missing_frames": missing, "moves": moves,
            "reordered": reorder, "loose": loose, "off_board": off,
            "same": not free and not missing and not moves and not reorder}


@router.get("/api/series-folders/{fid}/link")
def folder_link_get(fid: int):
    f = folder_row(fid)
    d = folder_link_state(db.conn(), fid)
    d["linked"] = bool(f["linked"])
    return d


@router.post("/api/series-folders/{fid}/link")
def folder_link(fid: int, body: Dict[str, Any] = Body(...)):
    """Link or unlink a folder's board. linked false: only the switch. linked true, with mode:
    "strip": arrange the board from the strip (each series' frame re-flowed in its order; frames that are no
             series are removed, their photos return to their series' frames; photos in no series stay on the
             board, outside the frames);
    "board": take order and membership from the board (a frame that is no series becomes one);
    "none":  only the switch (used by undo/redo, which then saves the board it restores)."""
    want = bool(body.get("linked"))
    mode = body.get("mode") or "strip"
    res: Dict[str, Any] = {"linked": want}
    moves: list = []
    with db.write_lock:
        c = db.conn()
        folder_row(fid)
        if want and mode == "strip":
            b = Board(c, "f", fid)
            sers = series_in(c, fid)
            sids = {s["id"] for s in sers}
            mem, _ = members(c, fid)
            removed = [f["label"] for f in b.frames if f.get("series_id") not in sids]
            for f in [f for f in b.frames if f.get("series_id") not in sids]:
                b.delete_frame(f)
            for s in sers:
                f = b.frame_of_series(s["id"])
                ids = order_of(c, s["id"])
                if f is None:
                    f = b.new_frame(s["name"], len(ids), s["color"] or "", s["id"])
                else:
                    c.execute("UPDATE folder_frames SET label=?, color=? WHERE id=?", (s["name"], s["color"] or "", f["id"]))
                    f["label"], f["color"] = s["name"], s["color"] or ""
                b.lay(f, ids)
                write_order(c, s["id"], ids)
            fr = frames_sorted(b.frames)
            if any(_overlap(a, x) for i, a in enumerate(fr) for x in fr[i + 1:]):  # frames lying over each other: one under the other
                y = min(f["y"] for f in fr)
                for f in fr:
                    dy = y - f["y"]
                    if dy:
                        for pid in order_of(c, f["series_id"]):
                            b.set_pos(pid, b.items[pid]["x"], b.items[pid]["y"] + dy)
                        f["y"] = y
                        c.execute("UPDATE folder_frames SET y=? WHERE id=?", (y, f["id"]))
                    y += f["h"] + 40
            loose = [pid for pid in b.items if pid not in mem]
            inside_some = [pid for pid in loose if owner(b.items[pid], fr) is not None]
            if inside_some:  # in no series, but lying in a series' frame: moved out, so it does not join that series
                b.place_below(inside_some)
            res.update(removed_frames=removed, loose=[{"id": p["id"], "filename": p["filename"]} for p in api.photo_dicts(loose)])
            c.execute("UPDATE series_folders SET linked=1 WHERE id=?", (fid,))
            moves += sync_folder(c, fid, b)
        elif want and mode == "board":
            b = Board(c, "f", fid)
            c.execute("UPDATE series_folders SET linked=1 WHERE id=?", (fid,))
            res["created"] = adopt_free_frames(c, fid, b)
            ensure_frames(c, fid, b)
            moves += sync_folder(c, fid, b)
        else:
            c.execute("UPDATE series_folders SET linked=? WHERE id=?", (int(want), fid))
        c.commit()
    retag(moves)
    res["moved"] = len(moves)
    return res


def series_link_state(c, s) -> dict:
    sid = s["id"]
    cur = order_of(c, sid)
    b = Board(c, "s", sid)
    fr = frames_sorted(b.frames)
    groups: Dict[int, list] = {}
    outside = []
    for it in b.items.values():
        f = owner(it, fr)
        (outside if f is None else groups.setdefault(id(f), [])).append(it)
    board: List[int] = []
    maybe = 0
    for f in fr:
        ids = [it["id"] for it in in_rows(groups.get(id(f), []))]
        if f["maybe"]:
            maybe += len(ids)
        else:
            board += ids
    board += [it["id"] for it in in_rows(outside)]
    ordered = [r["photo_id"] for r in c.execute("SELECT photo_id FROM series_items WHERE series_id=? AND ord IS NOT NULL ORDER BY ord, photo_id", (sid,)).fetchall()]
    return {"frames": len([f for f in fr if not f["maybe"]]), "photos": len(cur), "outside": len(outside), "maybe": maybe,
            "same": board == ordered, "differs": sum(1 for a, x in zip(board, ordered) if a != x) + abs(len(board) - len(ordered))}


@router.get("/api/series/{sid}/link")
def series_link_get(sid: int):
    s = api._series(sid)
    d = series_link_state(db.conn(), s)
    d["linked"] = is_linked(s)
    d["in_folder"] = bool(s["folder_id"])
    return d


@router.post("/api/series/{sid}/link")
def series_link(sid: int, body: Dict[str, Any] = Body(...)):
    """Link or unlink a top-level series' own board. linked true, with mode:
    "strip": arrange the board from the strip (the photos are placed into the frames in strip order; each frame
             keeps its number of photos, the last one takes the rest);
    "board": take the order from the board; "none": only the switch (undo/redo)."""
    want = bool(body.get("linked"))
    mode = body.get("mode") or "strip"
    with db.write_lock:
        c = db.conn()
        s = api._series(sid)
        if s["folder_id"] and want:
            raise HTTPException(409, "a series in a folder takes its order from the folder")
        if want and mode == "strip":
            b = Board(c, "s", sid)
            fr = [f for f in frames_sorted(b.frames) if not f["maybe"]]
            ids = order_of(c, sid)
            if not fr:
                fr = [b.new_frame("01", len(ids))]
            allfr = frames_sorted(b.frames)
            counts = [0] * len(fr)
            for pid in ids:
                o = owner(b.items[pid], allfr) if pid in b.items else None
                for k, f in enumerate(fr):
                    if o is f:
                        counts[k] += 1
            rest = list(ids)
            for k, f in enumerate(fr):
                take = rest if k == len(fr) - 1 else rest[:counts[k]]
                rest = rest[len(take):]
                b.lay(next(x for x in b.frames if x["id"] == f["id"]), take)
            c.execute("UPDATE series SET linked=1 WHERE id=?", (sid,))
            sync_series(c, sid)
        elif want and mode == "board":
            c.execute("UPDATE series SET linked=1 WHERE id=?", (sid,))
            sync_series(c, sid)
        else:
            c.execute("UPDATE series SET linked=? WHERE id=?", (int(want), sid))
            if not want:
                write_order(c, sid, order_of(c, sid))  # from here on the strip is the order: every photo has a place
        c.commit()
    return {"linked": want}


# ---------------------------------------------------------------- Send to NAS (see transfer.py and /api/import/index)
def import_place(c, sid: int, se: dict) -> None:
    """A series that just arrived with a send: into its folder (made if needed), with its colour and link state.
    Anything in the way (a series named like the folder) leaves it at the top level, as an older server would."""
    color = str(se.get("color") or "")[:20]
    fname = clean_name(se.get("folder"))
    fid = None
    if fname:
        r = c.execute("SELECT id FROM series_folders WHERE name=?", (fname,)).fetchone()
        if r:
            fid = r["id"]
        elif not c.execute("SELECT 1 FROM series WHERE name=? AND folder_id IS NULL AND id<>?", (fname, sid)).fetchone():
            pos = (c.execute("SELECT MAX(pos) m FROM series_folders").fetchone()["m"] or 0) + 1
            fid = c.execute("INSERT INTO series_folders(name, pos, linked, created) VALUES(?,?,?,?)",
                            (fname, pos, int(bool(se.get("folder_linked", True))), time.time())).lastrowid
    if fid is None:
        pos = (c.execute("SELECT MAX(pos) m FROM series WHERE folder_id IS NULL").fetchone()["m"] or 0) + 1
        linked = 0 if fname else int(bool(se.get("linked", True)))
    else:
        pos = se.get("pos") or (c.execute("SELECT MAX(pos) m FROM series WHERE folder_id=?", (fid,)).fetchone()["m"] or 0) + 1
        linked = 0
    c.execute("UPDATE series SET folder_id=?, pos=?, color=?, linked=? WHERE id=?", (fid, pos, color, linked, sid))


def import_boards(payload: list, pid_of, added: Dict[str, list]) -> None:
    """After a send: the folders' boards. A folder whose board is still empty takes the sender's (frames by
    series name, photos by their path); otherwise the new photos go into their series' frames."""
    moves: list = []
    with db.write_lock:
        c = db.conn()
        for fp in payload or []:
            f = c.execute("SELECT * FROM series_folders WHERE name=?", (clean_name(fp.get("name")),)).fetchone()
            if not f:
                continue
            fid = f["id"]
            sers = {s["name"]: s for s in series_in(c, fid)}
            empty = not c.execute("SELECT 1 FROM folder_frames WHERE folder_id=? LIMIT 1", (fid,)).fetchone() and \
                not c.execute("SELECT 1 FROM folder_items WHERE folder_id=? LIMIT 1", (fid,)).fetchone()
            if empty:
                for fr in fp.get("frames") or []:
                    s = sers.get(clean_name(fr.get("series")))
                    c.execute("INSERT INTO folder_frames(folder_id,series_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,?,0,?,?)",
                              (fid, s["id"] if s else None, fr.get("label") or "", float(fr["x"]), float(fr["y"]), float(fr["w"]), float(fr["h"]),
                               str(fr.get("color") or "")[:20], str(fr.get("border") or "")[:10]))
                for it in fp.get("items") or []:
                    pid = pid_of(it.get("dest"))
                    if pid:
                        c.execute("INSERT OR IGNORE INTO folder_items(folder_id,photo_id,x,y) VALUES(?,?,?,?)", (fid, pid, float(it.get("x") or 0), float(it.get("y") or 0)))
                shapes = fp.get("shapes") or []
                for sh in shapes:
                    for end in ("a", "b"):
                        e = sh.get(end)
                        if isinstance(e, dict) and e.get("dest"):
                            pid = pid_of(e.pop("dest"))
                            if pid:
                                e["photo"] = pid
                if shapes:
                    c.execute("INSERT INTO folder_shapes(folder_id,data) VALUES(?,?) ON CONFLICT(folder_id) DO UPDATE SET data=excluded.data",
                              (fid, json.dumps(shapes, ensure_ascii=False)))
            b = Board(c, "f", fid)
            for name, s in sers.items():
                new = [p for p in added.get(name, []) if p not in b.items]
                if not new:
                    continue
                if f["linked"]:
                    ensure_frames(c, fid, b)
                    b.lay(b.frame_of_series(s["id"]), new, append=True)
                else:
                    b.place_below(new)
            if f["linked"]:
                ensure_frames(c, fid, b)
                moves += sync_folder(c, fid, b)
        c.commit()
    retag(moves)


api.app.include_router(router)
