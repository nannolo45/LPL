# SPDX-License-Identifier: GPL-3.0-or-later
"""Nested series folders (lpl/series.py): a folder in a folder, its group frame on the top-level folder's board,
tags at depth, the strip across groups, linking again both ways, the folder filter, Send to NAS."""
import sqlite3

import pytest

from test_series_folders import assert_tidy as flat_tidy  # noqa: F401
from test_series_folders import board, lanes, lib, new_folder, no_series, ok, overlap, rect, tags_of, uniq, xmp_of  # noqa: F401


def sub_folder(c, parent, name, series=None):
    fid = ok(c.post("/api/series-folders", json={"name": name, "parent_id": parent}))["id"]
    return fid, {n: ok(c.post("/api/series", json={"name": n, "folder_id": fid, "ids": ids}))["id"] for n, ids in (series or {}).items()}


def tree(c):
    """{folder name: parent name}, {series name: folder name} as GET /api/series says."""
    d = ok(c.get("/api/series"))
    names = {f["id"]: f["name"] for f in d["folders"]}
    return {f["name"]: names.get(f["parent_id"]) for f in d["folders"]}, {s["name"]: names.get(s["folder_id"]) for s in d["series"]}


def box(f):
    return (f["x"], f["y"], f["w"], f["h"])


def holds(a, b):
    return a["x"] <= b["x"] and b["x"] + b["w"] <= a["x"] + a["w"] and a["y"] <= b["y"] and b["y"] + b["h"] <= a["y"] + a["h"]


def assert_nested_tidy(c, fid):
    """No two photos overlap; frames overlap only where a group frame holds the other; every photo lies in its
    series' frame; every series' frame and subfolder's group frame lies in the group frame of its folder."""
    bd = board(c, fid)
    its, frs = bd["items"], bd["frames"]
    for i, a in enumerate(its):
        for b in its[i + 1:]:
            assert not overlap(rect(a), rect(b)), (a["filename"], b["filename"])
    for i, a in enumerate(frs):
        for b in frs[i + 1:]:
            if overlap(box(a), box(b)):
                assert (a["sub_id"] and holds(a, b)) or (b["sub_id"] and holds(b, a)), (a["label"], b["label"])
    by = {f["series_id"]: f for f in frs if f["series_id"]}
    grp = {f["sub_id"]: f for f in frs if f["sub_id"]}
    for p in its:
        if p["series_id"]:
            f = by[p["series_id"]]
            x, y, w, h = rect(p)
            assert f["x"] <= x and x + w <= f["x"] + f["w"] and f["y"] <= y and y + h <= f["y"] + f["h"], (p["filename"], f["label"])
    d = ok(c.get("/api/series"))
    root = bd["root"]["id"]
    for s in d["series"]:
        if s["id"] in by and s["folder_id"] != root:
            assert holds(grp[s["folder_id"]], by[s["id"]]), s["name"]
    for f in d["folders"]:
        if f["id"] in grp and f["parent_id"] != root:
            assert holds(grp[f["parent_id"]], grp[f["id"]]), f["name"]
    return bd


