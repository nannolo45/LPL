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
import logging
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
GROUP_PAD, GROUP_TOP = 20, 50  # a group frame (subfolder): room around the frames in it, and under its name bar


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


def subfolders(c, fid: Optional[int]) -> list:
    if fid is None:
        return c.execute("SELECT * FROM series_folders WHERE parent_id IS NULL ORDER BY pos NULLS LAST, name COLLATE NOCASE").fetchall()
    return c.execute("SELECT * FROM series_folders WHERE parent_id=? ORDER BY pos NULLS LAST, name COLLATE NOCASE", (fid,)).fetchall()


def root_of(c, fid: int) -> int:
    """The top-level folder a folder lies in (itself, at the top level): the folder whose board it is on."""
    seen = set()
    while fid not in seen:
        seen.add(fid)
        r = c.execute("SELECT parent_id FROM series_folders WHERE id=?", (fid,)).fetchone()
        if not r or not r["parent_id"]:
            break
        fid = r["parent_id"]
    return fid


def entries(c, parent: Optional[int]) -> list:
    """What lies directly in a folder (None: the top level), subfolders and series in one order:
    [("f", id) | ("s", id)] by pos (a folder first on a tie)."""
    rows = [(r["pos"] if r["pos"] is not None else 10 ** 9, 0, r["name"].lower(), "f", r["id"]) for r in subfolders(c, parent)]
    rows += [(r["pos"] if r["pos"] is not None else 10 ** 9, 1, r["name"].lower(), "s", r["id"]) for r in series_in(c, parent)]
    return [(k, i) for _, _, _, k, i in sorted(rows)]


def top_entries(c) -> list:
    return entries(c, None)


def write_top(c, entries: list) -> None:
    for n, (k, i) in enumerate(entries):
        c.execute("UPDATE %s SET pos=? WHERE id=?" % ("series_folders" if k == "f" else "series"), (n + 1, i))


def next_pos(c, parent: Optional[int]) -> int:
    if parent is None:
        a = c.execute("SELECT MAX(pos) m FROM series_folders WHERE parent_id IS NULL").fetchone()["m"]
        b = c.execute("SELECT MAX(pos) m FROM series WHERE folder_id IS NULL").fetchone()["m"]
    else:
        a = c.execute("SELECT MAX(pos) m FROM series_folders WHERE parent_id=?", (parent,)).fetchone()["m"]
        b = c.execute("SELECT MAX(pos) m FROM series WHERE folder_id=?", (parent,)).fetchone()["m"]
    return max(a or 0, b or 0) + 1


def next_top_pos(c) -> int:
    return next_pos(c, None)


def folder_ids(c, fid: int, _seen=None) -> list:
    """A folder and every folder below it, in tree order."""
    seen = _seen if _seen is not None else set()
    if fid in seen:
        return []
    seen.add(fid)
    out = [fid]
    for r in subfolders(c, fid):
        out += folder_ids(c, r["id"], seen)
    return out


def tree_rows(c, fid: int, depth: int = 0, _seen=None) -> list:
    """Everything below a folder in tree order: ("f", folder row, depth) | ("s", series row, depth)."""
    seen = _seen if _seen is not None else {fid}
    out = []
    for k, i in entries(c, fid):
        if k == "s":
            out.append(("s", c.execute("SELECT * FROM series WHERE id=?", (i,)).fetchone(), depth))
        elif i not in seen:
            seen.add(i)
            out.append(("f", c.execute("SELECT * FROM series_folders WHERE id=?", (i,)).fetchone(), depth))
            out += tree_rows(c, i, depth + 1, seen)
    return out


def series_under(c, fid: int) -> list:
    """The series of a folder and of its subfolders, in tree order."""
    return [r for k, r, _ in tree_rows(c, fid) if k == "s"]


def folder_path(c, fid: Optional[int]) -> list:
    """[{id, name}] from the top-level folder down to this one."""
    out, seen = [], set()
    while fid and fid not in seen:
        seen.add(fid)
        r = c.execute("SELECT id, name, parent_id FROM series_folders WHERE id=?", (fid,)).fetchone()
        if not r:
            break
        out.insert(0, {"id": r["id"], "name": r["name"]})
        fid = r["parent_id"]
    return out


def folder_tag(c, fid: int) -> str:
    return api.SERIES_TAG + "|".join(p["name"] for p in folder_path(c, fid))


def tag_of(s, folder_name: Optional[str] = None) -> str:
    """The tag that mirrors a series for darktable: series|<name>, or series|<folder>|<subfolder>|…|<name>."""
    if s["folder_id"]:
        path = folder_path(db.conn(), s["folder_id"])
        if path:
            return "%s|%s" % (api.SERIES_TAG + "|".join(p["name"] for p in path), s["name"])
    return api.SERIES_TAG + s["name"]


def sibling_clash(c, name: str, parent: Optional[int], kind: str, own: int = 0) -> bool:
    """Would a folder ("f") / series ("s") of this name lie next to a series / folder of the same name? (Their
    tags would collide: series|…|<name> and series|…|<name>|….)"""
    if kind == "f":
        q = "SELECT 1 FROM series WHERE name=? AND id<>? AND folder_id %s" % ("IS NULL" if parent is None else "=%d" % parent)
    else:
        q = "SELECT 1 FROM series_folders WHERE name=? AND id<>? AND parent_id %s" % ("IS NULL" if parent is None else "=%d" % parent)
    return bool(c.execute(q, (name, own)).fetchone())


def tags_before(c, fid: Optional[int] = None, sids: Optional[list] = None) -> Dict[int, str]:
    rows = series_under(c, fid) if fid is not None else [c.execute("SELECT * FROM series WHERE id=?", (i,)).fetchone() for i in sids or []]
    return {r["id"]: tag_of(r) for r in rows if r}


def retag_changed(before: Dict[int, str]) -> dict:
    """After folders moved or were renamed: every series whose tag is another one now gets it renamed (the XMP
    files are written by the tag rename). before: series id -> its tag as it was."""
    res = {"photos": 0, "errors": []}
    if not config.write_series_tags:
        return res
    c = db.conn()
    olds = []
    for sid, old in before.items():
        s = c.execute("SELECT * FROM series WHERE id=?", (sid,)).fetchone()
        new = tag_of(s) if s else old
        if new == old:
            continue
        olds.append(old)
        if c.execute("SELECT 1 FROM series_items WHERE series_id=? LIMIT 1", (sid,)).fetchone():
            r = api.tag_rename({"old": old, "new": new})
            res["photos"] += r["photos"]
            res["errors"] += r["errors"]
    if olds:
        with db.write_lock:
            for old in olds:  # the old tag and the folder tags above it go once nothing carries them
                parts = old.split("|")
                for n in range(len(parts), 1, -1):
                    path = "|".join(parts[:n])
                    like = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "|%"
                    if c.execute("SELECT 1 FROM tags WHERE path LIKE ? ESCAPE '\\' LIMIT 1", (like,)).fetchone():
                        break
                    c.execute("DELETE FROM tags WHERE path=? AND id NOT IN (SELECT tag_id FROM photo_tags)", (path,))
            c.commit()
    return res


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


def _flat_sorted(frames: list) -> list:
    return sorted(frames, key=lambda f: (_round(f["y"] / 100), f["x"]))


def contains(g: dict, f: dict) -> bool:
    """Frame g holds frame f: f lies within g (a pixel of slack) and g is the bigger one."""
    return (g is not f and g["x"] - 1 <= f["x"] and g["y"] - 1 <= f["y"] and f["x"] + f["w"] <= g["x"] + g["w"] + 1
            and f["y"] + f["h"] <= g["y"] + g["h"] + 1 and (g["w"] * g["h"], -(g.get("id") or 0)) > (f["w"] * f["h"], -(f.get("id") or 0)))


def frame_parents(frames: list) -> Dict[int, Optional[dict]]:
    """id(frame) -> the smallest frame holding it (None at the top)."""
    out: Dict[int, Optional[dict]] = {}
    for f in frames:
        best = None
        for g in frames:
            if contains(g, f) and (best is None or g["w"] * g["h"] < best["w"] * best["h"]):
                best = g
        out[id(f)] = best
    return out


def frames_sorted(frames: list) -> list:
    """Frames in reading order: top to bottom in bands of 100, then left to right (as the web board always did);
    a frame inside another comes right after it, its own inner frames in the same order (a tree, read depth first)."""
    par = frame_parents(frames)
    kids: Dict[int, list] = {}
    for f in frames:
        kids.setdefault(id(par[id(f)]) if par[id(f)] is not None else 0, []).append(f)
    out: list = []

    def walk(key):
        for f in _flat_sorted(kids.get(key, [])):
            out.append(f)
            walk(id(f))
    walk(0)
    return out if len(out) == len(frames) else _flat_sorted(frames)


def inside(it: dict, f: dict) -> bool:
    cx, cy = it["x"] + it["tw"] / 2, it["y"] + it["th"] / 2
    return f["x"] <= cx <= f["x"] + f["w"] and f["y"] <= cy <= f["y"] + f["h"]


