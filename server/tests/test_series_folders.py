# SPDX-License-Identifier: GPL-3.0-or-later
"""Series folders, the strip and the board link (lpl/series.py): folders, tags, a linked board giving membership
and order, a strip move re-flowing a frame, unlinking and linking again both ways, Send to NAS."""
import itertools
import sqlite3

import pytest

from conftest import PHOTOS

FOLDER = "2025/2025-09-14 Naxos"
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def lib(client):
    """60 more photos (landscape, portrait, panorama) with capture times, indexed."""
    from PIL import Image
    from lpl import db, scanner
    d = PHOTOS / FOLDER
    d.mkdir(parents=True, exist_ok=True)
    for i in range(60):
        size = [(96, 64), (64, 96), (120, 40)][i % 3]
        ex = Image.Exif()
        ts = "2025:09:14 10:%02d:00" % i
        ex[0x0132] = ts
        ex.get_ifd(0x8769)[0x9003] = ts
        Image.new("RGB", size, ((i * 37) % 255, (i * 91) % 255, 120)).save(d / ("DSCF%04d.jpg" % (4800 + i)), exif=ex)
    scanner.scan()
    ids = [r["id"] for r in db.conn().execute("SELECT id FROM photos WHERE folder=? ORDER BY filename", (FOLDER,)).fetchall()]
    assert len(ids) == 60
    return client, ids


@pytest.fixture(autouse=True)
def no_series(client):
    """Every test starts without series and folders (deleted the way a user would)."""
    data = client.get("/api/series").json()
    for f in data["folders"]:
        assert client.delete("/api/series-folders/%d" % f["id"]).status_code == 200
    for s in client.get("/api/series").json()["series"]:
        assert client.delete("/api/series/%d" % s["id"]).status_code == 200


def uniq(name: str) -> str:
    return "%s %d" % (name, next(_seq))


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def new_folder(c, name, series):
    """A folder with series {name: [photo ids]}; returns (folder id, {series name: id})."""
    fid = ok(c.post("/api/series-folders", json={"name": name}))["id"]
    sids = {n: ok(c.post("/api/series", json={"name": n, "folder_id": fid, "ids": ids}))["id"] for n, ids in series.items()}
    return fid, sids


def tags_of(c, pid):
    return [t for t in ok(c.get("/api/photos/%d" % pid))["tags"] if t.startswith("series|")]


def xmp_of(pid):
    from lpl import db, scanner, xmp
    r = db.conn().execute("SELECT relpath FROM photos WHERE id=?", (pid,)).fetchone()
    return xmp.sidecar_path(scanner.abs_of(r["relpath"])).read_text()


def lanes(c, fid):
    return {la["series"]["name"]: [p["id"] for p in la["photos"]] for la in ok(c.get("/api/series-folders/%d/strip" % fid))["lanes"]}


def board(c, fid):
    return ok(c.get("/api/series-folders/%d/board" % fid))


def tile(p):
    w, h = p["width"] or 3, p["height"] or 2
    if p["orientation"] in (5, 6, 7, 8):
        w, h = h, w
    r = w / h
    return (150, round(150 / r)) if r >= 1 else (round(105 * r), 105)


def rect(p):
    return (p["x"], p["y"]) + tile(p)


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def assert_tidy(bd):
    """No two photos overlap, no two frames overlap, and every photo of a series lies inside that series' frame."""
    its = bd["items"]
    for i, a in enumerate(its):
        for b in its[i + 1:]:
            assert not overlap(rect(a), rect(b)), (a["filename"], b["filename"])
    frs = bd["frames"]
    for i, a in enumerate(frs):
        for b in frs[i + 1:]:
            assert not overlap((a["x"], a["y"], a["w"], a["h"]), (b["x"], b["y"], b["w"], b["h"])), (a["label"], b["label"])
    by = {f["series_id"]: f for f in frs}
    for p in its:
        if p["series_id"]:
            f = by[p["series_id"]]
            x, y, w, h = rect(p)
            assert f["x"] <= x and x + w <= f["x"] + f["w"] and f["y"] <= y and y + h <= f["y"] + f["h"], (p["filename"], f["label"])


# ---------------------------------------------------------------- folders
def test_folder_crud_and_tree(lib):
    c, ids = lib
    g, a, n = uniq("Grecia"), uniq("Atene"), uniq("Naxos")
    fid, s = new_folder(c, g, {a: ids[:3], n: ids[3:8]})
    data = ok(c.get("/api/series"))
    f = next(x for x in data["folders"] if x["id"] == fid)
    assert (f["name"], f["linked"], f["count"], f["series_count"]) == (g, True, 8, 2)
    mine = sorted((x for x in data["series"] if x["folder_id"] == fid), key=lambda x: x["pos"])
    assert [x["name"] for x in mine] == [a, n] and [x["count"] for x in mine] == [3, 5]
    assert all(x["color"] for x in mine) and mine[0]["color"] != mine[1]["color"]
    assert {"id", "name", "count", "covers"} <= set(mine[0])  # the fields older clients read are still there
    # names: no second folder or series of that name, and no top-level series named like a folder
    assert c.post("/api/series-folders", json={"name": g}).status_code == 409
    assert c.post("/api/series", json={"name": a}).status_code == 409
    assert c.post("/api/series", json={"name": g}).status_code == 409
    # reorder inside the folder
    ok(c.post("/api/series/order", json={"folder_id": fid, "ids": [s[n], s[a]]}))
    assert list(lanes(c, fid)) == [n, a]
    # delete: the series move to the top level with their photos and order; nothing else goes
    before = lanes(c, fid)
    ok(c.delete("/api/series-folders/%d" % fid))
    data = ok(c.get("/api/series"))
    assert not any(x["id"] == fid for x in data["folders"])
    for name, sid in s.items():
        x = next(x for x in data["series"] if x["id"] == sid)
        assert x["folder_id"] is None and x["linked"] is False
        assert [p["id"] for p in ok(c.get("/api/series/%d/strip" % sid))["lanes"][0]["photos"]] == before[name]
    assert tags_of(c, ids[0]) == ["series|" + a]
    assert c.get("/api/series-folders/%d/board" % fid).status_code == 404