def test_nesting_crud_and_cycle_guard(lib):
    c, ids = lib
    g, cy, do, a, b, t = uniq("Grecia"), uniq("Cicladi"), uniq("Dodecaneso"), uniq("Naxos"), uniq("Paros"), uniq("Atene")
    root, s = new_folder(c, g, {t: ids[0:2]})
    sub, s2 = sub_folder(c, root, cy, {a: ids[2:4], b: ids[4:6]})
    deep, _ = sub_folder(c, sub, do)
    d = ok(c.get("/api/series"))
    f = {x["name"]: x for x in d["folders"]}
    assert (f[cy]["parent_id"], f[do]["parent_id"], f[g]["parent_id"]) == (root, sub, None)
    assert (f[g]["depth"], f[cy]["depth"], f[do]["depth"]) == (0, 1, 2) and f[do]["root_id"] == root
    assert (f[g]["series_count"], f[g]["total_series"], f[g]["total_count"], f[cy]["total_series"]) == (1, 3, 6, 2)
    # not into itself, not into a subfolder of its own
    assert c.post("/api/series-folders/%d/move" % root, json={"parent_id": deep}).status_code == 400
    assert c.post("/api/series-folders/%d/move" % sub, json={"parent_id": sub}).status_code == 400
    assert c.post("/api/series-folders", json={"name": cy, "parent_id": deep}).status_code == 409
    assert c.post("/api/series-folders", json={"name": a, "parent_id": sub}).status_code == 409  # next to a series of that name
    # one order for subfolders and series inside a folder
    ok(c.post("/api/series/top-order", json={"folder_id": root, "entries": [{"folder": sub}, {"series": s[t]}]}))
    st = ok(c.get("/api/series-folders/%d/strip" % root))
    assert [la["label"] for la in st["lanes"]] == [a, b, t]
    assert st["tree"][0] == {"folder": sub, "depth": 0} and st["tree"][1] == {"series": s2[a], "depth": 1}
    assert [(x["name"], x["depth"], x["series_count"], x["count"]) for x in st["groups"]] == [(cy, 0, 2, 4), (do, 1, 0, 0)]
    # a subfolder alone: only its lanes, on the same board
    alone = ok(c.get("/api/series-folders/%d/strip" % sub))
    assert [la["label"] for la in alone["lanes"]] == [a, b] and alone["root"]["id"] == root and [p["name"] for p in alone["path"]] == [g, cy]
    bd = board(c, sub)
    assert bd["root"]["id"] == root and bd["focus"] == next(fr["id"] for fr in bd["frames"] if fr["sub_id"] == sub)
    assert {fr["label"] for fr in bd["frames"]} == {t, a, b, cy, do}
    assert_nested_tidy(c, root)
    # moving a series between the folders of one tree moves its frame; out to the top level takes it off the board
    ok(c.post("/api/series/%d/move" % s[t], json={"folder_id": deep}))
    assert tree(c)[1][t] == do
    assert_nested_tidy(c, root)
    assert ok(c.get("/api/photos/%d" % ids[0]))["series"][0]["path"] == [g, cy, do]
    # dissolve the middle folder: what is in it moves up, in its place; nothing is deleted
    ok(c.delete("/api/series-folders/%d" % sub))
    fo, se = tree(c)
    assert fo == {g: None, do: g} and se == {a: g, b: g, t: do}
    assert [la["label"] for la in ok(c.get("/api/series-folders/%d/strip" % root))["lanes"]] == [a, b, t]
    assert_nested_tidy(c, root)
    # a subfolder moved out becomes a top-level folder with its part of the board
    ok(c.post("/api/series-folders/%d/move" % deep, json={"parent_id": None}))
    assert tree(c)[0] == {g: None, do: None}
    assert {fr["label"] for fr in board(c, deep)["frames"]} == {t} and {fr["label"] for fr in board(c, root)["frames"]} == {a, b}
    flat_tidy(board(c, deep))
    assert [p["id"] for p in board(c, deep)["items"]] == ids[0:2]
    # and back in: a group frame around what it had
    ok(c.post("/api/series-folders/%d/move" % deep, json={"parent_id": root, "before": s2[a]}))
    assert [la["label"] for la in ok(c.get("/api/series-folders/%d/strip" % root))["lanes"]] == [t, a, b]
    assert_nested_tidy(c, root)
    # deleting the top-level folder: its series and subfolders move to the top level
    ok(c.delete("/api/series-folders/%d" % root))
    fo, se = tree(c)
    assert fo == {do: None} and se == {a: None, b: None, t: do}