def owner(it: dict, fr: list, innermost: bool = True) -> Optional[dict]:
    """The frame a photo lies in (its centre); in several, the smallest: a frame inside another takes its photos
    (on a folder's board and, since 25 Nov 2026, on a series' own board too: nested frames are chapters)."""
    hit = None
    for f in fr:
        if inside(it, f):
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
        cols = FRAME_COLS + (",series_id,sub_id" if kind == "f" else "")
        self.frames = [dict(r) for r in c.execute("SELECT %s FROM %s WHERE %s=? ORDER BY id" % (cols, self.ft, self.key), (bid,)).fetchall()]
        self.items: Dict[int, dict] = {}
        for r in c.execute("SELECT bi.photo_id id, bi.x, bi.y, p.width, p.height, p.orientation FROM %s bi LEFT JOIN photos p ON p.id=bi.photo_id "
                           "WHERE bi.%s=?" % (self.it, self.key), (bid,)).fetchall():
            tw, th = api._tile(r["width"], r["height"], r["orientation"])
            self.items[r["id"]] = {"id": r["id"], "x": r["x"] or 0, "y": r["y"] or 0, "tw": tw, "th": th}

    # notes and arrows: one JSON list per board (series_shapes / folder_shapes)
    def _shape_table(self):
        return ("series_shapes", "series_id") if self.kind == "s" else ("folder_shapes", "folder_id")

    def load_shapes(self) -> list:
        t, k = self._shape_table()
        r = self.c.execute("SELECT data FROM %s WHERE %s=?" % (t, k), (self.id,)).fetchone()
        try:
            data = json.loads(r["data"]) if r else []
        except ValueError:
            data = []
        return data if isinstance(data, list) else []

    @staticmethod
    def shape_points(shapes: list) -> list:
        """What can move on its own: notes (a rectangle) and free arrow ends (a point, no photo / note behind it)."""
        out = []
        for sh in shapes:
            if not isinstance(sh, dict):
                continue
            if sh.get("kind") == "note" and isinstance(sh.get("x"), (int, float)) and isinstance(sh.get("y"), (int, float)):
                out.append((sh, {"x": sh["x"], "y": sh["y"], "tw": sh.get("w") or 0, "th": sh.get("h") or 0}))
            elif sh.get("kind") == "arrow":
                for e in (sh.get("a"), sh.get("b")):
                    if isinstance(e, dict) and e.get("photo") is None and e.get("note") is None \
                            and isinstance(e.get("x"), (int, float)) and isinstance(e.get("y"), (int, float)):
                        out.append((e, {"x": e["x"], "y": e["y"], "tw": 0, "th": 0}))
        return out

    def shape_owners(self, shapes: list, fr: list) -> list:
        return [(obj, pt, owner(pt, fr, self.kind == "f")) for obj, pt in self.shape_points(shapes)]

    def save_shapes(self, shapes: list) -> None:
        t, k = self._shape_table()
        self.c.execute("INSERT INTO %s(%s,data) VALUES(?,?) ON CONFLICT(%s) DO UPDATE SET data=excluded.data" % (t, k, k),
                       (self.id, json.dumps(shapes, ensure_ascii=False)))

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

    def new_frame(self, label: str, n: int, color: str = "", series_id: Optional[int] = None, sub_id: Optional[int] = None,
                  into: Optional[dict] = None) -> dict:
        """A frame below everything, big enough for n photos in the grid; sub_id: the group frame of a subfolder;
        into: the group frame it is then moved into."""
        cols = min(8, max(5, int((max(n, 1) * 1.5) ** 0.5) + 1))
        rows_n = max(1, (n + cols - 1) // cols)
        f = {"label": label, "x": 40, "y": self.bottom() + 40 if (self.frames or self.items) else 40,
             "w": 32 + cols * CELL_W, "h": 50 + rows_n * CELL_H, "maybe": 0, "color": color or "", "border": ""}
        if sub_id:
            f["w"], f["h"] = f["w"] + 2 * GROUP_PAD, 140
        if self.kind == "f":
            f["series_id"], f["sub_id"] = series_id, sub_id
            f["id"] = self.c.execute("INSERT INTO folder_frames(folder_id,series_id,sub_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,?,?,0,?,'')",
                                     (self.id, series_id, sub_id, label, f["x"], f["y"], f["w"], f["h"], f["color"])).lastrowid
        else:
            f["id"] = self.c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,0,?,'')",
                                     (self.id, label, f["x"], f["y"], f["w"], f["h"], f["color"])).lastrowid
        self.frames.append(f)
        if into is not None:
            self.put_into(f, into)
        return f

    # group frames (a folder's board): a subfolder is a frame that contains frames
    def ancestors(self, f: dict) -> list:
        """The group frames around frame f (its centre lies in them and they are larger), the innermost first."""
        if self.kind != "f":
            return []
        cx, cy, a = f["x"] + f["w"] / 2, f["y"] + f["h"] / 2, f["w"] * f["h"]
        out = [g for g in self.frames if g is not f and g.get("sub_id") and g["w"] * g["h"] > a
               and g["x"] <= cx <= g["x"] + g["w"] and g["y"] <= cy <= g["y"] + g["h"]]
        return sorted(out, key=lambda g: g["w"] * g["h"])

    def group_of(self, f: dict) -> Optional[dict]:
        a = self.ancestors(f)
        return a[0] if a else None

    def within(self, g: dict) -> list:
        """The frames lying in group frame g (at any depth)."""
        if not g.get("sub_id"):
            return []
        return [f for f in self.frames if f is not g and any(a is g for a in self.ancestors(f))]

    def frame_of_sub(self, sub: Optional[int]) -> Optional[dict]:
        return next((f for f in self.frames if sub and f.get("sub_id") == sub), None)

    def _owners(self):
        fr = frames_sorted(self.frames)
        return fr, {i: owner(it, fr, self.kind == "f") for i, it in self.items.items()}

    def move_block(self, f: dict, x: float, y: float) -> None:
        """Move a frame to (x, y) with everything in it: frames (a group frame), photos, notes."""
        dx, dy = x - f["x"], y - f["y"]
        if not dx and not dy:
            return
        fr, own = self._owners()
        blk = [f] + self.within(f)
        ids = {id(g) for g in blk}
        shapes = self.load_shapes()
        sown = self.shape_owners(shapes, fr) if shapes else []
        for g in blk:
            g["x"] += dx
            g["y"] += dy
            self.c.execute("UPDATE %s SET x=?, y=? WHERE id=?" % self.ft, (g["x"], g["y"], g["id"]))
        for i, it in list(self.items.items()):
            if own[i] is not None and id(own[i]) in ids:
                self.set_pos(i, it["x"] + dx, it["y"] + dy)
        changed = False
        for obj, pt, o in sown:
            if o is not None and id(o) in ids:
                obj["x"], obj["y"] = pt["x"] + dx, pt["y"] + dy
                changed = True
        if changed:
            self.save_shapes(shapes)

    def grow(self, f: dict, amount: float) -> None:
        fr, own = self._owners()
        shapes = self.load_shapes()
        if self._grow(f, amount, own, set(), self.shape_owners(shapes, fr) if shapes else []):
            self.save_shapes(shapes)

    def put_into(self, f: dict, g: Optional[dict]) -> None:
        """Frame f (with what is in it) goes to the end of group frame g, which grows to hold it (what is below is
        pushed); g None: below everything on the board."""
        blk = {id(x) for x in [f] + self.within(f)}
        _, own = self._owners()
        mine = {i for i, o in own.items() if o is not None and id(o) in blk}
        if g is None:
            rest = [x["y"] + x["h"] for x in self.frames if id(x) not in blk] + [it["y"] + it["th"] for i, it in self.items.items() if i not in mine]
            self.move_block(f, 40, max(rest) + 40 if rest else 40)
            return
        inner = [x["y"] + x["h"] for x in self.within(g) if id(x) not in blk] + [it["y"] + it["th"] for i, it in self.items.items() if own[i] is g]
        top = max(inner + [g["y"] + GROUP_TOP - 20]) + 20
        prev = dict(g, w=max(g["w"], f["w"] + 2 * GROUP_PAD))
        for a in [g] + self.ancestors(g):  # wide enough (nothing is pushed sideways)
            w = prev["w"] if a is g else max(a["w"], prev["x"] + prev["w"] + GROUP_PAD - a["x"])
            if w != a["w"]:
                a["w"] = w
                self.c.execute("UPDATE %s SET w=? WHERE id=?" % self.ft, (w, a["id"]))
            prev = a
        need = top + f["h"] + GROUP_PAD - (g["y"] + g["h"])
        if need > 0:
            self.grow(g, need)
        self.move_block(f, g["x"] + GROUP_PAD, top)

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
        shapes = None if f.get("virtual") else self.load_shapes()
        sown = self.shape_owners(shapes, fr) if shapes else []
        shifted = False
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
        anc = self.ancestors(f)
        for g in self.frames:
            if g is f or g["y"] >= f["y"] + f["h"] - 1 or not _overlap(g, area) or any(a is g for a in anc):
                continue  # (the group frames around this one are not in its way: they grow with it)
            dy = self.bottom() + 40 - g["y"]
            g["y"] += dy
            self.c.execute("UPDATE %s SET y=? WHERE id=?" % self.ft, (g["y"], g["id"]))
            for i, it in list(self.items.items()):
                if own.get(i) is g and i not in moving:
                    self.set_pos(i, it["x"], it["y"] + dy)
            for obj, pt, o in sown:  # notes and free arrow ends in the displaced frame go with it
                if o is g:
                    obj["y"] = pt["y"] = pt["y"] + dy
                    shifted = True
        if grow > 0:
            shifted = self._grow(f, grow, own, moving, sown) or shifted
        if shifted:
            self.save_shapes(shapes)

    def _grow(self, f: dict, grow: float, own: dict, skip: set, sown: list = ()) -> bool:
        """Frame f gets taller by `grow`, and so do the group frames around it; what lies below any of them (and
        shares some width) moves down by as much: frames (a group frame with the frames in it), photos, notes."""
        pushed = []
        stay = set()
        for g in [f] + self.ancestors(f):
            pushed.append((g, g["y"] + g["h"]))
            stay.add(id(g))
            g["h"] += grow
            self.c.execute("UPDATE %s SET h=? WHERE id=?" % self.ft, (g["h"], g["id"]))
        moved: set = set()
        queue = list(pushed)

        def push(g):
            gob = g["y"] + g["h"]
            g["y"] += grow
            self.c.execute("UPDATE %s SET y=? WHERE id=?" % self.ft, (g["y"], g["id"]))
            moved.add(id(g))
            pushed.append((g, gob))
            queue.append((g, gob))
        while queue:  # frames below a grown or pushed frame (and sharing some width with it) move down too
            h, ob = queue.pop(0)
            for g in self.frames:
                if id(g) in moved or id(g) in stay or not (g["y"] >= ob - 1 and g["x"] < h["x"] + h["w"] and h["x"] < g["x"] + g["w"]):
                    continue
                inner = self.within(g)
                push(g)
                for x in inner:
                    if id(x) not in moved and id(x) not in stay:
                        push(x)

        def below(x, y, w):
            return any(y >= ob - 1 and x < h["x"] + h["w"] and h["x"] < x + w for h, ob in pushed)
        for i, it in list(self.items.items()):
            if i in skip:
                continue
            o = own.get(i)
            if o is f:
                continue
            if o is not None and id(o) not in stay:
                if id(o) in moved:
                    self.set_pos(i, it["x"], it["y"] + grow)
            elif below(it["x"], it["y"], it["tw"]):  # loose on the board, or loose in a group frame that grew
                self.set_pos(i, it["x"], it["y"] + grow)
        changed = False
        for obj, pt, o in sown:  # notes / free arrow ends: inside the growing frame they stay; inside a pushed frame they go with it;
            if o is f:           # outside every frame, in the area the frame grows into or below a pushed frame, they are pushed too
                continue
            if o is not None and id(o) not in stay:
                push_it = id(o) in moved
            else:
                push_it = below(pt["x"], pt["y"], pt["tw"])
            if push_it:
                obj["y"] = pt["y"] = pt["y"] + grow
                changed = True
        return changed

    def place_below(self, pids: List[int]) -> None:
        """Loose places under everything, eight to a row."""
        base = self.bottom() + 40
        for k, pid in enumerate(pids):
            self.set_pos(pid, 40 + (k % 8) * 165, base + (k // 8) * 140)


# ---------------------------------------------------------------- undo of "Take the order from the board"
# Before that relink the server keeps what it can change (membership, order, frames, places, notes, the link, and the
# series it may create from extra frames); POST …/link/restore puts it all back. Kept in memory: a few, newest last.
_snaps: Dict[int, dict] = {}
_snap_seq = [0]


def _rows(c, sql: str, args: tuple = ()) -> list:
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def _put(c, table: str, rows: list) -> None:
    for r in rows:
        c.execute("INSERT OR REPLACE INTO %s(%s) VALUES(%s)" % (table, ",".join(r), ",".join("?" * len(r))), tuple(r.values()))


def link_snapshot(c, kind: str, tid: int) -> int:
    """kind "s" (a top-level series) or "f" (a folder). Returns the token for restore."""
    if kind == "s":
        snap = {"kind": "s", "id": tid, "linked": api._series(tid)["linked"], "items": _rows(c, "SELECT * FROM series_items WHERE series_id=?", (tid,))}
    else:
        sids = [x["id"] for x in series_under(c, tid)]
        q = ",".join("?" * len(sids))
        snap = {"kind": "f", "id": tid, "linked": folder_row(tid)["linked"], "sids": sids,
                "places": [(x["id"], x["folder_id"], x["pos"], x["name"]) for x in series_under(c, tid)],
                "folders": [dict(folder_row(i)) for i in folder_ids(c, tid)],
                "items": _rows(c, "SELECT * FROM series_items WHERE series_id IN (%s)" % q, tuple(sids)),
                "sframes": _rows(c, "SELECT * FROM series_frames WHERE series_id IN (%s)" % q, tuple(sids)),
                "frames": _rows(c, "SELECT * FROM folder_frames WHERE folder_id=?", (tid,)),
                "fitems": _rows(c, "SELECT * FROM folder_items WHERE folder_id=?", (tid,)),
                "shapes": _rows(c, "SELECT * FROM folder_shapes WHERE folder_id=?", (tid,))}
    _snap_seq[0] += 1
    _snaps[_snap_seq[0]] = snap
    for k in sorted(_snaps)[:-30]:
        del _snaps[k]
    return _snap_seq[0]


def link_restore(c, kind: str, tid: int, token: int) -> tuple:
    """Put back what link_snapshot kept (caller holds the lock and commits); returns the tag changes and the tags
    of series that are gone (to be dropped once nobody carries them)."""
    snap = _snaps.get(token)
    if not snap or snap["kind"] != kind or snap["id"] != tid:
        raise HTTPException(410, "this can not be undone any more (the server was restarted or newer changes came in)")
    moves: list = []
    orphans: list = []
    if kind == "s":
        c.execute("DELETE FROM series_items WHERE series_id=?", (tid,))
        _put(c, "series_items", snap["items"])
        c.execute("UPDATE series SET linked=? WHERE id=?", (snap["linked"], tid))
    else:
        now = series_under(c, tid)
        tags = {x["id"]: tag_of(x) for x in now}
        cur_mem = {r["photo_id"]: r["series_id"] for r in c.execute("SELECT photo_id, series_id FROM series_items WHERE series_id IN (%s)" % (",".join(str(x["id"]) for x in now) or "0"))}
        for x in now:
            if x["id"] not in snap["sids"]:  # made from a free frame by the relink: gone again
                for t in ("series_items", "series_frames", "series_shapes"):
                    c.execute("DELETE FROM %s WHERE series_id=?" % t, (x["id"],))
                c.execute("DELETE FROM settings WHERE key=?", (api._add_criteria_key(x["id"]),))
                c.execute("DELETE FROM series WHERE id=?", (x["id"],))
        keep = ",".join(str(i) for i in snap["sids"]) or "0"
        c.execute("DELETE FROM series_items WHERE series_id IN (%s)" % keep)
        c.execute("DELETE FROM series_frames WHERE series_id IN (%s)" % keep)
        _put(c, "series_items", snap["items"])
        _put(c, "series_frames", snap["sframes"])
        for t in ("folder_frames", "folder_items", "folder_shapes"):
            c.execute("DELETE FROM %s WHERE folder_id=?" % t, (tid,))
        _put(c, "folder_frames", snap["frames"])
        _put(c, "folder_items", snap["fitems"])
        _put(c, "folder_shapes", snap["shapes"])
        c.execute("UPDATE series_folders SET linked=? WHERE id=?", (snap["linked"], tid))
        had = {f["id"] for f in snap["folders"]}
        for i in folder_ids(c, tid):  # subfolders the relink made from group frames: gone again
            if i not in had:
                c.execute("DELETE FROM series_folders WHERE id=?", (i,))
        for f in snap["folders"]:
            c.execute("UPDATE series_folders SET parent_id=?, pos=?, name=?, color=? WHERE id=?", (f["parent_id"], f["pos"], f["name"], f["color"], f["id"]))
        for sid, folder_id, pos, name in snap["places"]:
            c.execute("UPDATE series SET folder_id=?, pos=? WHERE id=?", (folder_id, pos, sid))
        was = {r["photo_id"]: r["series_id"] for r in snap["items"]}
        moves = [(pid, tags.get(cur_mem.get(pid)), tags.get(was.get(pid))) for pid in set(cur_mem) | set(was) if cur_mem.get(pid) != was.get(pid)]
        orphans = [tags[x["id"]] for x in now if x["id"] not in snap["sids"]]
    del _snaps[token]
    return moves, orphans


# ---------------------------------------------------------------- order from a board (the link)
def resync_nested_once() -> None:
    """Once (25 Nov 2026): frames inside frames became chapters of their own on a series' board; read the order
    again of the linked series whose board has a frame inside another."""
    if db.get_setting("resync_nested_v1"):
        return
    try:
        with db.write_lock:
            c = db.conn()
            for r in c.execute("SELECT * FROM series").fetchall():
                s = dict(r)
                if s.get("folder_id") or not is_linked(s):
                    continue
                b = Board(c, "s", s["id"])
                par = frame_parents(b.frames)
                if any(p is not None for p in par.values()):
                    sync_series(c, s["id"])
            c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('resync_nested_v1', '1')")
            c.commit()
    except Exception:  # never in the way of starting
        logging.getLogger("lpl.series").exception("resync nested")


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
    """photo -> series, and photo -> ord, for the series of a top-level folder (its subfolders' series included)."""
    mem: Dict[int, int] = {}
    ords: Dict[int, Optional[int]] = {}
    ids = ",".join(str(i) for i in folder_ids(c, fid))
    for r in c.execute("SELECT si.photo_id, si.series_id, si.ord FROM series_items si JOIN series s ON s.id=si.series_id WHERE s.folder_id IN (%s)" % ids).fetchall():
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
    sids = [s["id"] for s in series_under(c, fid)]
    mem, ords = members(c, fid)

    def key(f):
        return f["series_id"] if f.get("series_id") in sids else ("new", f["id"])
    # group frames (subfolders, and frames that hold frames) own no photos: a photo is in a series' frame or in none
    fr = frames_sorted([f for f in b.frames if f.get("series_id") in sids or (free_as_series and not f.get("sub_id") and not holds_frames(b, f))])
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
    tags = {s["id"]: tag_of(s) for s in series_under(c, fid)}
    moves = []
    for pid, new in target.items():
        old = mem.get(pid)
        if old != new:
            set_member(c, pid, old, new)
            moves.append((pid, tags.get(old), tags.get(new)))
    for sid, ids in order.items():
        write_order(c, sid, ids)
    return moves


def holds_frames(b: Board, f: dict) -> bool:
    """Does another frame lie in this one (its centre, and it is smaller)? Such a frame is a group, not a series."""
    a = f["w"] * f["h"]
    return any(g is not f and g["w"] * g["h"] < a and f["x"] <= g["x"] + g["w"] / 2 <= f["x"] + f["w"] and f["y"] <= g["y"] + g["h"] / 2 <= f["y"] + f["h"]
               for g in b.frames)


def ensure_frames(c, fid: int, b: Board) -> list:
    """Every subfolder of a linked top-level folder has its group frame on the folder's board and every series its
    frame (made below everything or at the end of its folder's group frame, the series' photos laid into it in order)."""
    made = []
    for sub in folder_ids(c, fid)[1:]:
        if b.frame_of_sub(sub) is None:
            r = folder_row(sub)
            b.new_frame(r["name"], 0, r["color"] or "", sub_id=sub, into=b.frame_of_sub(r["parent_id"]))
    for s in series_under(c, fid):
        if b.frame_of_series(s["id"]) is None:
            ids = order_of(c, s["id"])
            f = b.new_frame(s["name"], len(ids), s["color"] or "", s["id"], into=b.frame_of_sub(s["folder_id"]))
            if ids:
                b.lay(f, ids)
            made.append(s["name"])
    return made


def sync_structure(c, fid: int, b: Board, warnings: Optional[list] = None) -> None:
    """A linked top-level folder: which folder a series or a subfolder lies in is read from the board. A frame
    belongs to the innermost group frame its centre lies in (else to the top-level folder); what changes its folder
    comes last there."""
    for (k, i), par in sorted(board_parents(c, fid, b).items()):
        if k == "f":
            r = folder_row(i)
            if r["parent_id"] != par:
                if sibling_clash(c, r["name"], par, "f"):
                    if warnings is not None:
                        warnings.append("A series named “%s” lies there already; the folder stays where it was." % r["name"])
                    continue
                c.execute("UPDATE series_folders SET parent_id=?, pos=? WHERE id=?", (par, next_pos(c, par), i))
        else:
            s = api._series(i)
            if s["folder_id"] != par:
                if sibling_clash(c, s["name"], par, "s"):
                    if warnings is not None:
                        warnings.append("A folder named “%s” lies there already; the series stays where it was." % s["name"])
                    continue
                c.execute("UPDATE series SET folder_id=?, pos=? WHERE id=?", (par, next_pos(c, par), i))


def unique_name(c, base: str) -> str:
    name, k = base, 2
    while (c.execute("SELECT 1 FROM series WHERE name=?", (name,)).fetchone()
           or c.execute("SELECT 1 FROM series_folders WHERE name=?", (name,)).fetchone()):
        name = "%s %d" % (base, k)
        k += 1
    return name


def make_subfolder(c, parent: int, label: str, color: str = "") -> tuple:
    name = unique_name(c, clean_name(label) or "Folder")
    n = c.execute("SELECT COUNT(*) n FROM series_folders WHERE parent_id=?", (parent,)).fetchone()["n"]
    color = color or PALETTE[(n + 3) % len(PALETTE)]
    sub = c.execute("INSERT INTO series_folders(name, pos, linked, created, parent_id, color) VALUES(?,?,1,?,?,?)",
                    (name, next_pos(c, parent), time.time(), parent, color)).lastrowid
    return sub, name, color


def adopt_free_frames(c, fid: int, b: Board, folders: Optional[list] = None) -> list:
    """Linked folder: a frame that is no series yet becomes one (named after the frame); a frame that holds
    frames becomes a subfolder. (Where they lie in the tree is then read by sync_structure.)"""
    sids = {s["id"] for s in series_under(c, fid)}
    subs = set(folder_ids(c, fid))
    made = []
    free = [f for f in b.frames if f.get("series_id") not in sids and f.get("sub_id") not in subs]
    groups = [f for f in free if holds_frames(b, f)]
    for f in groups:
        sub, name, color = make_subfolder(c, fid, f["label"], f.get("color") or "")
        f["sub_id"], f["series_id"], f["label"], f["color"] = sub, None, name, color
        c.execute("UPDATE folder_frames SET sub_id=?, series_id=NULL, label=?, color=? WHERE id=?", (sub, name, color, f["id"]))
        subs.add(sub)
        if folders is not None:
            folders.append(name)
    for f in free:
        if f in groups:
            continue
        name = unique_name(c, clean_name(f["label"]) or "Series")
        n = len(sids)
        color = f.get("color") or PALETTE[n % len(PALETTE)]
        sid = c.execute("INSERT INTO series(name, created, folder_id, pos, color, linked) VALUES(?,?,?,?,?,0)", (name, time.time(), fid, next_pos(c, fid), color)).lastrowid
        c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe) VALUES(?,?,?,?,?,?,0)", (sid, "01", 40, 40, 900, 320))
        f["series_id"], f["sub_id"], f["label"], f["color"] = sid, None, name, color
        c.execute("UPDATE folder_frames SET series_id=?, sub_id=NULL, label=?, color=? WHERE id=?", (sid, name, color, f["id"]))
        sids.add(sid)
        made.append(name)
    return made


# ---------------------------------------------------------------- hooks for the series endpoints in api.py
def list_extra(s) -> dict:
    return {"folder_id": s["folder_id"], "pos": s["pos"], "color": s["color"] or "", "linked": bool(is_linked(s))}


def folders_list() -> list:
    """Every folder, parents before their subfolders. count / series_count: directly in it; total_*: with its subfolders."""
    c = db.conn()
    out = []

    def walk(parent, depth, root):
        for f in subfolders(c, parent):
            r = c.execute("SELECT COUNT(*) n FROM series_items si JOIN series s ON s.id=si.series_id WHERE s.folder_id=?", (f["id"],)).fetchone()
            ns = c.execute("SELECT COUNT(*) n FROM series WHERE folder_id=?", (f["id"],)).fetchone()["n"]
            rt = root or f
            d = {"id": f["id"], "name": f["name"], "pos": f["pos"], "linked": bool(rt["linked"]), "count": r["n"], "series_count": ns,
                 "parent_id": f["parent_id"], "color": f["color"] or "", "depth": depth, "root_id": rt["id"], "total_count": r["n"], "total_series": ns}
            out.append(d)
            k = len(out)
            if depth < 50:
                walk(f["id"], depth + 1, rt)
            for x in out[k:]:
                if x["parent_id"] == f["id"]:
                    d["total_count"] += x["total_count"]
                    d["total_series"] += x["total_series"]
    walk(None, 0, None)
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
        if sibling_clash(c, name, fid, "s"):
            raise HTTPException(409, "a folder has this name")
        pos = next_pos(c, fid)
        if fid is None:
            color = str(body.get("color") or "")[:20]
        else:
            n = len(series_under(c, root_of(c, fid)))
            color = str(body.get("color") or "")[:20] or PALETTE[n % len(PALETTE)]
        sid = c.execute("INSERT INTO series(name, created, folder_id, pos, color, linked) VALUES(?,?,?,?,?,?)",
                        (name, time.time(), fid, pos, color, 0 if fid else 1)).lastrowid
        c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe) VALUES(?,?,?,?,?,?,0)", (sid, "01", 40, 40, 900, 320))
        root = root_of(c, fid) if fid is not None else None
        if root is not None and folder_row(root)["linked"]:
            ensure_frames(c, root, Board(c, "f", root))
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
        if sibling_clash(c, name, s["folder_id"], "s"):
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
        c.execute("DELETE FROM folder_items WHERE folder_id=? AND photo_id IN (SELECT photo_id FROM series_items WHERE series_id=?)", (root_of(c, s["folder_id"]), s["id"]))
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
        fid = root_of(c, s["folder_id"]) if s["folder_id"] else None
        if fid:
            f = folder_row(fid)
            sibs = {x["id"]: x for x in series_under(c, fid) if x["id"] != sid}
            for pid in new:
                for r in c.execute("SELECT series_id FROM series_items WHERE photo_id=?", (pid,)).fetchall():
                    if r["series_id"] in sibs:
                        c.execute("DELETE FROM series_items WHERE series_id=? AND photo_id=?", (r["series_id"], pid))
                        moves.append((pid, tag_of(sibs[r["series_id"]]), None))
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
    """How many of these photos are in another series of the same top-level folder (adding them here moves them)."""
    s = api._series(sid)
    if not s["folder_id"] or not ids:
        return 0
    c = db.conn()
    return c.execute(
        "SELECT COUNT(DISTINCT si.photo_id) n FROM series_items si JOIN series x ON x.id=si.series_id WHERE x.folder_id IN (%s) AND x.id<>? AND si.photo_id IN (%s)"
        % (",".join(str(i) for i in folder_ids(c, root_of(c, s["folder_id"]))), ",".join(str(int(i)) for i in ids)), (sid,)).fetchone()["n"]