def test_tags_follow_folder_and_series(lib):
    c, ids = lib
    g, a = uniq("Grecia"), uniq("Atene")
    top = uniq("Portraits")
    fid, s = new_folder(c, g, {a: ids[10:13]})
    assert tags_of(c, ids[10]) == ["series|%s|%s" % (g, a)]
    assert "series|%s|%s" % (g, a) in xmp_of(ids[10])  # written to the sidecar, for darktable
    # a top-level series, moved into the folder and out again
    sid = ok(c.post("/api/series", json={"name": top, "ids": ids[13:15]}))["id"]
    assert tags_of(c, ids[13]) == ["series|" + top]
    r = ok(c.post("/api/series/%d/move" % sid, json={"folder_id": fid}))
    assert r["photos"] == 2 and not r["errors"]
    assert tags_of(c, ids[13]) == ["series|%s|%s" % (g, top)]
    assert "series|%s|%s" % (g, top) in xmp_of(ids[13]) and "<rdf:li>series|%s</rdf:li>" % top not in xmp_of(ids[13])
    # rename the folder: every series' tag below it follows
    g2 = uniq("Hellas")
    r = ok(c.patch("/api/series-folders/%d" % fid, json={"name": g2}))
    assert r["photos"] == 5
    assert tags_of(c, ids[10]) == ["series|%s|%s" % (g2, a)] and tags_of(c, ids[14]) == ["series|%s|%s" % (g2, top)]
    assert "series|%s|%s" % (g2, a) in xmp_of(ids[10])
    # rename a series in the folder
    a2 = uniq("Athens")
    ok(c.patch("/api/series/%d" % s[a], json={"name": a2}))
    assert tags_of(c, ids[10]) == ["series|%s|%s" % (g2, a2)]
    assert next(f for f in board(c, fid)["frames"] if f["series_id"] == s[a])["label"] == a2  # its frame is renamed with it
    # out of the folder again
    ok(c.post("/api/series/%d/move" % sid, json={"folder_id": None}))
    assert tags_of(c, ids[13]) == ["series|" + top]
    assert all(p["series_id"] != sid for p in board(c, fid)["items"])
    # deleting a series takes its tag off
    ok(c.delete("/api/series/%d" % s[a]))
    assert tags_of(c, ids[10]) == []


def test_write_series_tags_off(lib, monkeypatch):
    c, ids = lib
    from lpl.config import config
    monkeypatch.setattr(config, "write_series_tags", False)
    g, a, b = uniq("Quiet"), uniq("A"), uniq("B")
    fid, s = new_folder(c, g, {a: ids[16:18], b: ids[18:20]})
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": [ids[16]], "series": s[b], "before": None}))
    ok(c.patch("/api/series-folders/%d" % fid, json={"name": uniq("Quiet2")}))
    ok(c.post("/api/series/%d/move" % s[a], json={"folder_id": None}))
    assert lanes(c, fid)[b] == [ids[18], ids[19], ids[16]]
    for pid in ids[16:20]:
        assert tags_of(c, pid) == []


def test_one_series_per_photo_in_a_folder(lib):
    c, ids = lib
    g, a, b = uniq("Folder"), uniq("A"), uniq("B")
    fid, s = new_folder(c, g, {a: ids[20:23], b: ids[23:25]})
    # adding a photo of A to B moves it (and says so)
    r = ok(c.post("/api/series/%d/items" % s[b], json={"ids": [ids[20]]}))
    assert (r["added"], r["moved"]) == (1, 1)
    la = lanes(c, fid)
    assert la[a] == ids[21:23] and la[b] == [ids[23], ids[24], ids[20]]
    assert tags_of(c, ids[20]) == ["series|%s|%s" % (g, b)]
    # a top-level series sharing a photo with one in the folder can not move in
    top = ok(c.post("/api/series", json={"name": uniq("Top"), "ids": [ids[21], ids[40]]}))["id"]
    r = c.post("/api/series/%d/move" % top, json={"folder_id": fid})
    assert r.status_code == 409 and "one series" in r.json()["detail"]
    assert next(x for x in ok(c.get("/api/series"))["series"] if x["id"] == top)["folder_id"] is None


# ---------------------------------------------------------------- the linked board
def test_linked_board_gives_membership_and_order(lib):
    c, ids = lib
    g, a, b = uniq("Grecia"), uniq("Atene"), uniq("Naxos")
    fid, s = new_folder(c, g, {a: ids[0:6], b: ids[6:10]})
    bd = board(c, fid)
    assert bd["linked"] and {f["series_id"] for f in bd["frames"]} == set(s.values())
    assert {f["label"] for f in bd["frames"]} == {a, b}
    assert_tidy(bd)
    by = {p["id"]: p for p in bd["items"]}
    assert [by[i]["num"] for i in ids[0:6]] == [1, 2, 3, 4, 5, 6] and [by[i]["num"] for i in ids[6:10]] == [1, 2, 3, 4]  # numbers restart per series
    fa = next(f for f in bd["frames"] if f["series_id"] == s[a])
    fb = next(f for f in bd["frames"] if f["series_id"] == s[b])
    # move the first photo of Atene into Naxos' frame, left of everything in its first row; and swap two inside Atene
    first_b = by[ids[6]]
    moved = {"id": ids[0], "x": fb["x"] + 2, "y": first_b["y"]}
    swap = [{"id": ids[1], "x": by[ids[2]]["x"], "y": by[ids[2]]["y"]}, {"id": ids[2], "x": by[ids[1]]["x"], "y": by[ids[1]]["y"]}]
    for p in bd["items"]:
        if p["id"] == ids[6]:
            shifted = {"id": p["id"], "x": p["x"] + 20, "y": p["y"]}
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [moved, shifted] + swap}))
    la = lanes(c, fid)
    assert la[b] == [ids[0]] + ids[6:10]
    assert la[a] == [ids[2], ids[1], ids[3], ids[4], ids[5]]
    by = {p["id"]: p for p in bd["items"]}
    assert by[ids[0]]["series_id"] == s[b] and by[ids[0]]["num"] == 1 and by[ids[6]]["num"] == 2
    assert tags_of(c, ids[0]) == ["series|%s|%s" % (g, b)]
    # the Library's custom sort follows: by series, and the folder as a whole (series one after the other)
    q = ok(c.post("/api/photos/query", json={"filters": {"series": s[b]}, "sort": [{"f": "custom", "d": "asc"}]}))
    assert [p["id"] for p in q["items"]] == la[b]
    q = ok(c.post("/api/photos/query", json={"filters": {"series_folder": fid}, "sort": [{"f": "custom", "d": "asc"}]}))
    assert [p["id"] for p in q["items"]] == la[a] + la[b]
    # a photo dragged outside every frame keeps its series and comes last in it
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [{"id": ids[2], "x": fa["x"] + fa["w"] + 900, "y": fa["y"]}]}))
    assert lanes(c, fid)[a] == [ids[1], ids[3], ids[4], ids[5], ids[2]]
    assert tags_of(c, ids[2]) == ["series|%s|%s" % (g, a)]
    # a hidden photo keeps its place but has no number; the ones after it move up
    ok(c.post("/api/photos/hide", json={"ids": [ids[3]], "hidden": True}))
    by = {p["id"]: p for p in board(c, fid)["items"]}
    assert by[ids[3]]["num"] is None and (by[ids[1]]["num"], by[ids[4]]["num"]) == (1, 2)
    ok(c.post("/api/photos/hide", json={"ids": [ids[3]], "hidden": False}))
    assert {p["id"]: p for p in board(c, fid)["items"]}[ids[3]]["num"] == 2