def test_one_level_library_migrates(tmp_path):
    """A library with one level of folders (before nesting): every folder is top-level, nothing else changes."""
    from lpl import db
    import test_migration as tm
    p = tmp_path / "lpl.sqlite"
    tm.old_library(p)
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    con.executescript("""
        ALTER TABLE series ADD COLUMN folder_id INTEGER; ALTER TABLE series ADD COLUMN pos INTEGER;
        ALTER TABLE series ADD COLUMN color TEXT DEFAULT ''; ALTER TABLE series ADD COLUMN linked INTEGER DEFAULT 1;
        CREATE TABLE series_folders (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, pos INTEGER, linked INTEGER DEFAULT 1, created REAL);
        CREATE TABLE folder_frames (id INTEGER PRIMARY KEY, folder_id INTEGER NOT NULL, series_id INTEGER, label TEXT NOT NULL,
            x REAL, y REAL, w REAL, h REAL, maybe INTEGER DEFAULT 0, color TEXT DEFAULT '', border TEXT DEFAULT '');
        INSERT INTO series_folders(id, name, pos, linked, created) VALUES (1, 'Grecia', 1, 1, 0), (2, 'Italia', 2, 0, 0);
        UPDATE series SET folder_id=1, pos=1, linked=0 WHERE id=1;
        INSERT INTO folder_frames(folder_id, series_id, label, x, y, w, h) VALUES (1, 1, 'Walk', 40, 40, 800, 200);
    """)
    con.commit()
    before = tm.dump(p)
    db.migrate(con)
    db.migrate(con)
    after = tm.dump(p)
    assert {k: v for k, v in after.items() if k not in ("series_folders", "folder_frames")} == {k: v for k, v in before.items() if k not in ("series_folders", "folder_frames")}
    rows = [dict(r) for r in con.execute("SELECT * FROM series_folders ORDER BY id")]
    assert [(r["name"], r["pos"], r["linked"], r["parent_id"], r["color"]) for r in rows] == [("Grecia", 1, 1, None, ""), ("Italia", 2, 0, None, "")]
    fr = dict(con.execute("SELECT * FROM folder_frames").fetchone())
    assert (fr["series_id"], fr["sub_id"], fr["label"], fr["w"]) == (1, None, "Walk", 800)
    assert con.execute("SELECT folder_id, pos FROM series WHERE id=1").fetchone()[:] == (1, 1)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_tags_follow_at_depth(lib, monkeypatch):
    c, ids = lib
    from lpl.config import config
    g, cy, isl, a, b = uniq("Grecia"), uniq("Cicladi"), uniq("Isole"), uniq("Naxos"), uniq("Paros")
    root, _ = new_folder(c, g, {})
    sub, s = sub_folder(c, root, cy, {a: ids[0:2]})
    assert tags_of(c, ids[0]) == ["series|%s|%s|%s" % (g, cy, a)]
    assert "<rdf:li>series|%s|%s|%s</rdf:li>" % (g, cy, a) in xmp_of(ids[0]) or a in xmp_of(ids[0])
    # rename the middle, the top and the series
    cy2, g2, a2 = uniq("Kykladen"), uniq("Hellas"), uniq("Naxos neu")
    ok(c.patch("/api/series-folders/%d" % sub, json={"name": cy2}))
    assert tags_of(c, ids[0]) == ["series|%s|%s|%s" % (g, cy2, a)]
    ok(c.patch("/api/series-folders/%d" % root, json={"name": g2}))
    ok(c.patch("/api/series/%d" % s[a], json={"name": a2}))
    assert tags_of(c, ids[1]) == ["series|%s|%s|%s" % (g2, cy2, a2)]
    assert cy2 in xmp_of(ids[1]) and cy not in xmp_of(ids[1])
    # a third level, and moving a folder with everything in it
    deep, s3 = sub_folder(c, sub, isl, {b: ids[2:4]})
    assert tags_of(c, ids[2]) == ["series|%s|%s|%s|%s" % (g2, cy2, isl, b)]
    ok(c.post("/api/series-folders/%d/move" % deep, json={"parent_id": root}))
    assert tags_of(c, ids[2]) == ["series|%s|%s|%s" % (g2, isl, b)]
    ok(c.post("/api/series/%d/move" % s[a], json={"folder_id": deep}))
    assert tags_of(c, ids[0]) == ["series|%s|%s|%s" % (g2, isl, a2)]
    ok(c.post("/api/series-folders/%d/move" % deep, json={"parent_id": None}))
    assert tags_of(c, ids[0]) == ["series|%s|%s" % (isl, a2)] and tags_of(c, ids[3]) == ["series|%s|%s" % (isl, b)]
    assert isl in xmp_of(ids[3]) and g2 not in xmp_of(ids[3])
    left = [t["path"] for t in ok(c.get("/api/tags"))["tags"]] if c.get("/api/tags").status_code == 200 and "tags" in c.get("/api/tags").json() else []
    assert not [t for t in left if t.startswith("series|%s" % g2)]
    # switched off: the tree changes, the tags stay
    monkeypatch.setattr(config, "write_series_tags", False)
    ok(c.post("/api/series-folders/%d/move" % deep, json={"parent_id": sub}))
    assert tree(c)[0][isl] == cy2 and tags_of(c, ids[0]) == ["series|%s|%s" % (isl, a2)]