def after_remove(sid: int, ids: List[int]) -> None:
    """Photos left a series: on a linked folder board they leave the board too (there, the frame is the series)."""
    s = api._series(sid)
    if not s["folder_id"] or not ids:
        return
    with db.write_lock:
        c = db.conn()
        root = root_of(c, s["folder_id"])
        if folder_row(root)["linked"]:
            for pid in ids:
                c.execute("DELETE FROM folder_items WHERE folder_id=? AND photo_id=?", (root, pid))
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
    rl = bool(folder_row(root_of(c, f["id"]))["linked"]) if f else False
    return {"linked": is_linked(s), "folder": {"id": f["id"], "name": f["name"], "linked": rl} if f else None,
            "path": folder_path(c, s["folder_id"]), "color": s["color"] or ""}


def photo_series(pid: int) -> list:
    """The series a photo is in, with its folder and its number there (for the inspector)."""
    c = db.conn()
    out = []
    for r in c.execute("SELECT s.id, s.name, s.folder_id, s.color, f.name folder FROM series_items si JOIN series s ON s.id=si.series_id "
                       "LEFT JOIN series_folders f ON f.id=s.folder_id WHERE si.photo_id=?", (pid,)).fetchall():
        d = dict(r)
        nums = nums_of(c, r["id"])
        d["num"], d["count"] = nums.get(pid), len(nums)
        d["path"] = [p["name"] for p in folder_path(c, r["folder_id"])]
        out.append(d)
    return out