def test_frames_are_series(lib):
    c, ids = lib
    g, a = uniq("Grecia"), uniq("Atene")
    fid, s = new_folder(c, g, {a: ids[0:4]})
    bd = board(c, fid)
    fa = bd["frames"][0]
    by = {p["id"]: p for p in bd["items"]}
    # a new frame drawn around two photos: a new series in the folder, with those two
    x0 = min(by[ids[0]]["x"], by[ids[1]]["x"]) - 10
    new = {"label": "New series", "x": x0, "y": fa["y"] + 30, "w": by[ids[1]]["x"] + 160 - x0, "h": 120, "maybe": 0, "color": "#5cc08a"}
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": bd["frames"] + [new]}))
    made = next(f for f in bd["frames"] if f["id"] != fa["id"])
    assert made["series_id"] and made["label"].startswith("New series")
    la = lanes(c, fid)
    assert set(la[made["label"]]) == {ids[0], ids[1]} and la[a] == ids[2:4]
    lane = next(x for x in bd["lanes"] if x["id"] == made["series_id"])
    assert lane["color"] == "#5cc08a" and lane["count"] == 2
    assert tags_of(c, ids[0]) == ["series|%s|%s" % (g, made["label"])]
    # renaming the frame renames the series (and its tag); recolouring it recolours the lane
    n2 = uniq("Isole")
    frames = [dict(f, label=n2, color="#e07bb0") if f["id"] == made["id"] else f for f in bd["frames"]]
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": frames}))
    lane = next(x for x in bd["lanes"] if x["id"] == made["series_id"])
    assert (lane["name"], lane["color"]) == (n2, "#e07bb0")
    assert tags_of(c, ids[0]) == ["series|%s|%s" % (g, n2)]
    # a name that is taken: the series keeps its name and the answer says why
    frames = [dict(f, label=a) if f["id"] == made["id"] else f for f in bd["frames"]]
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": frames}))
    assert bd["warnings"] and next(f for f in bd["frames"] if f["id"] == made["id"])["label"] == n2
    # leaving a series' frame out of a save does not delete the series: its frame is still there
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": [f for f in bd["frames"] if f["id"] == fa["id"]]}))
    assert {f["series_id"] for f in bd["frames"]} == {s[a], made["series_id"]}
    # deleting the series removes its frame and its photos from the board; the photos themselves stay in the library
    ok(c.delete("/api/series/%d" % made["series_id"]))
    bd = board(c, fid)
    assert [f["series_id"] for f in bd["frames"]] == [s[a]] and {p["id"] for p in bd["items"]} == set(ids[2:4])
    assert ok(c.post("/api/photos/get", json={"ids": ids[0:2]}))["items"][0]["id"] == ids[0] and tags_of(c, ids[0]) == []
    # undo of "delete": the old frame comes back with its photos -> the series is made again
    back = [{"id": i, "x": by[i]["x"], "y": by[i]["y"]} for i in ids[0:2]]
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": back, "frames": bd["frames"] + [dict(made, label=n2)], "restore": True}))
    assert set(lanes(c, fid)[n2]) == {ids[0], ids[1]}
    # undo of "draw a frame": the frame is gone again, its photos are back in Atene's frame -> the empty series goes too
    old = [{"id": i, "x": by[i]["x"], "y": by[i]["y"]} for i in ids[0:2]]
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": old, "frames": [f for f in bd["frames"] if f["series_id"] == s[a]], "restore": True}))
    assert list(lanes(c, fid)) == [a] and lanes(c, fid)[a] == ids[0:4]
    # taking a photo off a linked board takes it out of its series
    ok(c.post("/api/series-folders/%d/remove" % fid, json={"ids": [ids[3]]}))
    assert lanes(c, fid)[a] == ids[0:3] and tags_of(c, ids[3]) == []


def test_strip_move_reflows_frames(lib):
    c, ids = lib
    g, a, b, d = uniq("Grecia"), uniq("Atene"), uniq("Naxos"), uniq("Traghetti")
    fid, s = new_folder(c, g, {a: ids[0:9], b: ids[9:23], d: ids[23:30]})
    assert_tidy(board(c, fid))
    # reorder inside a lane: photo 6 of Naxos to place 3
    r = ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": [ids[14]], "series": s[b], "before": ids[11]}))
    want = ids[9:11] + [ids[14]] + ids[11:14] + ids[15:23]
    assert [p["id"] for p in r["lanes"][1]["photos"]] == want
    assert [p["num"] for p in r["lanes"][1]["photos"]] == list(range(1, 15))
    bd = board(c, fid)
    assert_tidy(bd)
    # the board says the same: reading the frame row by row gives the strip's order
    from lpl import series
    fb = next(f for f in bd["frames"] if f["series_id"] == s[b])
    inside = [dict(p, tw=tile(p)[0], th=tile(p)[1]) for p in bd["items"] if p["series_id"] == s[b]]
    assert [p["id"] for p in series.in_rows(inside)] == want
    assert all(fb["x"] <= p["x"] for p in inside)
    # several photos at once into another lane, in the given order, at its start: membership, order, tags, frames
    grp = [ids[20], ids[3], ids[10]]
    r = ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": grp, "series": s[d], "before": ids[23]}))
    la = {x["series"]["name"]: [p["id"] for p in x["photos"]] for x in r["lanes"]}
    assert la[d] == grp + ids[23:30]
    assert ids[3] not in la[a] and len(la[a]) == 8 and len(la[b]) == 12
    for pid in grp:
        assert tags_of(c, pid) == ["series|%s|%s" % (g, d)]
    bd = board(c, fid)
    assert_tidy(bd)
    assert {p["id"]: p["series_id"] for p in bd["items"]}[ids[3]] == s[d]
    # a small frame that gets many photos grows, and what is below it is pushed down without overlapping
    frames = [dict(f, w=32 + 2 * 160, h=165) if f["series_id"] == s[a] else f for f in bd["frames"]]
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": frames}))
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": la[b], "series": s[a], "before": None}))
    bd = board(c, fid)
    assert_tidy(bd)
    assert len(lanes(c, fid)[a]) == 20 and lanes(c, fid)[b] == []
    # the same moves again change nothing more (the order is stable)
    again = lanes(c, fid)
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": [again[a][0]], "series": s[a], "before": again[a][1]}))
    assert lanes(c, fid) == again