def frames_of(bd):
    return {f["label"]: f for f in bd["frames"]}


def test_linked_board_with_group_frames(lib):
    c, ids = lib
    g, cy, a, b, t = uniq("Grecia"), uniq("Cicladi"), uniq("Naxos"), uniq("Paros"), uniq("Atene")
    root, s = new_folder(c, g, {t: ids[0:3]})
    sub, s2 = sub_folder(c, root, cy, {a: ids[3:6], b: ids[6:9]})
    bd = assert_nested_tidy(c, root)
    fr = frames_of(bd)
    # drag the frame of Atene (with its photos) into the group frame: it is in Cicladi then
    grp = fr[cy]
    dx, dy = grp["x"] + 20 - fr[t]["x"], grp["y"] + grp["h"] + 10 - fr[t]["y"]
    frames = [dict(f) for f in bd["frames"]]
    for f in frames:
        if f["label"] == t:
            f["x"] += dx
            f["y"] += dy
        if f["label"] == cy:
            f["h"] += fr[t]["h"] + 40
    items = [{"id": p["id"], "x": p["x"] + dx, "y": p["y"] + dy} for p in bd["items"] if p["series_id"] == s[t]]
    bd = ok(c.put("/api/series-folders/%d/board" % root, json={"items": items, "frames": frames}))
    assert tree(c)[1] == {t: cy, a: cy, b: cy}
    assert tags_of(c, ids[0]) == ["series|%s|%s|%s" % (g, cy, t)]
    assert [la["label"] for la in ok(c.get("/api/series-folders/%d/strip" % root))["lanes"]] == [a, b, t]  # last in its new folder
    # a photo dragged into another series' frame inside the group changes series; one left in the group frame but
    # outside every series' frame keeps its series
    fr = frames_of(bd)
    items = [{"id": ids[3], "x": fr[b]["x"] + 500, "y": fr[b]["y"] + 50}, {"id": ids[4], "x": fr[cy]["x"] + fr[cy]["w"] + 100, "y": fr[cy]["y"] + 60}]
    frames = [dict(f, w=f["w"] + 300) if f["label"] == cy else dict(f) for f in bd["frames"]]
    bd = ok(c.put("/api/series-folders/%d/board" % root, json={"items": items, "frames": frames}))
    la = lanes(c, root)
    assert la[b] == ids[6:9] + [ids[3]] and la[a] == [ids[5], ids[4]]
    assert tags_of(c, ids[3]) == ["series|%s|%s|%s" % (g, cy, b)]
    # a new frame inside the group frame: a new series in the subfolder; a frame drawn as a group: a new subfolder
    n, k = uniq("Milos"), uniq("Ionie")
    frames = [dict(f) for f in bd["frames"]]
    for f in frames:
        if f["label"] == cy:
            f["h"] += 300
    below = max(f["y"] + f["h"] for f in frames) + 60
    frames += [{"label": n, "x": fr[cy]["x"] + 20, "y": fr[cy]["y"] + fr[cy]["h"] + 20, "w": 500, "h": 240},
               {"label": k, "x": 40, "y": below, "w": 700, "h": 300, "group": True}]
    bd = ok(c.put("/api/series-folders/%d/board" % root, json={"frames": frames}))
    fo, se = tree(c)
    assert se[n] == cy and fo[k] == g and frames_of(bd)[k]["sub_id"] and frames_of(bd)[n]["series_id"]
    # rename and recolour the group frame: the subfolder follows, and so do the tags
    cy2 = uniq("Kykladen")
    frames = [dict(f, label=cy2, color="#123456") if f["label"] == cy else dict(f) for f in bd["frames"]]
    bd = ok(c.put("/api/series-folders/%d/board" % root, json={"frames": frames}))
    f = next(x for x in ok(c.get("/api/series"))["folders"] if x["id"] == sub)
    assert (f["name"], f["color"]) == (cy2, "#123456") and tags_of(c, ids[0]) == ["series|%s|%s|%s" % (g, cy2, t)]
    # the new subfolder's frame dragged into the group frame: a folder in a folder; a frame left out stays
    fr = frames_of(bd)
    frames = [dict(f) for f in bd["frames"] if f["label"] != a]
    for f in frames:
        if f["label"] == cy2:
            f["h"] += 400
        if f["label"] == k:
            f.update(x=fr[cy2]["x"] + 20, y=fr[cy2]["y"] + fr[cy2]["h"] + 20, w=400, h=200)
    bd = ok(c.put("/api/series-folders/%d/board" % root, json={"frames": frames}))
    assert tree(c)[0][k] == cy2 and a in frames_of(bd)
    # drag a series' frame out of every group frame: back in the top-level folder
    frames = [dict(f) for f in bd["frames"]]
    bottom = max(f["y"] + f["h"] for f in frames)
    old = frames_of(bd)[t]
    for f in frames:
        if f["label"] == t:
            f["y"] = bottom + 80
    items = [{"id": p["id"], "x": p["x"], "y": p["y"] + bottom + 80 - old["y"]} for p in bd["items"] if p["series_id"] == s[t]]
    ok(c.put("/api/series-folders/%d/board" % root, json={"frames": frames, "items": items}))
    assert tree(c)[1][t] == g and tags_of(c, ids[0]) == ["series|%s|%s" % (g, t)] and lanes(c, root)[t] == ids[0:3]
    # dissolving the subfolder takes its group frame away, nothing else
    n_frames = len(board(c, root)["frames"])
    ok(c.delete("/api/series-folders/%d" % sub))
    bd = board(c, root)
    assert len(bd["frames"]) == n_frames - 1 and tree(c)[1] == {t: g, a: g, b: g, n: g} and tree(c)[0][k] == g
    assert lanes(c, root)[b] == ids[6:9] + [ids[3]]
    # not linked: frames are only drawn
    ok(c.post("/api/series-folders/%d/link" % root, json={"linked": False}))
    fr = frames_of(bd)
    frames = [dict(f) for f in bd["frames"]]
    for f in frames:
        if f["label"] == k:
            f.update(x=fr[a]["x"] - 30, y=fr[a]["y"] - 60, w=fr[a]["w"] + 60, h=fr[a]["h"] + 90)
    ok(c.put("/api/series-folders/%d/board" % root, json={"frames": frames}))
    assert tree(c)[1][a] == g