# ---------------------------------------------------------------- folders
@router.post("/api/series-folders")
def folder_create(body: Dict[str, Any] = Body(...)):
    """name; parent_id: the folder it lies in (none: the top level)."""
    name = clean_name(body.get("name"))
    if not name:
        raise HTTPException(400, "name required")
    parent = int(body["parent_id"]) if body.get("parent_id") else None
    with db.write_lock:
        c = db.conn()
        if parent is not None:
            folder_row(parent)
        if c.execute("SELECT 1 FROM series_folders WHERE name=?", (name,)).fetchone():
            raise HTTPException(409, "a folder with this name exists")
        if sibling_clash(c, name, parent, "f"):
            raise HTTPException(409, "a series has this name")
        color = ""
        if parent is not None:
            color = str(body.get("color") or "")[:20] or PALETTE[(c.execute("SELECT COUNT(*) n FROM series_folders WHERE parent_id IS NOT NULL").fetchone()["n"] + 3) % len(PALETTE)]
        fid = c.execute("INSERT INTO series_folders(name, pos, linked, created, parent_id, color) VALUES(?,?,1,?,?,?)",
                        (name, next_pos(c, parent), time.time(), parent, color)).lastrowid
        if parent is not None:
            root = root_of(c, fid)
            if folder_row(root)["linked"]:
                ensure_frames(c, root, Board(c, "f", root))
        c.commit()
    return {"id": fid}


@router.patch("/api/series-folders/{fid}")
def folder_rename(fid: int, body: Dict[str, Any] = Body(...)):
    """name (every series|…|<old>|… tag is renamed with it) and/or color (a subfolder's group frame and heading)."""
    f = folder_row(fid)
    if "color" in body:
        color = str(body.get("color") or "")[:20]
        with db.write_lock:
            c = db.conn()
            c.execute("UPDATE series_folders SET color=? WHERE id=?", (color, fid))
            c.execute("UPDATE folder_frames SET color=? WHERE sub_id=?", (color, fid))
            c.commit()
    name = clean_name(body.get("name"))
    if not name or name == f["name"]:
        return {"ok": True}
    with db.write_lock:
        c = db.conn()
        if c.execute("SELECT 1 FROM series_folders WHERE name=? AND id<>?", (name, fid)).fetchone():
            raise HTTPException(409, "a folder with this name exists")
        if sibling_clash(c, name, f["parent_id"], "f"):
            raise HTTPException(409, "a series has this name")
        old = folder_tag(c, fid)
        c.execute("UPDATE series_folders SET name=? WHERE id=?", (name, fid))
        c.execute("UPDATE folder_frames SET label=? WHERE sub_id=?", (name, fid))
        c.commit()
        new = folder_tag(c, fid)
    res = {"ok": True}
    if config.write_series_tags:  # every series|…|<old>|... tag moves to series|…|<new>|... (the tag rename writes the XMP files)
        r = api.tag_rename({"old": old, "new": new})
        res.update(photos=r["photos"], errors=r["errors"])
    return res


def transplant(c, fid: int, old_root: int, new_root: int, parent: Optional[int]) -> None:
    """Folder fid changes its top-level folder: its part of old_root's board (its group frame, the frames and
    photos of its series) goes to new_root's board, below everything (then into the group frame of its new parent).
    new_root == fid: it becomes a top-level folder with a board of its own."""
    whole = old_root == fid
    qf = ",".join(str(i) for i in folder_ids(c, fid))
    qs = ",".join(str(x["id"]) for x in series_under(c, fid)) or "0"
    fw = "folder_id=%d" % old_root + ("" if whole else " AND (series_id IN (%s) OR sub_id IN (%s))" % (qs, qf))
    iw = "folder_id=%d" % old_root + ("" if whole else " AND photo_id IN (SELECT photo_id FROM series_items WHERE series_id IN (%s))" % qs)
    f = folder_row(fid)
    if new_root == fid:
        c.execute("DELETE FROM folder_frames WHERE folder_id=? AND sub_id=?", (old_root, fid))
        c.execute("UPDATE series_folders SET linked=? WHERE id=?", (folder_row(old_root)["linked"], fid))
    frames = c.execute("SELECT x,y,w,h FROM folder_frames WHERE " + fw).fetchall()
    items = c.execute("SELECT x,y FROM folder_items WHERE " + iw).fetchall()
    if frames or items:
        minx = min([r["x"] for r in frames] + [r["x"] for r in items])
        miny = min([r["y"] for r in frames] + [r["y"] for r in items])
        maxx = max([r["x"] + r["w"] for r in frames] + [r["x"] + 150 for r in items])
        maxy = max([r["y"] + r["h"] for r in frames] + [r["y"] + 105 for r in items])
        if new_root == fid:
            tx, ty = 40, 40
        else:
            a = c.execute("SELECT MAX(y+h) m FROM folder_frames WHERE folder_id=?", (new_root,)).fetchone()["m"]
            b2 = c.execute("SELECT MAX(y) m FROM folder_items WHERE folder_id=?", (new_root,)).fetchone()["m"]
            base = max(a or 0, (b2 + 105) if b2 is not None else 0)
            tx, ty = 40 + (GROUP_PAD if whole else 0), (base + 40 if base else 40) + (GROUP_TOP if whole else 0)
        dx, dy = tx - minx, ty - miny
        c.execute("DELETE FROM folder_items WHERE folder_id=? AND photo_id IN (SELECT photo_id FROM folder_items WHERE %s)" % iw, (new_root,))
        c.execute("UPDATE folder_frames SET folder_id=?, x=x+?, y=y+? WHERE " + fw, (new_root, dx, dy))
        c.execute("UPDATE folder_items SET folder_id=?, x=x+?, y=y+? WHERE " + iw, (new_root, dx, dy))
        if whole:  # a top-level folder goes into another: its notes come along, and a group frame is drawn around what it had
            old = Board(c, "f", fid).load_shapes()
            if old:
                for obj, pt in Board.shape_points(old):
                    obj["x"], obj["y"] = pt["x"] + dx, pt["y"] + dy
                nb = Board(c, "f", new_root)
                nb.save_shapes(nb.load_shapes() + old)
            c.execute("INSERT INTO folder_frames(folder_id,sub_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,?,0,?,'')",
                      (new_root, fid, f["name"], tx - GROUP_PAD, ty - GROUP_TOP, maxx - minx + 2 * GROUP_PAD, maxy - miny + GROUP_TOP + GROUP_PAD, f["color"] or ""))
    if whole:
        c.execute("DELETE FROM folder_shapes WHERE folder_id=?", (fid,))
    if new_root != fid:
        b = Board(c, "f", new_root)
        g, tgt = b.frame_of_sub(fid), b.frame_of_sub(parent)
        if g is not None and tgt is not None:
            b.put_into(g, tgt)