def test_side_by_side_frames_keep_their_photos(lib):
    """A frame that grows pushes only what is below it; a taller frame next to it keeps its photos inside."""
    c, ids = lib
    g, a, b = uniq("Grecia"), uniq("Left"), uniq("Right")
    fid, s = new_folder(c, g, {a: ids[0:2], b: ids[2:14]})
    bd = board(c, fid)
    fa = next(f for f in bd["frames"] if f["series_id"] == s[a])
    fb = next(f for f in bd["frames"] if f["series_id"] == s[b])
    # put Right next to Left, with its photos
    dx, dy = fa["x"] + fa["w"] + 40 - fb["x"], fa["y"] - fb["y"]
    items = [{"id": p["id"], "x": p["x"] + dx, "y": p["y"] + dy} for p in bd["items"] if p["series_id"] == s[b]]
    frames = [dict(f, x=f["x"] + dx, y=f["y"] + dy) if f["id"] == fb["id"] else f for f in bd["frames"]]
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": items, "frames": frames}))
    before = lanes(c, fid)
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": ids[14:30], "series": s[a], "before": None}))
    bd = board(c, fid)
    assert_tidy(bd)
    assert lanes(c, fid)[b] == before[b] and len(lanes(c, fid)[a]) == 18


def test_frame_inside_a_frame_survives_a_reflow(lib):
    """A series drawn as a frame inside another series' frame keeps its photos when the outer one is re-flowed."""
    c, ids = lib
    g, a = uniq("Grecia"), uniq("Atene")
    fid, s = new_folder(c, g, {a: ids[0:8]})
    bd = board(c, fid)
    by = {p["id"]: p for p in bd["items"]}
    fa = bd["frames"][0]
    x0 = by[ids[0]]["x"] - 10
    new = {"label": uniq("Inner"), "x": x0, "y": fa["y"] + 30, "w": by[ids[1]]["x"] + 160 - x0, "h": 120, "maybe": 0}
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": bd["frames"] + [new]}))
    assert set(lanes(c, fid)[new["label"]]) == {ids[0], ids[1]}
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": [ids[7]], "series": s[a], "before": ids[2]}))
    la = lanes(c, fid)
    assert la[a] == [ids[7]] + ids[2:7] and set(la[new["label"]]) == {ids[0], ids[1]}
    assert_tidy(board(c, fid))


def test_unlink_and_link_again(lib):
    c, ids = lib
    g, a, b = uniq("Grecia"), uniq("Atene"), uniq("Naxos")
    fid, s = new_folder(c, g, {a: ids[0:5], b: ids[5:10]})
    ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": False}))
    assert ok(c.get("/api/series"))["folders"][-1]["linked"] is False
    start = lanes(c, fid)
    bd = board(c, fid)
    by = {p["id"]: p for p in bd["items"]}
    fb = next(f for f in bd["frames"] if f["series_id"] == s[b])
    # free moves: a photo of Atene into Naxos' frame, a new frame "Cover candidates" over two photos, one photo off the board
    cover = {"label": "Cover candidates", "x": 3000, "y": 40, "w": 520, "h": 200, "maybe": 0, "color": ""}
    items = [{"id": ids[0], "x": fb["x"] + 30, "y": fb["y"] + 60}, {"id": ids[1], "x": 3020, "y": 80}, {"id": ids[6], "x": 3200, "y": 80}]
    bd = ok(c.put("/api/series-folders/%d/board" % fid, json={"items": items, "frames": bd["frames"] + [cover], "remove": [ids[9]]}))
    assert bd["linked"] is False and len(bd["frames"]) == 3
    assert next(f for f in bd["frames"] if f["label"] == "Cover candidates")["series_id"] is None
    assert lanes(c, fid) == start  # nothing changed: neither membership nor order
    assert tags_of(c, ids[0]) == ["series|%s|%s" % (g, a)] and tags_of(c, ids[9]) == ["series|%s|%s" % (g, b)]
    # the strip alone gives the order now; the board does not move
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": [ids[4]], "series": s[a], "before": ids[0]}))
    assert lanes(c, fid)[a] == [ids[4]] + ids[0:4]
    assert {p["id"]: (p["x"], p["y"]) for p in board(c, fid)["items"]} == {p["id"]: (p["x"], p["y"]) for p in bd["items"]}
    # a photo that is on the board but in no series
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [{"id": ids[50], "x": fb["x"] + 40, "y": fb["y"] + 200}]}))
    st = ok(c.get("/api/series-folders/%d/link" % fid))
    assert (st["frames"], st["series"], st["same"]) == (3, 2, False)
    assert st["free_frames"] == [{"id": st["free_frames"][0]["id"], "label": "Cover candidates", "photos": 2}]
    assert [x["id"] for x in st["loose"]] == [ids[50]] and st["off_board"] == 1

    # ---- A: arrange the board from the strip
    strip = lanes(c, fid)
    r = ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": True, "mode": "strip"}))
    assert r["removed_frames"] == ["Cover candidates"] and [x["id"] for x in r["loose"]] == [ids[50]]
    assert lanes(c, fid) == strip  # the strip won: same series, same order
    bd = board(c, fid)
    assert bd["linked"] and {f["series_id"] for f in bd["frames"]} == set(s.values())
    assert_tidy(bd)
    by = {p["id"]: p for p in bd["items"]}
    assert by[ids[0]]["series_id"] == s[a] and by[ids[9]]["series_id"] == s[b]  # back in their series' frames, also the one that was off the board
    assert by[ids[50]]["series_id"] is None  # in no series: still on the board, outside the frames
    assert ok(c.get("/api/series-folders/%d/link" % fid))["same"] is True
    # the board's undo of A: unlinked again, the old places back, the strip untouched
    ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": False, "mode": "none"}))
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": items, "frames": [f for f in bd["frames"]] + [cover], "restore": True}))
    assert lanes(c, fid) == strip

    # ---- B: take the order from the board
    r = ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": True, "mode": "board"}))
    assert r["created"] == ["Cover candidates"]
    la = lanes(c, fid)
    assert set(la["Cover candidates"]) == {ids[1], ids[6]}  # the frame became a series with the photos lying in it
    assert ids[0] in la[b] and ids[0] not in la[a]  # the photo that lay in Naxos' frame is in Naxos now
    assert tags_of(c, ids[0]) == ["series|%s|%s" % (g, b)] and tags_of(c, ids[1]) == ["series|%s|Cover candidates" % g]
    bd = board(c, fid)
    assert bd["linked"] and all(f["series_id"] for f in bd["frames"]) and len(bd["frames"]) == 3
    ok(c.delete("/api/series/%d" % next(f["series_id"] for f in bd["frames"] if f["label"] == "Cover candidates")))


def test_link_again_untangles_overlapping_frames(lib):
    c, ids = lib
    g, a, b = uniq("Grecia"), uniq("Atene"), uniq("Naxos")
    fid, s = new_folder(c, g, {a: ids[0:6], b: ids[6:12]})
    ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": False}))
    bd = board(c, fid)
    top = min(f["y"] for f in bd["frames"])
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": [dict(f, x=40, y=top) for f in bd["frames"]]}))  # one on top of the other
    strip = lanes(c, fid)
    ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": True, "mode": "strip"}))
    assert lanes(c, fid) == strip
    assert_tidy(board(c, fid))