def test_strip_moves_across_groups_reflow(lib):
    c, ids = lib
    g, cy, do, a, b, t, r, note = uniq("Grecia"), uniq("Cicladi"), uniq("Dodecaneso"), uniq("Naxos"), uniq("Paros"), uniq("Atene"), uniq("Rodi"), "n1"
    root, s = new_folder(c, g, {})
    sub, s2 = sub_folder(c, root, cy, {a: ids[0:4], b: ids[4:8]})
    deep, s3 = sub_folder(c, sub, do, {r: ids[8:10]})
    s.update(new_folder_series(c, root, {t: ids[10:14]}))
    bd = assert_nested_tidy(c, root)
    fr = frames_of(bd)
    ok(c.put("/api/series-folders/%d/shapes" % root, json={"shapes": [
        {"key": note, "kind": "note", "x": fr[t]["x"] + 5, "y": fr[t]["y"] + fr[t]["h"] + 30, "w": 180, "h": 110, "text": "below"}]}))
    y_note = fr[t]["y"] + fr[t]["h"] + 30
    h0 = {k: v["h"] for k, v in fr.items()}
    y0 = {k: v["y"] for k, v in fr.items()}
    # 30 photos into Naxos (first lane of the group): its frame grows, so does the group frame; Paros, Dodecaneso
    # (with Rodi in it), Atene and the note below are pushed down
    ok(c.post("/api/series-folders/%d/strip/move" % root, json={"ids": ids[20:50], "series": s2[a], "before": ids[1]}))
    bd = assert_nested_tidy(c, root)
    fr = frames_of(bd)
    grow = fr[a]["h"] - h0[a]
    assert grow > 0 and fr[cy]["h"] == h0[cy] + grow and fr[do]["h"] == h0[do]
    assert [fr[x]["y"] - y0[x] for x in (b, do, r, t)] == [grow] * 4 and fr[cy]["y"] == y0[cy]
    assert bd["shapes"][0]["y"] == y_note + grow
    assert lanes(c, root)[a] == [ids[0]] + ids[20:50] + ids[1:4]
    # a photo across groups, three levels down; numbers restart in each series
    st = ok(c.post("/api/series-folders/%d/strip/move" % sub, json={"ids": [ids[10], ids[0]], "series": s3[r], "before": ids[9]}))
    assert [la["label"] for la in st["lanes"]] == [a, b, r]  # asked for the subfolder: its lanes only
    assert [p["id"] for p in st["lanes"][2]["photos"]] == [ids[8], ids[10], ids[0], ids[9]] and [p["num"] for p in st["lanes"][2]["photos"]] == [1, 2, 3, 4]
    assert tags_of(c, ids[10]) == ["series|%s|%s|%s|%s" % (g, cy, do, r)]
    assert_nested_tidy(c, root)
    # a lane dragged out of its group and one into the deepest: the frames follow, nothing overlaps
    ok(c.post("/api/series/%d/move" % s2[b], json={"folder_id": root, "before": s[t]}))
    ok(c.post("/api/series/%d/move" % s[t], json={"folder_id": deep, "before": s3[r]}))
    assert [la["label"] for la in ok(c.get("/api/series-folders/%d/strip" % root))["lanes"]] == [a, t, r, b]
    assert_nested_tidy(c, root)
    assert lanes(c, root)[t] == ids[11:14] and lanes(c, root)[b] == ids[4:8]
    # a group heading dragged: the subfolder among its siblings, then into the top-level folder
    ok(c.post("/api/series-folders/%d/move" % deep, json={"parent_id": sub, "before": s2[a]}))
    assert [la["label"] for la in ok(c.get("/api/series-folders/%d/strip" % root))["lanes"]] == [t, r, a, b]
    ok(c.post("/api/series-folders/%d/move" % deep, json={"parent_id": root}))
    assert tree(c)[0][do] == g and [la["label"] for la in ok(c.get("/api/series-folders/%d/strip" % root))["lanes"]] == [a, b, t, r]
    assert_nested_tidy(c, root)
    ok(c.post("/api/series/%d/items" % s3[r], json={"ids": ids[50:56]}))  # "+ Add photos" into a nested series
    assert_nested_tidy(c, root)