@router.delete("/api/series-folders/{fid}")
def folder_delete(fid: int):
    """Delete (dissolve) a folder: what is in it moves up one level, in its place; nothing in the series changes
    but their tag. A subfolder's group frame goes, the frames in it stay. A top-level folder's shared board
    (layout, notes) goes with it; its subfolders become top-level folders and keep their part of the board."""
    f = folder_row(fid)
    with db.write_lock:
        c = db.conn()
        parent = f["parent_id"]
        sers = series_in(c, fid)
        subs = subfolders(c, fid)
        for s in sers:
            if c.execute("SELECT 1 FROM series_folders WHERE name=? AND id<>? AND parent_id %s" % ("IS NULL" if parent is None else "=%d" % parent), (s["name"], fid)).fetchone():
                raise HTTPException(409, "the series “%s” has the name of another folder: rename one of them first" % s["name"])
        for x in subs:
            if sibling_clash(c, x["name"], parent, "f"):
                raise HTTPException(409, "the folder “%s” has the name of a series there: rename one of them first" % x["name"])
        before = tags_before(c, fid)
        kids = entries(c, fid)
        if parent is None:
            pos = next_top_pos(c) - 1
            for x in subs:
                c.execute("UPDATE series_folders SET parent_id=NULL WHERE id=?", (x["id"],))
                transplant(c, x["id"], fid, x["id"], None)
            for k, (kind, i) in enumerate(kids):
                if kind == "s":
                    c.execute("UPDATE series SET folder_id=NULL, pos=?, linked=0 WHERE id=?", (pos + k + 1, i))
                else:
                    c.execute("UPDATE series_folders SET pos=? WHERE id=?", (pos + k + 1, i))
            for t in ("folder_frames", "folder_items", "folder_shapes"):
                c.execute("DELETE FROM %s WHERE folder_id=?" % t, (fid,))
        else:
            order = entries(c, parent)
            k = order.index(("f", fid))
            order[k:k + 1] = kids
            c.execute("UPDATE series SET folder_id=? WHERE folder_id=?", (parent, fid))
            c.execute("UPDATE series_folders SET parent_id=? WHERE parent_id=?", (parent, fid))
            write_top(c, order)
            c.execute("DELETE FROM folder_frames WHERE sub_id=?", (fid,))
        c.execute("DELETE FROM series_folders WHERE id=?", (fid,))
        c.execute("DELETE FROM settings WHERE key=?", (_add_key(fid),))
        c.commit()
        errors = retag_changed(before)["errors"]
    return {"ok": True, "series": [s["id"] for s in sers], "errors": errors}


@router.post("/api/series/order")
def series_order(body: Dict[str, Any] = Body(...)):
    """The order of the series in a folder (folder_id) or at the top level (folder_id null): ids, first to last.
    The series take the series' places; folders lying among them stay where they are."""
    fid = int(body["folder_id"]) if body.get("folder_id") else None
    with db.write_lock:
        c = db.conn()
        have = [s["id"] for s in series_in(c, fid)]
        ids = [int(i) for i in body.get("ids") or [] if int(i) in have]
        it = iter(ids + [i for i in have if i not in ids])
        write_top(c, [(k, next(it)) if k == "s" else (k, i) for k, i in entries(c, fid)])
        c.commit()
    return {"ok": True}


@router.post("/api/series/top-order")
def series_top_order(body: Dict[str, Any] = Body(...)):
    """The order of the top level (or, with folder_id, of what lies in that folder), folders and series together:
    entries [{"folder": id} | {"series": id}], first to last (what is not named follows in its present order).
    The same on every device."""
    fid = int(body["folder_id"]) if body.get("folder_id") else None
    with db.write_lock:
        c = db.conn()
        cur = entries(c, fid)
        have = set(cur)
        want: list = []
        for e in body.get("entries") or []:
            k = ("f", int(e["folder"])) if isinstance(e, dict) and e.get("folder") is not None else ("s", int(e["series"])) if isinstance(e, dict) and e.get("series") is not None else None
            if k in have and k not in want:
                want.append(k)
        write_top(c, want + [k for k in cur if k not in want])
        c.commit()
    return {"ok": True}


def _insert_entry(c, parent: Optional[int], key: tuple, body: Dict[str, Any]) -> None:
    """Put a series / folder at its place among what lies in `parent`: in front of "before_folder" or "before" (a series), else last."""
    order = [e for e in entries(c, parent) if e != key]
    bf, before = body.get("before_folder"), body.get("before")
    k = order.index(("f", int(bf))) if bf and ("f", int(bf)) in order else order.index(("s", int(before))) if before and ("s", int(before)) in order else len(order)
    order.insert(k, key)
    write_top(c, order)


def _photo_clash(c, sids: list, root: int, what: str) -> None:
    """Inside a top-level folder a photo is in one series: refuse to bring in series that share photos with one of its."""
    clash = c.execute(
        "SELECT x.name, y.name mine, COUNT(*) n FROM series_items a JOIN series_items b ON b.photo_id=a.photo_id JOIN series x ON x.id=b.series_id "
        "JOIN series y ON y.id=a.series_id WHERE a.series_id IN (%s) AND b.series_id NOT IN (%s) AND x.folder_id IN (%s) GROUP BY x.id, y.id ORDER BY n DESC"
        % (",".join(str(i) for i in sids) or "0", ",".join(str(i) for i in sids) or "0", ",".join(str(i) for i in folder_ids(c, root)))).fetchone()
    if clash:
        one = clash["n"] == 1
        raise HTTPException(409, "%d photo%s of “%s” %s also in “%s” of that folder. Inside a folder a photo is in one series: take %s out of one of the two first."
                            % (clash["n"], "" if one else "s", clash["mine"], "is" if one else "are", clash["name"], "it" if one else "them"))


@router.post("/api/series/{sid}/move")
def series_move(sid: int, body: Dict[str, Any] = Body(...)):
    """Move a series into a folder (folder_id, at any depth) or out to the top level (folder_id null); "before": the
    series it goes in front of, "before_folder": the folder (else last). Its tag moves with it
    (series|<folder>|…|<name>). Inside one top-level folder with a linked board, its frame goes to that folder's group frame."""
    s = api._series(sid)
    fid = body.get("folder_id")
    fid = int(fid) if fid else None
    with db.write_lock:
        c = db.conn()
        if fid is not None:
            folder_row(fid)
        before = {sid: tag_of(s)}
        old_root = root_of(c, s["folder_id"]) if s["folder_id"] else None
        new_root = root_of(c, fid) if fid is not None else None
        if fid != s["folder_id"]:
            if sibling_clash(c, s["name"], fid, "s"):
                raise HTTPException(409, "a folder is named “%s” too: rename one of them first" % s["name"])
            if new_root != old_root:
                if new_root is not None:
                    _photo_clash(c, [sid], new_root, s["name"])
                if old_root:  # leaving a top-level folder: off its board
                    on_delete(c, s)
                color = s["color"] or ""
                if not color and new_root is not None:
                    color = PALETTE[len(series_under(c, new_root)) % len(PALETTE)]
                # in a folder the order comes from the folder; a series that leaves one keeps its order and stays unlinked
                c.execute("UPDATE series SET folder_id=?, linked=0, color=?, pos=NULL WHERE id=?", (fid, color, sid))
                write_order(c, sid, order_of(c, sid))  # every photo gets a place (a "maybe" pile had none)
            else:
                c.execute("UPDATE series SET folder_id=?, pos=NULL WHERE id=?", (fid, sid))
                if folder_row(new_root)["linked"]:
                    b = Board(c, "f", new_root)
                    fr = b.frame_of_series(sid)
                    if fr is not None:
                        b.put_into(fr, b.frame_of_sub(fid))
        _insert_entry(c, fid, ("s", sid), body)
        if new_root is not None and folder_row(new_root)["linked"] and fid != s["folder_id"]:
            ensure_frames(c, new_root, Board(c, "f", new_root))
        c.commit()
    res: Dict[str, Any] = {"ok": True}
    r = retag_changed(before)
    if config.write_series_tags and fid != s["folder_id"]:
        res.update(photos=r["photos"], errors=r["errors"])
    return res


@router.post("/api/series-folders/{fid}/move")
def folder_move(fid: int, body: Dict[str, Any] = Body(...)):
    """Move a folder into another one (parent_id, at any depth; not into itself or one of its subfolders) or out
    to the top level (parent_id null); "before" (a series) / "before_folder": what it goes in front of there (else
    last). The tags of every series in it follow. Its group frame moves with everything in it; when it changes
    its top-level folder, its part of the board goes to the other board."""
    parent = int(body["parent_id"]) if body.get("parent_id") else None
    with db.write_lock:
        c = db.conn()
        f = folder_row(fid)
        if parent is not None:
            folder_row(parent)
            if parent in folder_ids(c, fid):
                raise HTTPException(400, "a folder can not be moved into itself or into one of its subfolders")
        before = tags_before(c, fid)
        if parent != f["parent_id"]:
            if sibling_clash(c, f["name"], parent, "f"):
                raise HTTPException(409, "a series is named “%s” too: rename one of them first" % f["name"])
            old_root = root_of(c, fid)
            new_root = root_of(c, parent) if parent is not None else fid
            if new_root != old_root:
                if parent is not None:
                    _photo_clash(c, list(before), new_root, f["name"])
                color = f["color"] or ""
                if parent is not None and not color:
                    color = PALETTE[(c.execute("SELECT COUNT(*) n FROM series_folders WHERE parent_id IS NOT NULL").fetchone()["n"] + 3) % len(PALETTE)]
                c.execute("UPDATE series_folders SET parent_id=?, pos=NULL, color=? WHERE id=?", (parent, color, fid))
                transplant(c, fid, old_root, new_root, parent)
            else:
                c.execute("UPDATE series_folders SET parent_id=?, pos=NULL WHERE id=?", (parent, fid))
                if folder_row(new_root)["linked"]:
                    b = Board(c, "f", new_root)
                    g = b.frame_of_sub(fid)
                    if g is not None:
                        b.put_into(g, b.frame_of_sub(parent))
        _insert_entry(c, parent, ("f", fid), body)
        root = root_of(c, fid)
        if folder_row(root)["linked"]:
            ensure_frames(c, root, Board(c, "f", root))
        c.commit()
    r = retag_changed(before)
    return {"ok": True, "photos": r["photos"], "errors": r["errors"]}


# ---------------------------------------------------------------- a folder's board
def _add_key(fid: int) -> str:
    return "series_folder_add.%d" % fid


def _lanes(c, fid: int) -> list:
    out = []
    for s in series_under(c, fid):
        n = c.execute("SELECT COUNT(*) n FROM series_items si JOIN photos p ON p.id=si.photo_id WHERE si.series_id=? AND p.missing=0", (s["id"],)).fetchone()["n"]
        out.append({"id": s["id"], "name": s["name"], "color": s["color"] or "", "count": n, "folder_id": s["folder_id"],
                    "path": [p["name"] for p in folder_path(c, s["folder_id"])[1:]]})
    return out


def _groups(c, fid: int) -> list:
    """The subfolders below a folder, in tree order, with what is in them (their own subfolders included)."""
    out = []
    for k, r, depth in tree_rows(c, fid):
        if k == "f":
            su = series_under(c, r["id"])
            n = c.execute("SELECT COUNT(*) n FROM series_items si JOIN photos p ON p.id=si.photo_id WHERE p.missing=0 AND si.series_id IN (%s)"
                          % (",".join(str(x["id"]) for x in su) or "0")).fetchone()["n"]
            out.append({"id": r["id"], "name": r["name"], "color": r["color"] or "", "parent_id": r["parent_id"], "depth": depth,
                        "series_count": len(su), "count": n})
    return out


def _folder_head(c, fid: int) -> dict:
    """What every answer about a folder says: the folder asked for, the top-level folder whose board it is on, the way down."""
    req = folder_row(fid)
    root = folder_row(root_of(c, fid))
    return {"folder": {"id": req["id"], "name": req["name"], "linked": bool(root["linked"]), "parent_id": req["parent_id"]}, "linked": bool(root["linked"]),
            "root": {"id": root["id"], "name": root["name"]}, "path": folder_path(c, fid)}