# ---------------------------------------------------------------- a single series
def single(c, name, ids):
    sid = ok(c.post("/api/series", json={"name": name, "ids": ids}))["id"]
    return sid, ok(c.get("/api/series/%d/board" % sid))


def test_single_series_board_order_is_the_servers(lib):
    c, ids = lib
    sid, bd = single(c, uniq("Walk"), ids[30:38])
    assert bd["linked"] is True and bd["folder"] is None
    assert [{p["id"]: p["num"] for p in bd["items"]}[i] for i in ids[30:38]] == list(range(1, 9))  # added below the frame: outside it, in the order they came
    f1 = bd["frames"][0]
    f2 = {"label": "02", "x": 40, "y": 1500, "w": 900, "h": 320, "maybe": 0}
    # four into the first frame (two rows), two into a second frame, two left outside; the client's "ord" is ignored
    place = {ids[30]: (60, 90), ids[31]: (240, 92), ids[32]: (60, 210), ids[33]: (240, 215),
             ids[34]: (60, 1560), ids[35]: (240, 1560)}
    items = [{"id": i, "x": x, "y": y, "ord": 99} for i, (x, y) in place.items()]
    bd = ok(c.put("/api/series/%d/board" % sid, json={"items": items, "frames": [f1, f2]}))
    by = {p["id"]: p for p in bd["items"]}
    assert [by[i]["ord"] for i in ids[30:38]] == [1, 2, 3, 4, 5, 6, 7, 8]
    # swap inside a row
    ok(c.put("/api/series/%d/board" % sid, json={"items": [{"id": ids[30], "x": 400, "y": 90}]}))
    st = ok(c.get("/api/series/%d/strip" % sid))
    assert [la["label"] for la in st["lanes"]] == ["01", "02", "Outside the frames"]
    assert [[p["id"] for p in la["photos"]] for la in st["lanes"]] == [[ids[31], ids[30], ids[32], ids[33]], ids[34:36], ids[36:38]]
    assert [p["num"] for la in st["lanes"] for p in la["photos"]] == list(range(1, 9))  # the numbers run through the whole series
    # strip: a photo from the second frame to the front of the first; that frame is re-flowed
    k2 = st["lanes"][1]["key"]
    st = ok(c.post("/api/series/%d/strip/move" % sid, json={"ids": [ids[35]], "lane": st["lanes"][0]["key"], "before": ids[31]}))
    assert [[p["id"] for p in la["photos"]] for la in st["lanes"]] == [[ids[35], ids[31], ids[30], ids[32], ids[33]], [ids[34]], ids[36:38]]
    bd = ok(c.get("/api/series/%d/board" % sid))
    q = ok(c.post("/api/photos/query", json={"filters": {"series": sid}, "sort": [{"f": "custom", "d": "asc"}]}))
    assert [p["id"] for p in q["items"]] == [ids[35], ids[31], ids[30], ids[32], ids[33], ids[34], ids[36], ids[37]]
    rects = [rect(p) for p in bd["items"]]
    assert not any(overlap(x, y) for i, x in enumerate(rects) for y in rects[i + 1:])
    # ... and into the lane of the photos outside every frame
    st = ok(c.post("/api/series/%d/strip/move" % sid, json={"ids": [ids[34]], "lane": "out", "before": ids[37]}))
    assert [[p["id"] for p in la["photos"]] for la in st["lanes"]] == [[ids[35], ids[31], ids[30], ids[32], ids[33]], [], [ids[36], ids[34], ids[37]]]
    assert st["lanes"][1]["key"] == k2


def test_single_series_unlink_and_link_again(lib):
    c, ids = lib
    sid, bd = single(c, uniq("Walk"), ids[40:46])
    place = {ids[40]: (60, 90), ids[41]: (240, 90), ids[42]: (420, 90), ids[43]: (60, 1560), ids[44]: (240, 1560), ids[45]: (420, 1560)}
    f2 = {"label": "02", "x": 40, "y": 1500, "w": 900, "h": 320, "maybe": 0}
    bd = ok(c.put("/api/series/%d/board" % sid, json={"items": [{"id": i, "x": x, "y": y} for i, (x, y) in place.items()], "frames": bd["frames"] + [f2]}))
    ok(c.post("/api/series/%d/link" % sid, json={"linked": False}))
    # unlinked: the board moves nothing in the order; the strip is one lane
    ok(c.put("/api/series/%d/board" % sid, json={"items": [{"id": ids[40], "x": 700, "y": 1560}]}))
    st = ok(c.get("/api/series/%d/strip" % sid))
    assert st["linked"] is False and [[p["id"] for p in la["photos"]] for la in st["lanes"]] == [ids[40:46]]
    st = ok(c.post("/api/series/%d/strip/move" % sid, json={"ids": [ids[45]], "lane": "all", "before": ids[40]}))
    order = [ids[45]] + ids[40:45]
    assert [p["id"] for p in st["lanes"][0]["photos"]] == order
    assert ok(c.get("/api/series/%d/link" % sid))["same"] is False
    # A: arrange from the strip: the frames are filled in strip order
    ok(c.post("/api/series/%d/link" % sid, json={"linked": True, "mode": "strip"}))
    st = ok(c.get("/api/series/%d/strip" % sid))
    assert st["linked"] is True and [p["id"] for la in st["lanes"] for p in la["photos"]] == order
    assert [len(la["photos"]) for la in st["lanes"]] == [2, 4]
    # B: take it from the board
    ok(c.post("/api/series/%d/link" % sid, json={"linked": False}))
    ok(c.post("/api/series/%d/strip/move" % sid, json={"ids": [ids[44]], "lane": "all", "before": ids[45]}))
    ok(c.post("/api/series/%d/link" % sid, json={"linked": True, "mode": "board"}))
    assert [p["id"] for la in ok(c.get("/api/series/%d/strip" % sid))["lanes"] for p in la["photos"]] == order
    # a series in a folder never has a linked board of its own
    fid = ok(c.post("/api/series-folders", json={"name": uniq("F")}))["id"]
    ok(c.post("/api/series/%d/move" % sid, json={"folder_id": fid}))
    assert ok(c.get("/api/series/%d/board" % sid))["linked"] is False
    assert c.post("/api/series/%d/link" % sid, json={"linked": True, "mode": "board"}).status_code == 409
    name = ok(c.get("/api/series/%d/board" % sid))["series"]["name"]
    assert lanes(c, fid)[name] == order  # it brought its order along
    # reordering it on its own (opened alone) is the folder's order too, and the folder's board follows
    st = ok(c.post("/api/series/%d/strip/move" % sid, json={"ids": [order[3]], "lane": "all", "before": order[0]}))
    order = [order[3]] + order[:3] + order[4:]
    assert [p["id"] for p in st["lanes"][0]["photos"]] == order and lanes(c, fid)[name] == order
    bd = board(c, fid)
    assert_tidy(bd)
    assert {p["id"]: p["num"] for p in bd["items"]}[order[0]] == 1