def new_folder_series(c, fid, series):
    return {n: ok(c.post("/api/series", json={"name": n, "folder_id": fid, "ids": i}))["id"] for n, i in series.items()}


def snapshot(c, root):
    bd = board(c, root)
    return (tree(c), lanes(c, root), sorted((f["label"], f["x"], f["y"], f["w"], f["h"], f["sub_id"], f["series_id"]) for f in bd["frames"]),
            sorted((p["id"], p["x"], p["y"]) for p in bd["items"]), tags_of(c, bd["items"][0]["id"]))


def test_link_again_both_ways_and_undo(lib):
    c, ids = lib
    g, cy, a, b, t, k = uniq("Grecia"), uniq("Cicladi"), uniq("Naxos"), uniq("Paros"), uniq("Atene"), uniq("Ionie")
    root, s = new_folder(c, g, {t: ids[0:3]})
    sub, s2 = sub_folder(c, root, cy, {a: ids[3:6], b: ids[6:9]})
    ok(c.post("/api/series-folders/%d/link" % sub, json={"linked": False}))  # (one switch, the top-level folder's)
    assert board(c, root)["linked"] is False and ok(c.get("/api/series"))["folders"][0]["linked"] is False
    bd = board(c, root)
    fr = frames_of(bd)
    # unlinked: Paros is dragged out of the group frame, a frame is drawn around Atene, a photo changes frames; the tree stays
    frames = [dict(f) for f in bd["frames"]]
    bottom = max(f["y"] + f["h"] for f in frames)
    for f in frames:
        if f["label"] == b:
            f["y"] = bottom + 200
    items = [{"id": p["id"], "x": p["x"], "y": p["y"] + bottom + 200 - fr[b]["y"]} for p in bd["items"] if p["series_id"] == s2[b]]
    items += [{"id": ids[0], "x": fr[a]["x"] + 520, "y": fr[a]["y"] + 50}]
    frames.append({"label": k, "x": fr[t]["x"] - 30, "y": fr[t]["y"] - 60, "w": fr[t]["w"] + 60, "h": fr[t]["h"] + 90})
    ok(c.put("/api/series-folders/%d/board" % root, json={"frames": frames, "items": items}))
    assert tree(c)[1] == {t: g, a: cy, b: cy} and lanes(c, root)[t] == ids[0:3]
    st = ok(c.get("/api/series-folders/%d/link" % root))
    assert st["same"] is False and st["refiled"] == 1 and st["new_folders"] == [k] and st["free_frames"] == [] and st["moves"] == 1
    was = snapshot(c, root)
    # take it from the board: the frame around Atene is a subfolder, Paros leaves Cicladi, the photo moves
    r = ok(c.post("/api/series-folders/%d/link" % root, json={"linked": True, "mode": "board"}))
    assert r["created_folders"] == [k] and r["created"] == []
    assert tree(c) == ({g: None, cy: g, k: g}, {t: k, a: cy, b: g})
    assert lanes(c, root)[a] == ids[3:6] + [ids[0]] and tags_of(c, ids[1]) == ["series|%s|%s|%s" % (g, k, t)]
    assert tags_of(c, ids[6]) == ["series|%s|%s" % (g, b)] and tags_of(c, ids[0]) == ["series|%s|%s|%s" % (g, cy, a)]
    assert ok(c.get("/api/series-folders/%d/link" % root))["same"] is True
    # undo: folders, membership, frames, tags as before
    ok(c.post("/api/series-folders/%d/link/restore" % root, json={"token": r["undo"]}))
    assert snapshot(c, root) == was and board(c, root)["linked"] is False
    assert tags_of(c, ids[1]) == ["series|%s|%s" % (g, t)] and tags_of(c, ids[6]) == ["series|%s|%s|%s" % (g, cy, b)]
    assert k not in [f["name"] for f in ok(c.get("/api/series"))["folders"]]
    # arrange the board from the strip: the tree wins; group frames are rebuilt around their series, the extra frame goes
    r = ok(c.post("/api/series-folders/%d/link" % root, json={"linked": True, "mode": "strip"}))
    assert r["removed_frames"] == [k]
    assert tree(c) == ({g: None, cy: g}, {t: g, a: cy, b: cy}) and lanes(c, root) == was[1]
    bd = assert_nested_tidy(c, root)
    assert sorted(f["label"] for f in bd["frames"]) == sorted([t, a, b, cy]) and ok(c.get("/api/series-folders/%d/link" % root))["same"] is True
    # its undo in the web app: unlink, then save the board as it was (frames come back with what they were)
    ok(c.post("/api/series-folders/%d/link" % root, json={"linked": False, "mode": "none"}))
    old_frames = [{"id": None, "label": f[0], "x": f[1], "y": f[2], "w": f[3], "h": f[4], "sub_id": f[5], "series_id": f[6]} for f in was[2]]
    ids_now = {(f["sub_id"], f["series_id"]): f["id"] for f in bd["frames"]}
    for f in old_frames:
        f["id"] = ids_now.get((f["sub_id"], f["series_id"]))
    ok(c.put("/api/series-folders/%d/board" % root, json={"frames": old_frames, "items": [{"id": i, "x": x, "y": y} for i, x, y in was[3]], "restore": True}))
    assert snapshot(c, root) == was
    # a subfolder without a group frame (deleted while unlinked) gets one again
    ok(c.put("/api/series-folders/%d/board" % root, json={"frames": [f for f in board(c, root)["frames"] if not f["sub_id"] and f["label"] != k]}))
    assert ok(c.get("/api/series-folders/%d/link" % root))["missing_frames"] == [cy]
    ok(c.post("/api/series-folders/%d/link" % root, json={"linked": True, "mode": "strip"}))
    assert_nested_tidy(c, root)
    assert tree(c) == ({g: None, cy: g}, {t: g, a: cy, b: cy})