@router.get("/api/series-folders/{fid}/board")
def folder_board(fid: int):
    """The shared board of the top-level folder this folder lies in (a subfolder has no board of its own: `focus`
    is its group frame on that board)."""
    c = db.conn()
    head = _folder_head(c, fid)
    root = head["root"]["id"]
    frames = [dict(r) for r in c.execute("SELECT %s,series_id,sub_id FROM folder_frames WHERE folder_id=? ORDER BY id" % FRAME_COLS, (root,)).fetchall()]
    pos = {r["photo_id"]: r for r in c.execute("SELECT photo_id,x,y,version FROM folder_items WHERE folder_id=?", (root,)).fetchall()}
    items = api.photo_dicts(list(pos.keys()))
    mem, ords = members(c, root)
    nums = {sid: nums_of(c, sid) for sid in set(mem.values())}
    from . import versions
    spins = {(r["series_id"], r["photo_id"]): r["version"] for r in c.execute(
        "SELECT series_id, photo_id, version FROM series_items WHERE series_id IN (%s)" % (",".join(str(x) for x in set(mem.values())) or "0")).fetchall()}
    versions.decorate(items, {k: (spins.get((mem.get(k), k)) if spins.get((mem.get(k), k)) is not None else v["version"]) for k, v in pos.items()})
    for it in items:
        r = pos[it["id"]]
        sid = mem.get(it["id"])
        it["x"], it["y"], it["ord"], it["series_id"] = r["x"], r["y"], ords.get(it["id"]), sid
        it["num"] = nums[sid].get(it["id"]) if sid else None
    try:
        last_add = json.loads(db.get_setting(_add_key(fid)) or "null")
    except ValueError:
        last_add = None
    r = c.execute("SELECT data FROM folder_shapes WHERE folder_id=?", (root,)).fetchone()
    try:
        shapes = json.loads(r["data"]) if r else []
    except ValueError:
        shapes = []
    focus = next((f["id"] for f in frames if f["sub_id"] == fid), None) if fid != root else None
    return {**head, "lanes": _lanes(c, root), "groups": _groups(c, root), "focus": focus,
            "frames": frames, "items": items, "last_add": last_add, "shapes": shapes}


@router.put("/api/series-folders/{fid}/shapes")
def folder_shapes_save(fid: int, body: Dict[str, Any] = Body(...)):
    folder_row(fid)
    fid = root_of(db.conn(), fid)
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


@router.put("/api/series-folders/{fid}/pin")
def folder_pin(fid: int, body: Dict[str, Any] = Body(...)):
    """"Use in series" on a folder board (R17): the entry shows version `version` (null: the cover). A photo that is
    in one of the folder's series is pinned there (the series and the board agree); another entry on this board is
    pinned on the board itself."""
    from . import versions
    c = db.conn()
    root = _folder_head(c, fid)["root"]["id"]
    pid = int(body.get("photo_id"))
    n = body.get("version")
    n = None if n is None else int(n)
    mem, _ords = members(c, root)
    with db.write_lock:
        try:
            if mem.get(pid):
                versions.set_pin("series_items", "series_id", mem[pid], pid, n)
            else:
                versions.set_pin("folder_items", "folder_id", root, pid, n)
        except KeyError:
            raise HTTPException(404, "no such version")
        except LookupError:
            raise HTTPException(404, "the photo is not on this board")
        c.commit()
    return {"ok": True, "photo_id": pid, "version": n}