def test_add_matching_in_a_folder(lib):
    c, ids = lib
    g, a = uniq("Grecia"), uniq("Atene")
    fid, s = new_folder(c, g, {a: ids[0:3]})
    crit = {"filters": {"folders": [FOLDER]}}
    m = ok(c.post("/api/series-folders/%d/match" % fid, json=crit))
    assert (m["total"], m["new"]) == (60, 57)
    r = ok(c.post("/api/series-folders/%d/add-matching" % fid, json=dict(crit, target={"per_folder": True})))
    assert r["added"] == 57 and len(r["series"]) == 1
    bd = board(c, fid)
    assert len(bd["items"]) == 60 and len(bd["frames"]) == 2
    assert_tidy(bd)
    name = next(x["name"] for x in bd["lanes"] if x["id"] == r["series"][0])
    assert name.startswith("2025-09-14 Naxos") and lanes(c, fid)[name] == ids[3:60]
    assert ok(c.post("/api/series-folders/%d/add-matching" % fid, json=dict(crit, target={"series": s[a]})))["added"] == 0
    ok(c.delete("/api/series/%d" % r["series"][0]))


# ---------------------------------------------------------------- an old library, Send to NAS
def test_old_library_opens_unchanged(tmp_path):
    """A library from before folders: every series is a top-level, linked series; nothing in it changes."""
    from lpl import db
    import test_migration as tm
    p = tmp_path / "lpl.sqlite"
    tm.old_library(p)
    before = tm.dump(p)
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    db.migrate(con)
    db.migrate(con)
    assert tm.dump(p) == before
    row = con.execute("SELECT * FROM series WHERE id=1").fetchone()
    assert (row["name"], row["folder_id"], row["pos"], row["color"], row["linked"]) == ("Walk", None, None, "", 1)
    assert {"series_folders", "folder_frames", "folder_items", "folder_shapes"} <= {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert con.execute("SELECT COUNT(*) FROM series_folders").fetchone()[0] == 0
    assert [r["ord"] for r in con.execute("SELECT ord FROM series_items WHERE series_id=1 ORDER BY photo_id LIMIT 3")] == [1, 2, 3]
    con.close()


def test_send_to_nas_carries_the_folder(lib):
    c, ids = lib
    from lpl import api, db, transfer
    g, a, b, top = uniq("Viaggio"), uniq("Atene"), uniq("Naxos"), uniq("Solo")
    fid, s = new_folder(c, g, {a: ids[50:54], b: ids[54:58]})
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": [ids[53]], "series": s[a], "before": ids[50]}))
    ok(c.put("/api/series-folders/%d/shapes" % fid, json={"shapes": [{"key": "n1", "kind": "note", "x": 5, "y": 5, "w": 180, "h": 110, "text": "hi", "color": "#f5d76e"},
                                                                   {"key": "a1", "kind": "arrow", "a": {"x": 0, "y": 0, "photo": ids[50]}, "b": {"x": 9, "y": 9, "note": "n1"}}]}))
    tid = ok(c.post("/api/series", json={"name": top, "ids": [ids[58]]}))["id"]
    ok(c.post("/api/series/%d/link" % tid, json={"linked": False}))
    want_board = board(c, fid)
    want_lanes = lanes(c, fid)
    rel = {r["id"]: r["relpath"] for r in db.conn().execute("SELECT id, relpath FROM photos").fetchall()}
    sent = ids[50:57] + [ids[58]]  # one photo of Naxos stays behind
    dest = {i: rel[i] for i in sent}
    series = [x for x in transfer._series_payload(dest) if x["name"] in (a, b, top)]
    folders = [x for x in transfer._folders_payload(dest, series)]
    # what an older NAS reads is still there, in the same shape
    for se in series:
        assert {"name", "frames", "items", "shapes"} <= set(se) and {"dest", "x", "y", "ord"} <= set(se["items"][0])
    assert [f["name"] for f in folders] == [g] and folders[0]["linked"] is True
    assert {fr["series"] for fr in folders[0]["frames"]} == {a, b} and len(folders[0]["items"]) == 7
    assert next(x for x in series if x["name"] == a)["folder"] == g and "folder" not in next(x for x in series if x["name"] == top)
    # "the NAS": the same library with those series gone, receiving the send
    ok(c.delete("/api/series-folders/%d" % fid))
    for sid in list(s.values()) + [tid]:
        ok(c.delete("/api/series/%d" % sid))
    assert tags_of(c, ids[50]) == []
    r = ok(c.post("/api/import/index", json={"dests": list(dest.values()), "series": series, "series_folders": folders}))
    assert {x["name"]: (x["created"], x["added"]) for x in r["series"]} == {a: (True, 4), b: (True, 3), top: (True, 1)}
    data = ok(c.get("/api/series"))
    f = next(x for x in data["folders"] if x["name"] == g)
    got = {x["name"]: x for x in data["series"] if x["name"] in (a, b, top)}
    assert got[a]["folder_id"] == f["id"] and got[b]["folder_id"] == f["id"] and got[top]["folder_id"] is None
    assert got[top]["linked"] is False and f["linked"] is True
    assert (got[a]["color"], got[b]["color"]) == tuple(x["color"] for x in want_board["lanes"])
    la = lanes(c, f["id"])
    assert la[a] == want_lanes[a] and la[b] == [p for p in want_lanes[b] if p in sent]  # the order came along
    bd = board(c, f["id"])
    want = {p["id"]: (p["x"], p["y"]) for p in want_board["items"] if p["id"] in sent}
    assert {p["id"]: (p["x"], p["y"]) for p in bd["items"]} == want  # and the folder's board, as it was
    assert {(fr["label"], fr["x"], fr["y"], fr["w"], fr["h"]) for fr in bd["frames"]} == {(fr["label"], fr["x"], fr["y"], fr["w"], fr["h"]) for fr in want_board["frames"]}
    assert [sh["key"] for sh in bd["shapes"]] == ["n1", "a1"] and bd["shapes"][1]["a"]["photo"] == ids[50]
    assert tags_of(c, ids[50]) == ["series|%s|%s" % (g, a)] and tags_of(c, ids[58]) == ["series|" + top]
    # a send without folder data (an older laptop): the series arrive at the top level, as they always did
    old = [{k: v for k, v in se.items() if k in ("name", "frames", "items", "shapes")} for se in series]
    for se in old:
        se["name"] = se["name"] + " old"
    r = ok(c.post("/api/import/index", json={"dests": list(dest.values()), "series": old}))
    data = ok(c.get("/api/series"))
    assert all(x["folder_id"] is None and x["linked"] is True for x in data["series"] if x["name"].endswith(" old"))
    assert api.app is not None


def three_frames(c, ids):
    sid, bd = single(c, uniq("Lanes"), ids[30:36])
    f = bd["frames"][0]
    fr = [dict(f, label="A", x=40, y=40, w=400, h=250), {"label": "B", "x": 40, "y": 400, "w": 400, "h": 250, "maybe": 0},
          {"label": "C", "x": 40, "y": 760, "w": 400, "h": 250, "maybe": 0}]
    items = [{"id": ids[30], "x": 60, "y": 90}, {"id": ids[31], "x": 60, "y": 450}, {"id": ids[32], "x": 60, "y": 810},
             {"id": ids[33], "x": 240, "y": 810}, {"id": ids[34], "x": 60, "y": 1200}, {"id": ids[35], "x": 240, "y": 1200}]
    ok(c.put("/api/series/%d/board" % sid, json={"items": items, "frames": fr}))
    return sid


def test_reorder_frame_lanes(lib):
    c, ids = lib
    sid = three_frames(c, ids)
    st = ok(c.get("/api/series/%d/strip" % sid))
    assert [la["label"] for la in st["lanes"]] == ["A", "B", "C", "Outside the frames"]
    kA, kB, kC = [la["key"] for la in st["lanes"][:3]]
    # C to the front
    st = ok(c.post("/api/series/%d/strip/lanes" % sid, json={"lane": kC, "before": kA}))
    assert [la["label"] for la in st["lanes"]] == ["C", "A", "B", "Outside the frames"]
    assert [[p["id"] for p in la["photos"]] for la in st["lanes"]] == [ids[32:34], [ids[30]], [ids[31]], ids[34:36]]
    bd = ok(c.get("/api/series/%d/board" % sid))
    assert [p["num"] for p in sorted(bd["items"], key=lambda p: p["num"])] == list(range(1, 7))
    assert {p["id"]: p["num"] for p in bd["items"]}[ids[32]] == 1
    assert_tidy_single(bd)
    q = ok(c.post("/api/photos/query", json={"filters": {"series": sid}, "sort": [{"f": "custom", "d": "asc"}]}))
    assert [p["id"] for p in q["items"]][:4] == [ids[32], ids[33], ids[30], ids[31]]
    # A to the end of the framed lanes
    st = ok(c.post("/api/series/%d/strip/lanes" % sid, json={"lane": kA, "before": "out"}))
    assert [la["label"] for la in st["lanes"]] == ["C", "B", "A", "Outside the frames"]
    assert_tidy_single(ok(c.get("/api/series/%d/board" % sid)))
    # unlinked: no frame lanes to move
    ok(c.post("/api/series/%d/link" % sid, json={"linked": False, "mode": "none"}))
    assert c.post("/api/series/%d/strip/lanes" % sid, json={"lane": kA, "before": None}).status_code == 409


def assert_tidy_single(bd):
    frs = bd["frames"]
    for i, a in enumerate(frs):
        for b in frs[i + 1:]:
            assert not overlap((a["x"], a["y"], a["w"], a["h"]), (b["x"], b["y"], b["w"], b["h"])), (a["label"], b["label"])
    for p in bd["items"]:
        x, y, w, h = rect(p)
        assert any(f["x"] <= x and x + w <= f["x"] + f["w"] and f["y"] <= y and y + h <= f["y"] + f["h"] for f in frs) or p["y"] >= 1000


def test_notes_move_with_frames_and_get_out_of_the_way(lib):
    c, ids = lib
    sid = three_frames(c, ids)
    shapes = [{"key": "n1", "kind": "note", "x": 60, "y": 130, "w": 100, "h": 60, "text": "inside A"},
              {"key": "n2", "kind": "note", "x": 60, "y": 470, "w": 100, "h": 60, "text": "inside B"},
              {"key": "n3", "kind": "note", "x": 300, "y": 700, "w": 100, "h": 60, "text": "loose, between B and C"},
              {"key": "n4", "kind": "note", "x": 1800, "y": 700, "w": 100, "h": 60, "text": "far right"},
              {"key": "a1", "kind": "arrow", "a": {"x": 70, "y": 800}, "b": {"photo": ids[30]}},
              {"key": "a2", "kind": "arrow", "a": {"x": 70, "y": 140}, "b": {"x": 90, "y": 150}}]
    ok(c.put("/api/series/%d/shapes" % sid, json={"shapes": shapes}))
    before = {p["id"]: p["y"] for p in ok(c.get("/api/series/%d/board" % sid))["items"]}
    # eight photos into frame A: it grows, B and C (and what is in them) move down by the same amount
    st = ok(c.get("/api/series/%d/strip" % sid))
    ok(c.post("/api/series/%d/strip/move" % sid, json={"ids": ids[34:36] + [ids[32], ids[33], ids[31]], "lane": st["lanes"][0]["key"], "before": ids[30]}))
    bd = ok(c.get("/api/series/%d/board" % sid))
    fa = next(f for f in bd["frames"] if f["label"] == "A")
    grown = fa["h"] - 250
    assert grown > 0
    sh = {s["key"]: s for s in bd["shapes"]}
    assert (sh["n1"]["x"], sh["n1"]["y"]) == (60, 130)          # inside the growing frame: stays
    fb = next(f for f in bd["frames"] if f["label"] == "B")
    assert fb["y"] == 400 + grown
    assert sh["n2"]["y"] == 470 + grown                         # inside a pushed frame: goes with it
    assert sh["n3"]["y"] == 700 + grown                         # loose, below A's old bottom, over its width
    assert sh["a1"]["a"]["y"] == 800 + grown and sh["a1"]["b"] == {"photo": ids[30]}
    assert sh["a2"]["a"]["y"] == 140
    assert sh["n4"]["y"] == 700 and sh["n3"]["x"] == 300


def test_notes_move_on_a_folder_board(lib):
    c, ids = lib
    g, a, b = uniq("Grecia"), uniq("Top"), uniq("Bottom")
    fid, s = new_folder(c, g, {a: ids[0:2], b: ids[2:6]})
    bd = board(c, fid)
    fa = next(f for f in bd["frames"] if f["series_id"] == s[a])
    fb = next(f for f in bd["frames"] if f["series_id"] == s[b])
    fa_small = dict(fa, w=32 + 2 * 160, h=165)
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": [], "frames": [fa_small]}))
    bd = board(c, fid)
    fa, fb = (next(f for f in bd["frames"] if f["series_id"] == s[x]) for x in (a, b))
    inside, below = {"key": "in", "kind": "note", "x": fa["x"] + 20, "y": fa["y"] + 20, "w": 80, "h": 50}, \
                    {"key": "lo", "kind": "note", "x": fb["x"] + 20, "y": fb["y"] + 60, "w": 80, "h": 50}
    ok(c.put("/api/series-folders/%d/shapes" % fid, json={"shapes": [inside, below]}))
    ok(c.post("/api/series-folders/%d/strip/move" % fid, json={"ids": ids[6:14], "series": s[a], "before": None}))
    bd = board(c, fid)
    fa2, fb2 = (next(f for f in bd["frames"] if f["series_id"] == s[x]) for x in (a, b))
    grown = fb2["y"] - fb["y"]
    assert grown > 0
    sh = {x["key"]: x for x in bd["shapes"]}
    assert sh["in"]["y"] == inside["y"] and sh["lo"]["y"] == below["y"] + grown


def test_manual_order_of_top_level_entries(lib):
    c, ids = lib
    f1, f2 = uniq("Folder A"), uniq("Folder B")
    fa = ok(c.post("/api/series-folders", json={"name": f1}))["id"]
    fb = ok(c.post("/api/series-folders", json={"name": f2}))["id"]
    s1 = ok(c.post("/api/series", json={"name": uniq("Solo"), "ids": ids[0:2]}))["id"]
    s2 = ok(c.post("/api/series", json={"name": uniq("Solo"), "ids": ids[2:4]}))["id"]

    def top():
        d = ok(c.get("/api/series"))
        rows = [("f", f["id"], f["pos"]) for f in d["folders"]] + [("s", s["id"], s["pos"]) for s in d["series"] if s["folder_id"] is None]
        return [(k, i) for k, i, _ in sorted(rows, key=lambda r: r[2])]
    assert top() == [("f", fa), ("f", fb), ("s", s1), ("s", s2)]
    ok(c.post("/api/series/top-order", json={"entries": [{"series": s1}, {"folder": fb}, {"series": s2}, {"folder": fa}]}))
    assert top() == [("s", s1), ("f", fb), ("s", s2), ("f", fa)]
    # a series moved before a folder, and out of a folder to a place among the top-level entries
    ok(c.post("/api/series/%d/move" % s2, json={"folder_id": None, "before_folder": fb}))
    assert top() == [("s", s1), ("s", s2), ("f", fb), ("f", fa)]
    ok(c.post("/api/series/%d/move" % s1, json={"folder_id": fa}))
    ok(c.post("/api/series/%d/move" % s1, json={"folder_id": None, "before": s2}))
    assert top() == [("s", s1), ("s", s2), ("f", fb), ("f", fa)]
    # the old call (series among themselves) leaves the folders where they are
    ok(c.post("/api/series/order", json={"folder_id": None, "ids": [s2, s1]}))
    assert top() == [("s", s2), ("s", s1), ("f", fb), ("f", fa)]
    # new things come last
    f3 = ok(c.post("/api/series-folders", json={"name": uniq("Folder C")}))["id"]
    assert top()[-1] == ("f", f3)
    # an old library (folders first, then series, two separate numberings) keeps that look once
    from lpl import db
    cn = db.conn()
    for n, (k, i) in enumerate([e for e in top() if e[0] == "f"]):
        cn.execute("UPDATE series_folders SET pos=? WHERE id=?", (n + 1, i))
    cn.execute("UPDATE series SET pos=1 WHERE id=?", (s1,))
    cn.execute("UPDATE series SET pos=2 WHERE id=?", (s2,))
    cn.commit()
    db.migrate(cn)
    t = top()
    assert [k for k, _ in t] == ["f", "f", "f", "s", "s"] and t[-2:] == [("s", s1), ("s", s2)]


def test_undo_take_the_order_from_the_board(lib):
    c, ids = lib
    g, a, b = uniq("Grecia"), uniq("Atene"), uniq("Naxos")
    fid, s = new_folder(c, g, {a: ids[0:5], b: ids[5:10]})
    ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": False}))
    bd = board(c, fid)
    fb = next(f for f in bd["frames"] if f["series_id"] == s[b])
    cover = {"label": "Cover candidates", "x": 3000, "y": 40, "w": 520, "h": 200, "maybe": 0, "color": ""}
    items = [{"id": ids[0], "x": fb["x"] + 30, "y": fb["y"] + 60}, {"id": ids[1], "x": 3020, "y": 80}, {"id": ids[6], "x": 3200, "y": 80}]
    ok(c.put("/api/series-folders/%d/board" % fid, json={"items": items, "frames": bd["frames"] + [cover]}))
    ok(c.put("/api/series-folders/%d/shapes" % fid, json={"shapes": [{"key": "n", "kind": "note", "x": 5, "y": 5, "w": 80, "h": 40}]}))
    before = (lanes(c, fid), sorted((f["label"], f["series_id"], f["x"], f["y"]) for f in board(c, fid)["frames"]),
              sorted((p["id"], p["x"], p["y"]) for p in board(c, fid)["items"]), [tags_of(c, i) for i in ids[:10]])
    nseries = len(ok(c.get("/api/series"))["series"])
    r = ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": True, "mode": "board"}))
    assert r["created"] == ["Cover candidates"] and r["undo"]
    assert lanes(c, fid) != before[0] and tags_of(c, ids[1]) == ["series|%s|Cover candidates" % g]
    ok(c.post("/api/series-folders/%d/link/restore" % fid, json={"token": r["undo"]}))
    bd = board(c, fid)
    assert bd["linked"] is False
    assert lanes(c, fid) == before[0]                      # membership and order
    assert (sorted((f["label"], f["series_id"], f["x"], f["y"]) for f in bd["frames"]), sorted((p["id"], p["x"], p["y"]) for p in bd["items"])) == before[1:3]
    assert [tags_of(c, i) for i in ids[:10]] == before[3]  # tags
    assert len(ok(c.get("/api/series"))["series"]) == nseries  # the series made from the extra frame is gone again
    assert bd["shapes"][0]["key"] == "n"
    assert c.post("/api/series-folders/%d/link/restore" % fid, json={"token": r["undo"]}).status_code == 410  # once
    # and it can be done again
    r = ok(c.post("/api/series-folders/%d/link" % fid, json={"linked": True, "mode": "board"}))
    assert r["created"] == ["Cover candidates"]


def test_undo_take_the_order_from_the_board_single(lib):
    c, ids = lib
    sid = three_frames(c, ids)
    ok(c.post("/api/series/%d/link" % sid, json={"linked": False, "mode": "none"}))
    ok(c.put("/api/series/%d/board" % sid, json={"items": [{"id": ids[30], "x": 60, "y": 810}, {"id": ids[32], "x": 60, "y": 90}]}))
    strip = ok(c.get("/api/series/%d/strip" % sid))
    order = [p["id"] for la in strip["lanes"] for p in la["photos"]]
    r = ok(c.post("/api/series/%d/link" % sid, json={"linked": True, "mode": "board"}))
    now = [p["id"] for la in ok(c.get("/api/series/%d/strip" % sid))["lanes"] for p in la["photos"]]
    assert now != order and ids[32] in now[:2]
    ok(c.post("/api/series/%d/link/restore" % sid, json={"token": r["undo"]}))
    st = ok(c.get("/api/series/%d/strip" % sid))
    assert st["linked"] is False and [p["id"] for la in st["lanes"] for p in la["photos"]] == order