def test_folder_filter_follows_the_tree(lib):
    c, ids = lib
    g, cy, do, a, b, t, r = uniq("Grecia"), uniq("Cicladi"), uniq("Dodecaneso"), uniq("Naxos"), uniq("Paros"), uniq("Atene"), uniq("Rodi")
    root, s = new_folder(c, g, {t: ids[0:2]})
    sub, s2 = sub_folder(c, root, cy, {a: ids[2:4]})
    deep, s3 = sub_folder(c, sub, do, {r: ids[4:6]})
    s2.update(new_folder_series(c, sub, {b: ids[6:8]}))
    ok(c.post("/api/series/top-order", json={"folder_id": root, "entries": [{"folder": sub}, {"series": s[t]}]}))
    ok(c.post("/api/series-folders/%d/strip/move" % root, json={"ids": [ids[3]], "series": s2[a], "before": ids[2]}))

    def q(fid):
        return [p["id"] for p in ok(c.post("/api/photos/query", json={"filters": {"series_folder": fid}, "sort": [{"f": "custom", "d": "asc"}], "limit": 100}))["items"]]
    assert q(root) == [ids[3], ids[2]] + ids[4:6] + ids[6:8] + ids[0:2]  # Naxos, Dodecaneso › Rodi, Paros, Atene
    assert q(sub) == [ids[3], ids[2]] + ids[4:6] + ids[6:8] and q(deep) == ids[4:6]