@router.put("/api/series-folders/{fid}/board")
def folder_board_save(fid: int, body: Dict[str, Any] = Body(...)):
    """Save places and frames of a folder's board (always the board of its top-level folder). Linked: the folder
    tree, membership and order are then read from it: a series lies in the folder of the innermost group frame
    around its frame; a new frame becomes a new series (with "group": true, or when it holds frames: a new
    subfolder); a renamed frame renames its series / subfolder; a frame's colour is theirs. A series' frame or a
    subfolder's group frame can not be dropped by leaving it out (delete the series / dissolve the folder instead).

    items: [{id,x,y}] (photos not on the board yet are put on it); frames: the whole list; remove: photo ids to
    take off the board; restore: true when an undo/redo puts an earlier state back (a series or subfolder that
    was made by drawing a frame and is empty again goes away with its frame)."""
    req = fid
    folder_row(fid)
    warnings: List[str] = []
    moves: list = []
    gone_of: list = []
    with db.write_lock:
        c = db.conn()
        fid = root_of(c, fid)
        f = folder_row(fid)
        linked = bool(f["linked"])
        sers = {s["id"]: s for s in series_under(c, fid)}
        subs = set(folder_ids(c, fid)) - {fid}
        before = {i: tag_of(x) for i, x in sers.items()}
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
                    gone_of.append((pid, mem[pid]))
        dropped: list = []
        dropped_subs: list = []
        if "frames" in body:
            keep = []
            claimed = set()
            for fr in body["frames"]:
                vals = (fr.get("label") or "", float(fr["x"]), float(fr["y"]), float(fr["w"]), float(fr["h"]),
                        str(fr.get("color") or "")[:20], str(fr.get("border") or "")[:10])
                row = c.execute("SELECT id, series_id, sub_id FROM folder_frames WHERE id=? AND folder_id=?", (int(fr["id"]), fid)).fetchone() if fr.get("id") else None
                if row:
                    c.execute("UPDATE folder_frames SET label=?,x=?,y=?,w=?,h=?,color=?,border=? WHERE id=?", vals + (row["id"],))
                    keep.append(row["id"])
                    claimed.add(row["series_id"])
                    claimed.add(("f", row["sub_id"]))
                else:  # new, or brought back by undo: it is its old series / subfolder again if that still exists and has no frame
                    sid = fr.get("series_id")
                    sid = int(sid) if sid and int(sid) in sers and int(sid) not in claimed and not c.execute(
                        "SELECT 1 FROM folder_frames WHERE series_id=?", (int(sid),)).fetchone() else None
                    sub = fr.get("sub_id")
                    sub = int(sub) if sub and sid is None and int(sub) in subs and ("f", int(sub)) not in claimed and not c.execute(
                        "SELECT 1 FROM folder_frames WHERE sub_id=?", (int(sub),)).fetchone() else None
                    if linked and sid is None and sub is None and fr.get("group"):  # drawn as a subfolder
                        sub, name, color = make_subfolder(c, fid, vals[0], vals[5])
                        vals = (name,) + vals[1:5] + (color, vals[6])
                        subs.add(sub)
                    keep.append(c.execute("INSERT INTO folder_frames(label,x,y,w,h,color,border,folder_id,series_id,sub_id,maybe) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                                          vals + (fid, sid, sub)).lastrowid)
                    claimed.add(sid)
                    claimed.add(("f", sub))
            for r in c.execute("SELECT id, series_id, sub_id FROM folder_frames WHERE folder_id=?", (fid,)).fetchall():
                if r["id"] in keep:
                    continue
                if linked and (r["series_id"] in sers or r["sub_id"] in subs):
                    if body.get("restore"):
                        (dropped if r["series_id"] in sers else dropped_subs).append(r["series_id"] if r["series_id"] in sers else r["sub_id"])
                        c.execute("DELETE FROM folder_frames WHERE id=?", (r["id"],))
                    continue  # a series' frame / a subfolder's group frame stays
                c.execute("DELETE FROM folder_frames WHERE id=?", (r["id"],))
        if linked:
            b = Board(c, "f", fid)
            for fr in b.frames:  # a frame's name and colour are its series' / its subfolder's
                s = sers.get(fr.get("series_id"))
                sub = folder_row(fr["sub_id"]) if not s and fr.get("sub_id") in subs else None
                x = s or sub
                if not x:
                    continue
                label = clean_name(fr["label"])
                if label and label != x["name"]:
                    if s:
                        taken = c.execute("SELECT 1 FROM series WHERE name=? AND id<>?", (label, s["id"])).fetchone() or sibling_clash(c, label, s["folder_id"], "s")
                    else:
                        taken = c.execute("SELECT 1 FROM series_folders WHERE name=? AND id<>?", (label, sub["id"])).fetchone() or sibling_clash(c, label, sub["parent_id"], "f")
                    if taken:
                        warnings.append("A %s named “%s” exists already; “%s” keeps its name." % ("series" if s else "folder", label, x["name"]))
                        c.execute("UPDATE folder_frames SET label=? WHERE id=?", (x["name"], fr["id"]))
                    else:
                        c.execute("UPDATE %s SET name=? WHERE id=?" % ("series" if s else "series_folders"), (label, x["id"]))
                        c.execute("UPDATE folder_frames SET label=? WHERE id=?", (label, fr["id"]))
                elif not label:
                    c.execute("UPDATE folder_frames SET label=? WHERE id=?", (x["name"], fr["id"]))
                if (fr.get("color") or "") != (x["color"] or ""):
                    c.execute("UPDATE %s SET color=? WHERE id=?" % ("series" if s else "series_folders"), (fr.get("color") or "", x["id"]))
            adopt_free_frames(c, fid, b)
            sync_structure(c, fid, b, warnings)
            moves += sync_folder(c, fid, b)
            for sid in dropped:  # undo of "draw a frame": the series it made goes if nothing is in it
                if not c.execute("SELECT 1 FROM series_items WHERE series_id=? LIMIT 1", (sid,)).fetchone():
                    c.execute("DELETE FROM series_frames WHERE series_id=?", (sid,))
                    c.execute("DELETE FROM series_shapes WHERE series_id=?", (sid,))
                    c.execute("DELETE FROM settings WHERE key=?", (api._add_criteria_key(sid),))
                    c.execute("DELETE FROM series WHERE id=?", (sid,))
            for sub in dropped_subs:  # undo of "New subfolder": it goes if nothing is in it
                if not c.execute("SELECT 1 FROM series WHERE folder_id=? LIMIT 1", (sub,)).fetchone() and \
                        not c.execute("SELECT 1 FROM series_folders WHERE parent_id=? LIMIT 1", (sub,)).fetchone():
                    c.execute("DELETE FROM series_folders WHERE id=?", (sub,))
            ensure_frames(c, fid, Board(c, "f", fid))
        c.commit()
        gone_moves = []
        for pid, sid in gone_of:
            x = c.execute("SELECT * FROM series WHERE id=?", (sid,)).fetchone()
            if x:
                gone_moves.append((pid, tag_of(x), None))
    retag_changed(before)  # renamed or moved series / subfolders: their tags first, then who carries which
    retag(gone_moves + moves)
    if not c.execute("SELECT 1 FROM series_folders WHERE id=?", (req,)).fetchone():
        req = fid
    out = folder_board(req)
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
    if not any(f.get(k) for k in ("folders", "folder", "ratings", "labels", "tags", "tag", "lenses", "cameras", "formats", "date_from", "date_to", "q", "edited", "month", "names")):
        raise HTTPException(400, "pick at least one folder or criterion")
    c = db.conn()
    where, args, join = query.build_where(f)
    total = c.execute("SELECT COUNT(*) n FROM photos p%s WHERE %s" % (join, where), args).fetchone()["n"]
    where += " AND p.id NOT IN (SELECT si.photo_id FROM series_items si JOIN series sx ON sx.id=si.series_id WHERE sx.folder_id IN (%s))" \
        % ",".join(str(i) for i in folder_ids(c, root_of(c, int(fid))))
    order = query.build_order(sort or [{"f": "taken", "d": "asc"}, {"f": "name", "d": "asc"}], 1, False)
    rows = c.execute("SELECT p.id, p.folder, p.preview_ver FROM photos p%s WHERE %s ORDER BY %s" % (join, where, order), args).fetchall()
    return total, rows


@router.post("/api/series-folders/{fid}/match")
def folder_match(fid: int, body: Dict[str, Any] = Body(...)):
    folder_row(fid)
    total, rows = _match_folder(fid, body.get("filters") or {}, body.get("sort") or [])
    folders = {r["folder"] for r in rows}
    return {"total": total, "new": len(rows), "limit": api.ADD_LIMIT, "folders": len(folders),
            "sample": [{"id": r["id"], "v": r["preview_ver"]} for r in rows[:14]], "names_missing": api.names_missing(body.get("filters") or {})}


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
        if api._series(sid)["folder_id"] not in folder_ids(c, root_of(c, fid)):
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
    if sid is not None:  # versions pinned to entries of this series (R17)
        from . import versions
        versions.decorate(out, {r["photo_id"]: r["version"] for r in c.execute("SELECT photo_id, version FROM series_items WHERE series_id=?", (sid,)).fetchall()})
    return out


@router.get("/api/series-folders/{fid}/strip")
def folder_strip(fid: int):
    """A folder as lanes: one per series, in tree order (its subfolders' series too), each with its photos in
    order. `tree` is what to draw from top to bottom: {"folder": id, "depth"} (a group heading, see `groups`) or
    {"series": id, "depth"} (a lane)."""
    c = db.conn()
    head = _folder_head(c, fid)
    root = head["root"]["id"]
    framed = {r["series_id"] for r in c.execute("SELECT series_id FROM folder_frames WHERE folder_id=?", (root,)).fetchall()}
    _, ords = members(c, root)
    lanes, tree = [], []
    for k, s, depth in tree_rows(c, fid):
        if k == "f":
            tree.append({"folder": s["id"], "depth": depth})
            continue
        tree.append({"series": s["id"], "depth": depth})
        lanes.append({"key": "s%d" % s["id"], "series": {"id": s["id"], "name": s["name"], "color": s["color"] or ""},
                      "label": s["name"], "color": s["color"] or "", "maybe": 0, "frame": head["linked"] and s["id"] in framed,
                      "folder_id": s["folder_id"], "depth": depth,
                      "photos": _photos(c, order_of(c, s["id"]), s["id"], nums_of(c, s["id"]), ords)})
    return {**head, "lanes": lanes, "groups": _groups(c, fid), "tree": tree}


@router.post("/api/series-folders/{fid}/strip/move")
def folder_strip_move(fid: int, body: Dict[str, Any] = Body(...)):
    """Put photos (ids, in the order they should stand) into the lane of series `series`, in front of photo
    `before` (null: at the end). Photos from the other series of the top-level folder move to this one. Linked:
    the series' frame on the board is re-flowed in the new order (the group frames around it grow with it)."""
    sid = int(body.get("series") or 0)
    before = body.get("before")
    moves = []
    req = fid
    with db.write_lock:
        c = db.conn()
        fid = root_of(c, fid)
        f = folder_row(fid)
        sers = {s["id"]: s for s in series_under(c, fid)}
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
                moves.append((pid, tag_of(sers[old]) if old else None, tag_of(sers[sid])))
        write_order(c, sid, new)
        if f["linked"]:
            b = Board(c, "f", fid)
            ensure_frames(c, fid, b)
            b.lay(b.frame_of_series(sid), new)
            moves += sync_folder(c, fid, b)  # the board has the last word; after a re-flow it says the same
        c.commit()
    retag(moves)
    return folder_strip(req)


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
    par = frame_parents(fr)
    holders = {id(p) for p in par.values() if p is not None}
    # a frame that only holds other frames is no chapter of its own: its frames are
    lanes = [("f%d" % f["id"], f, [it["id"] for it in in_rows(groups.get(id(f), []))]) for f in fr
             if not f["maybe"] and not (id(f) in holders and not groups.get(id(f)))]
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
    sl = series_lanes(c, s)
    for key, f, ids in sl:
        lanes.append({"key": key, "series": None, "label": f["label"] if f else ("Outside the frames" if key == "out" else s["name"]),
                      "color": (f.get("color") or "") if f else (s["color"] or ""), "maybe": int(bool(f and f["maybe"])), "frame": f is not None,
                      "photos": _photos(c, ids, sid, nums, ords)})
    extra = board_extra(s, [])
    out = {"series": {"id": s["id"], "name": s["name"], "folder_id": s["folder_id"], "color": s["color"] or ""}, "folder": extra["folder"],
           "linked": extra["linked"], "lanes": lanes}
    # frames inside frames: the strip shows the same nesting, a frame holding frames as a heading that folds
    if is_linked(s):
        frames = [dict(r) for r in c.execute("SELECT * FROM series_frames WHERE series_id=?", (sid,)).fetchall()]
        par = frame_parents(frames)
        if any(p is not None for p in par.values()):
            byid = {f["id"]: f for f in frames}
            depth = {}
            for f in frames:
                d, p = 0, par[id(f)]
                while p is not None and d < 50:
                    d, p = d + 1, par[id(p)]
                depth[f["id"]] = d
            holders = {p["id"] for p in par.values() if p is not None}
            count = {l["key"]: len(l["photos"]) for l in lanes}
            under: Dict[int, int] = {}
            for f in frames:  # photos in a holder and everything inside it
                p = f
                while p is not None:
                    if p["id"] in holders:
                        under[p["id"]] = under.get(p["id"], 0) + count.get("f%d" % f["id"], 0)
                    p = par[id(p)]
            tree = []
            lane_keys = {l["key"] for l in lanes}
            for f in frames_sorted(frames):
                if f["maybe"]:
                    continue
                if f["id"] in holders:
                    tree.append({"frame": f["id"], "depth": depth[f["id"]]})
                    if "f%d" % f["id"] in lane_keys:  # its own photos, not in an inner frame
                        tree.append({"lane": "f%d" % f["id"], "depth": depth[f["id"]] + 1})
                elif "f%d" % f["id"] in lane_keys:
                    tree.append({"lane": "f%d" % f["id"], "depth": depth[f["id"]]})
            for l in lanes:
                if l["key"] == "out" or l["maybe"]:
                    tree.append({"lane": l["key"], "depth": 0})
            out["ftree"] = tree
            out["fgroups"] = [{"id": i, "label": byid[i]["label"], "color": byid[i].get("color") or "", "count": under.get(i, 0),
                               "frames": sum(1 for f in frames if par[id(f)] is not None and par[id(f)]["id"] == i)} for i in holders]
    return out


@router.post("/api/series/{sid}/strip/move")
def series_strip_move(sid: int, body: Dict[str, Any] = Body(...)):
    """Put photos (ids, in order) into lane `lane` (a key from GET strip: "f<frame id>", "out", "all") in front
    of photo `before` (null: at the end). Linked: that frame is re-flowed and the order read back from the board."""
    lane = str(body.get("lane") or "all")
    before = body.get("before")
    s = api._series(sid)
    if s["folder_id"]:  # its order is the folder's business (a linked folder board re-flows this series' frame)
        # photos from outside (the strip's photo panel) join the series there, like a move from another lane
        folder_strip_move(s["folder_id"], {"ids": list(body.get("ids") or []), "series": sid, "before": before})
        return series_strip(sid)
    have = set(order_of(db.conn(), sid))
    extra = [int(i) for i in body.get("ids") or [] if int(i) not in have]
    if extra:  # photos dropped in from outside the series: add them first (tag, board), then put them in place
        api.series_add(sid, {"ids": extra})
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


def _frame_lane_order(b: "Board") -> List[int]:
    return [f["id"] for f in frames_sorted(b.frames) if not f["maybe"]]


def move_frame_lane(c, sid: int, fid: int, before: Optional[int]) -> None:
    """Reading order of a linked single series: put frame `fid` in front of frame `before` (None: after the last).
    The frame goes to where `before` lies, with its photos, notes and free arrow ends; `before` and everything after it
    in reading order (and what is loose below) moves down by the frame's height, so nothing overlaps."""
    b = Board(c, "s", sid)
    fr = frames_sorted(b.frames)
    own = {i: owner(it, fr) for i, it in b.items.items()}  # who holds what, before anything moves
    shapes = b.load_shapes()
    sown = b.shape_owners(shapes, fr)
    F = next((f for f in b.frames if f["id"] == fid and not f["maybe"]), None)
    if F is None:
        raise HTTPException(400, "that frame no longer exists")
    order = [f for f in fr if not f["maybe"] and f is not F]
    T = next((f for f in order if f["id"] == before), None) if before is not None else None
    if before is not None and T is None:
        raise HTTPException(400, "that frame no longer exists")
    want = [f["id"] for f in order]
    want.insert(want.index(T["id"]) if T else len(want), fid)
    if T:
        tx, ty = T["x"], T["y"]
        later = {id(f) for f in order[order.index(T):]}
    else:
        tx, ty = F["x"], max([f["y"] + f["h"] for f in order] + [0]) + 40
        later = set()
    dy = F["h"] + 40

    def shift(g, ddx, ddy):  # a frame with its photos, notes and free arrow ends
        g["x"] += ddx
        g["y"] += ddy
        c.execute("UPDATE series_frames SET x=?, y=? WHERE id=?", (g["x"], g["y"], g["id"]))
        for i, it in list(b.items.items()):
            if own.get(i) is g:
                b.set_pos(i, it["x"] + ddx, it["y"] + ddy)
        for obj, pt, o in sown:
            if o is g:
                obj["x"] = pt["x"] = pt["x"] + ddx
                obj["y"] = pt["y"] = pt["y"] + ddy

    for g in b.frames:  # make the gap (maybe-frames and loose things at or below it move too)
        if g is not F and (id(g) in later or (g["maybe"] and g["y"] >= ty - 1)):
            shift(g, 0, dy)
    for i, it in list(b.items.items()):
        if own.get(i) is None and it["y"] >= ty - 1:
            b.set_pos(i, it["x"], it["y"] + dy)
    for obj, pt, o in sown:
        if o is None and pt["y"] >= ty - 1:
            obj["y"] = pt["y"] = pt["y"] + dy
    shift(F, tx - F["x"], ty - F["y"])
    if _frame_lane_order(b) != want:  # a band boundary got in the way: stack the frames under each other, in the wanted order
        byid = {f["id"]: f for f in b.frames}
        y = min(f["y"] for f in b.frames if not f["maybe"])
        for k in want:
            shift(byid[k], 0, y - byid[k]["y"])
            y += byid[k]["h"] + 40
    if sown:
        b.save_shapes(shapes)


@router.patch("/api/series/{sid}/frames/{fid}")
def series_frame_patch(sid: int, fid: int, body: Dict[str, Any] = Body(...)):
    """Rename a frame of a single series' board (its lane in the strip). A folder's frames are series: rename those."""
    api._series(sid)
    label = str(body.get("label") or "").strip()
    if not label:
        raise HTTPException(400, "a frame needs a name")
    with db.write_lock:
        c = db.conn()
        if not c.execute("SELECT 1 FROM series_frames WHERE id=? AND series_id=?", (fid, sid)).fetchone():
            raise HTTPException(404, "that frame no longer exists")
        c.execute("UPDATE series_frames SET label=? WHERE id=?", (label[:120], fid))
        c.commit()
    return series_strip(sid)


@router.post("/api/series/{sid}/strip/lanes")
def series_strip_lanes(sid: int, body: Dict[str, Any] = Body(...)):
    """Reorder the frame lanes of a linked single series: lane `lane` ("f<frame id>") goes in front of lane `before`
    (null: after the last framed lane). The frame moves on the board to the matching place, with its photos."""
    def fid_of(k):
        k = str(k)
        if not k.startswith("f") or not k[1:].isdigit():
            raise HTTPException(400, "not a frame lane")
        return int(k[1:])
    with db.write_lock:
        c = db.conn()
        s = api._series(sid)
        if not is_linked(s):
            raise HTTPException(409, "this series has no frames in its strip (its board is not linked)")
        before = body.get("before")
        move_frame_lane(c, sid, fid_of(body.get("lane")), fid_of(before) if before not in (None, "", "out") else None)
        sync_series(c, sid)
        c.commit()
    return series_strip(sid)


# ---------------------------------------------------------------- link / unlink
def board_parents(c, fid: int, b: Board) -> Dict[tuple, int]:
    """What the board says about the tree: ("f", subfolder) / ("s", series) -> the folder its frame lies in."""
    subs = set(folder_ids(c, fid))
    sids = {s["id"] for s in series_under(c, fid)}
    out: Dict[tuple, int] = {}
    for f in b.frames:
        key = ("f", f["sub_id"]) if f.get("sub_id") in subs and f["sub_id"] != fid else ("s", f["series_id"]) if f.get("series_id") in sids else None
        if key:
            g = next((a for a in b.ancestors(f) if a.get("sub_id") in subs), None)
            out[key] = g["sub_id"] if g else fid
    return out


def tree_differs(c, fid: int, b: Board) -> int:
    """How many series / subfolders lie in another folder on the board than in the tree."""
    n = 0
    for (k, i), par in board_parents(c, fid, b).items():
        r = c.execute("SELECT %s p FROM %s WHERE id=?" % (("parent_id", "series_folders") if k == "f" else ("folder_id", "series")), (i,)).fetchone()
        n += int(r["p"] != par)
    return n


def stray_overlap(b: Board) -> bool:
    """Do two frames lie over each other without one being a group frame around the other?"""
    fr = b.frames
    return any(_overlap(a, x) and not any(g is a for g in b.ancestors(x)) and not any(g is x for g in b.ancestors(a))
               for i, a in enumerate(fr) for x in fr[i + 1:])


def rebuild_layout(c, fid: int, b: Board) -> None:
    """Stack a folder's board from the tree: each series' frame under the one before, re-flowed in its order; a
    subfolder's group frame around the frames of what is in it."""
    groups = {f["sub_id"]: f for f in b.frames if f.get("sub_id")}
    every = b.frames
    x0 = min([f["x"] for f in every] + [40])
    y0 = min([f["y"] for f in every] + [40])
    b.frames = [f for f in every if not f.get("sub_id")]  # (out of the way while the series' frames are laid)

    def place(folder: int, x: float, y: float):
        w = 0.0
        for k, i in entries(c, folder):
            f = b.frame_of_series(i) if k == "s" else groups.get(i)
            if f is None:
                continue
            if k == "s":
                f["x"], f["y"] = x, y
                c.execute("UPDATE folder_frames SET x=?, y=? WHERE id=?", (x, y, f["id"]))
                b.lay(f, order_of(c, i))
            else:
                iw, bottom = place(i, x + GROUP_PAD, y + GROUP_TOP)
                f.update(x=x, y=y, w=max(iw + 2 * GROUP_PAD, 320), h=max(bottom - 40 + GROUP_PAD - y, 140))
                c.execute("UPDATE folder_frames SET x=?, y=?, w=?, h=? WHERE id=?", (f["x"], f["y"], f["w"], f["h"], f["id"]))
            w = max(w, f["w"])
            y = f["y"] + f["h"] + 40
        return w, y
    place(fid, x0, y0)
    b.frames = every


def folder_link_state(c, fid: int) -> dict:
    """How far a folder's board and its strip are apart: what each way of linking them again would do."""
    b = Board(c, "f", fid)
    sers = series_under(c, fid)
    sids = {s["id"] for s in sers}
    subs = set(folder_ids(c, fid)) - {fid}
    target, order, mem = derive_folder(c, fid, b, free_as_series=True)
    unbound = [f for f in b.frames if f.get("series_id") not in sids and f.get("sub_id") not in subs]
    free = [{"id": f["id"], "label": f["label"], "photos": sum(1 for v in target.values() if v == ("new", f["id"]))}
            for f in unbound if not holds_frames(b, f)]
    new_folders = [f["label"] for f in unbound if holds_frames(b, f)]
    missing = [s["name"] for s in sers if b.frame_of_series(s["id"]) is None] + [folder_row(i)["name"] for i in sorted(subs) if b.frame_of_sub(i) is None]
    # photos lying in another series' frame (those in a frame that is no series are counted with that frame)
    moves = sum(1 for pid, v in target.items() if mem.get(pid) != v and not isinstance(v, tuple))
    in_free = sum(f["photos"] for f in free)
    reorder = sum(1 for s in sers if order.get(s["id"], []) != order_of(c, s["id"]))
    loose_ids = [pid for pid in b.items if pid not in mem]
    loose = [{"id": p["id"], "filename": p["filename"]} for p in api.photo_dicts(loose_ids)]
    off = sum(1 for pid in mem if pid not in b.items)
    refiled = tree_differs(c, fid, b)
    return {"frames": len(b.frames), "series": len(sers), "free_frames": free, "missing_frames": missing, "moves": moves,
            "reordered": reorder, "loose": loose, "off_board": off, "new_folders": new_folders, "refiled": refiled,
            "same": not free and not missing and not moves and not in_free and not reorder and not new_folders and not refiled}


@router.get("/api/series-folders/{fid}/link")
def folder_link_get(fid: int):
    f = folder_row(root_of(db.conn(), fid))
    d = folder_link_state(db.conn(), f["id"])
    d["linked"] = bool(f["linked"])
    return d


@router.post("/api/series-folders/{fid}/link")
def folder_link(fid: int, body: Dict[str, Any] = Body(...)):
    """Link or unlink a folder's board. linked false: only the switch. linked true, with mode:
    "strip": arrange the board from the strip (each series' frame re-flowed in its order; frames that are no
             series are removed, their photos return to their series' frames; photos in no series stay on the
             board, outside the frames; every subfolder gets its group frame around its series' frames: where
             the frames do not lie as the tree says, the board is stacked again from the tree);
    "board": take order, membership and folders from the board (a frame that is no series becomes one, a frame
             that holds frames becomes a subfolder, a frame lying in another group frame changes its folder);
    "none":  only the switch (used by undo/redo, which then saves the board it restores)."""
    want = bool(body.get("linked"))
    mode = body.get("mode") or "strip"
    res: Dict[str, Any] = {"linked": want}
    moves: list = []
    before: Dict[int, str] = {}
    with db.write_lock:
        c = db.conn()
        folder_row(fid)
        fid = root_of(c, fid)
        if want and mode == "strip":
            b = Board(c, "f", fid)
            sers = series_under(c, fid)
            sids = {s["id"] for s in sers}
            subs = set(folder_ids(c, fid)) - {fid}
            mem, _ = members(c, fid)
            removed = [f["label"] for f in b.frames if f.get("series_id") not in sids and f.get("sub_id") not in subs]
            for f in [f for f in b.frames if f.get("series_id") not in sids and f.get("sub_id") not in subs]:
                b.delete_frame(f)
            if subs:
                ensure_frames(c, fid, b)
                for i in subs:
                    g, r = b.frame_of_sub(i), folder_row(i)
                    g["label"], g["color"] = r["name"], r["color"] or ""
                    c.execute("UPDATE folder_frames SET label=?, color=? WHERE id=?", (g["label"], g["color"], g["id"]))
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
            fr = frames_sorted([f for f in b.frames if not f.get("sub_id")])
            if subs:
                if tree_differs(c, fid, b) or stray_overlap(b):
                    rebuild_layout(c, fid, b)
            elif any(_overlap(a, x) for i, a in enumerate(fr) for x in fr[i + 1:]):  # frames lying over each other: one under the other
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
            res["undo"] = link_snapshot(c, "f", fid)
            before = tags_before(c, fid)
            b = Board(c, "f", fid)
            c.execute("UPDATE series_folders SET linked=1 WHERE id=?", (fid,))
            made_folders: list = []
            warnings: list = []
            res["created"] = adopt_free_frames(c, fid, b, made_folders)
            res["created_folders"] = made_folders
            ensure_frames(c, fid, b)
            sync_structure(c, fid, b, warnings)
            res["warnings"] = warnings
            moves += sync_folder(c, fid, b)
        else:
            c.execute("UPDATE series_folders SET linked=? WHERE id=?", (int(want), fid))
        c.commit()
    retag_changed(before)
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
    undo = None
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
            undo = link_snapshot(c, "s", sid)
            c.execute("UPDATE series SET linked=1 WHERE id=?", (sid,))
            sync_series(c, sid)
        else:
            c.execute("UPDATE series SET linked=? WHERE id=?", (int(want), sid))
            if not want:
                write_order(c, sid, order_of(c, sid))  # from here on the strip is the order: every photo has a place
        c.commit()
    return {"linked": want, **({"undo": undo} if want and mode == "board" else {})}


def _restore(kind: str, tid: int, body: Dict[str, Any]) -> dict:
    with db.write_lock:
        c = db.conn()
        if kind == "f":
            tid = root_of(c, tid)
        before = tags_before(c, tid) if kind == "f" else {}
        moves, orphans = link_restore(c, kind, tid, int(body.get("token") or 0))
        c.commit()
    retag(moves)
    retag_changed(before)
    if orphans:
        with db.write_lock:
            for t in orphans:
                db.conn().execute("DELETE FROM tags WHERE path=? AND id NOT IN (SELECT tag_id FROM photo_tags)", (t,))
            db.conn().commit()
    return {"ok": True, "moved": len(moves)}


@router.post("/api/series/{sid}/link/restore")
def series_link_restore(sid: int, body: Dict[str, Any] = Body(...)):
    """Undo "Take the order from the board" (token: the "undo" of that answer): order, membership, frames, notes, the link."""
    api._series(sid)
    return _restore("s", sid, body)


@router.post("/api/series-folders/{fid}/link/restore")
def folder_link_restore(fid: int, body: Dict[str, Any] = Body(...)):
    folder_row(fid)
    return _restore("f", fid, body)


# ---------------------------------------------------------------- Send to NAS (see transfer.py and /api/import/index)
def import_place(c, sid: int, se: dict) -> None:
    """A series that just arrived with a send: into its folder (made if needed, with the folders above it), with
    its colour and link state. Anything in the way (a series named like the folder) leaves it one level up, as an
    older server would."""
    color = str(se.get("color") or "")[:20]
    names = [clean_name(n) for n in se.get("folder_path") or []] or ([clean_name(se.get("folder"))] if se.get("folder") else [])
    meta = se.get("folder_meta") or []
    fid = None
    for depth, fname in enumerate(n for n in names if n):
        r = c.execute("SELECT id FROM series_folders WHERE name=?", (fname,)).fetchone()
        if r:
            fid = r["id"]
        elif not sibling_clash(c, fname, fid, "f", sid):
            m = meta[depth] if depth < len(meta) and isinstance(meta[depth], dict) else {}
            fid = c.execute("INSERT INTO series_folders(name, pos, linked, created, parent_id, color) VALUES(?,?,?,?,?,?)",
                            (fname, (m.get("pos") if fid else None) or next_pos(c, fid), int(bool(se.get("folder_linked", True))), time.time(), fid,
                             str(m.get("color") or "")[:20])).lastrowid
        else:
            break
    if fid is None:
        pos = next_top_pos(c)
        linked = 0 if names else int(bool(se.get("linked", True)))
    else:
        pos = se.get("pos") or next_pos(c, fid)
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
            if f["parent_id"]:
                continue
            fid = f["id"]
            sers = {s["name"]: s for s in series_under(c, fid)}
            subs = {folder_row(i)["name"]: i for i in folder_ids(c, fid)[1:]}
            empty = not c.execute("SELECT 1 FROM folder_frames WHERE folder_id=? LIMIT 1", (fid,)).fetchone() and \
                not c.execute("SELECT 1 FROM folder_items WHERE folder_id=? LIMIT 1", (fid,)).fetchone()
            if empty:
                for fr in fp.get("frames") or []:
                    s = sers.get(clean_name(fr.get("series")))
                    c.execute("INSERT INTO folder_frames(folder_id,series_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,?,0,?,?)",
                              (fid, s["id"] if s else None, fr.get("label") or "", float(fr["x"]), float(fr["y"]), float(fr["w"]), float(fr["h"]),
                               str(fr.get("color") or "")[:20], str(fr.get("border") or "")[:10]))
                for fr in fp.get("groups") or []:  # the group frames of its subfolders
                    sub = subs.get(clean_name(fr.get("sub")))
                    if sub:
                        c.execute("INSERT INTO folder_frames(folder_id,sub_id,label,x,y,w,h,maybe,color,border) VALUES(?,?,?,?,?,?,?,0,?,?)",
                                  (fid, sub, fr.get("label") or "", float(fr["x"]), float(fr["y"]), float(fr["w"]), float(fr["h"]),
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