def test_send_to_nas_carries_the_nesting(lib):
    c, ids = lib
    from lpl import db, transfer
    g, cy, do, a, b, t = uniq("Viaggio"), uniq("Cicladi"), uniq("Dodecaneso"), uniq("Naxos"), uniq("Rodi"), uniq("Atene")
    root, s = new_folder(c, g, {t: ids[0:2]})
    sub, s2 = sub_folder(c, root, cy, {a: ids[2:5]})
    deep, s3 = sub_folder(c, sub, do, {b: ids[5:7]})
    want_board, want_lanes, want_tree = board(c, root), lanes(c, root), tree(c)
    rel = {r["id"]: r["relpath"] for r in db.conn().execute("SELECT id, relpath FROM photos").fetchall()}
    dest = {i: rel[i] for i in ids[0:7]}
    series = [x for x in transfer._series_payload(dest) if x["name"] in (a, b, t)]
    folders = transfer._folders_payload(dest, series)
    by = {x["name"]: x for x in series}
    # what a NAS with one level of folders reads: the top-level folder, and frames that are series only
    assert [by[x]["folder"] for x in (t, a, b)] == [g, g, g] and by[b]["folder_path"] == [g, cy, do] and by[t]["folder_path"] == [g]
    assert [f["name"] for f in folders] == [g] and {fr["series"] for fr in folders[0]["frames"]} == {t, a, b}
    assert {fr["sub"] for fr in folders[0]["groups"]} == {cy, do}

    def wipe():
        for f in ok(c.get("/api/series"))["folders"]:
            c.delete("/api/series-folders/%d" % f["id"])
        for x in ok(c.get("/api/series"))["series"]:
            ok(c.delete("/api/series/%d" % x["id"]))
    wipe()
    ok(c.post("/api/import/index", json={"dests": list(dest.values()), "series": series, "series_folders": folders}))
    assert tree(c) == want_tree
    root2 = next(f["id"] for f in ok(c.get("/api/series"))["folders"] if f["name"] == g)
    assert lanes(c, root2) == want_lanes
    bd = assert_nested_tidy(c, root2)
    assert {(f["label"], f["x"], f["y"], f["w"], f["h"]) for f in bd["frames"]} == {(f["label"], f["x"], f["y"], f["w"], f["h"]) for f in want_board["frames"]}
    assert tags_of(c, ids[5]) == ["series|%s|%s|%s|%s" % (g, cy, do, b)]
    # what an older server does with the same send (it knows `folder` and `frames` only): flattened into the
    # top-level folder, no errors
    wipe()
    old_series = [{k: v for k, v in se.items() if k not in ("folder_path", "folder_meta")} for se in series]
    old_folders = [{k: v for k, v in f.items() if k != "groups"} for f in folders]
    r = ok(c.post("/api/import/index", json={"dests": list(dest.values()), "series": old_series, "series_folders": old_folders}))
    assert {x["name"] for x in r["series"]} == {a, b, t}
    assert tree(c) == ({g: None}, {t: g, a: g, b: g})
    root3 = ok(c.get("/api/series"))["folders"][0]["id"]
    flat_tidy(board(c, root3))
    assert lanes(c, root3) == want_lanes
