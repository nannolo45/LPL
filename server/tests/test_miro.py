# SPDX-License-Identifier: GPL-3.0-or-later
"""Import from Miro, against a stand-in for Miro's API (no network)."""
from lpl import db, miro

BOARD = {
    "items": [
        {"id": "F1", "type": "frame", "data": {"title": "Walk"}, "style": {"fillColor": "#ffffff"},
         "position": {"x": 0, "y": 0, "origin": "center", "relativeTo": "canvas_center"}, "geometry": {"width": 4000, "height": 2000}},
        {"id": "F2", "type": "frame", "data": {"title": "Lake"}, "style": {"fillColor": "#8fd14f"}, "parent": {"id": "F1"},
         "position": {"x": 3000, "y": 1000, "origin": "center", "relativeTo": "parent_top_left"}, "geometry": {"width": 1600, "height": 1200}},
        {"id": "I1", "type": "image", "data": {"title": "IMG_0001.jpg", "imageUrl": "x"}, "parent": {"id": "F1"},
         "position": {"x": 600, "y": 600, "origin": "center", "relativeTo": "parent_top_left"}, "geometry": {"width": 1000, "height": 750}},
        {"id": "I2", "type": "image", "data": {"title": "img_0002_01.JPG", "imageUrl": "x"}, "parent": {"id": "F1"},
         "position": {"x": 1700, "y": 600, "origin": "center", "relativeTo": "parent_top_left"}, "geometry": {"width": 1000, "height": 750}},
        {"id": "I3", "type": "image", "data": {"title": "IMG_0003.jpg", "imageUrl": "x"}, "parent": {"id": "F2"},
         "position": {"x": 600, "y": 600, "origin": "center", "relativeTo": "parent_top_left"}, "geometry": {"width": 1000, "height": 750}},
        {"id": "I4", "type": "image", "data": {"title": "DSCF9999.jpg", "imageUrl": "x"},
         "position": {"x": 5000, "y": 0, "origin": "center", "relativeTo": "canvas_center"}, "geometry": {"width": 1000, "height": 750}},
        {"id": "S1", "type": "sticky_note", "data": {"content": "<p>opening</p><p>shot</p>"}, "style": {"fillColor": "light_blue"},
         "position": {"x": -2500, "y": 0, "origin": "center", "relativeTo": "canvas_center"}, "geometry": {"width": 400, "height": 400}},
        {"id": "E1", "type": "embed", "data": {}, "position": {"x": 0, "y": 3000}, "geometry": {"width": 10, "height": 10}},
    ],
    "connectors": [{"id": "C1", "startItem": {"id": "S1"}, "endItem": {"id": "I1"}, "captions": [{"content": "this one"}]}],
}


def fake(url, token, raw=False):
    assert token == "tok"
    if raw:
        return b"", "image/jpeg"
    if "/items" in url:
        return {"data": BOARD["items"], "cursor": ""}
    if "/connectors" in url:
        return {"data": BOARD["connectors"]}
    return {"name": "Miro walk"}


def test_board_id():
    assert miro.board_id("https://miro.com/app/board/uXjVO1a2b3c=/?share_link_id=12") == "uXjVO1a2b3c="
    assert miro.board_id("uXjVO1a2b3c=") == "uXjVO1a2b3c="


def test_stems_and_colours():
    assert miro.stems("DSCF4821_01.jpg") == ["dscf4821_01", "dscf4821"]
    assert miro.stems("DSCF4821 (2).JPG")[-1] == "dscf4821"
    assert miro.nearest("#ffffff", miro.FRAME_COLORS, True) == ""
    assert miro.nearest("#8fd14f", miro.FRAME_COLORS, True) == "#5cc08a"
    assert miro.note_color({"fillColor": "light_blue"}) == "#9cc8f0"


def test_preview_and_import(client, monkeypatch):
    monkeypatch.setattr(miro, "fetch", fake)
    r = client.post("/api/miro/preview", json={"url": "https://miro.com/app/board/uXjVabcdef=/", "token": "tok", "wait": True})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == "Miro walk" and p["pictures"] == 4 and p["matched"] == 3
    assert [u["title"] for u in p["unmatched"]] == ["DSCF9999.jpg"]
    assert p["frames"] == 2 and p["notes"] == 1 and p["arrows"] == 1 and p["skipped"] == {"embed": 1}
    assert client.get("/api/miro").json()["has_token"]

    r = client.post("/api/miro/import", json={"key": p["key"], "name": "From Miro"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    b = client.get("/api/series/%d/board" % sid).json()
    frames = {f["label"]: f for f in b["frames"]}
    assert set(frames) == {"Walk", "Lake"} and frames["Lake"]["color"] == "#5cc08a" and frames["Walk"]["color"] == ""
    outer, inner = frames["Walk"], frames["Lake"]
    assert outer["x"] <= inner["x"] and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + 1  # nested stays nested
    byname = {it["filename"]: it for it in b["items"]}
    assert set(byname) == {"IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg"}
    a, c3 = byname["IMG_0001.jpg"], byname["IMG_0003.jpg"]
    assert inner["x"] <= c3["x"] + 75 <= inner["x"] + inner["w"]  # the lake photo is inside the lake frame
    assert abs((byname["IMG_0002.jpg"]["x"] - a["x"]) - 1100 * 0.15) <= 2  # spacing kept at the scale of a 150 px tile
    notes = [s for s in b["shapes"] if s["kind"] == "note"]
    assert any(n["text"] == "opening\nshot" for n in notes) and any(n["text"] == "Not found: DSCF9999.jpg" for n in notes)
    arrow = next(s for s in b["shapes"] if s["kind"] == "arrow")
    assert arrow["b"]["photo"] == a["id"] and arrow["text"] == "this one"
    assert sorted(it["num"] for it in b["items"] if it["num"]) == [1, 2, 3]
    # a frame inside another is a chapter of its own (strip lane), right after the frame holding it
    lanes = client.get("/api/series/%d/strip" % sid).json()["lanes"]
    assert [(l["label"], [p["filename"] for p in l["photos"]]) for l in lanes] == [("Walk", ["IMG_0001.jpg", "IMG_0002.jpg"]), ("Lake", ["IMG_0003.jpg"])]
    # a frame that only holds frames is no lane
    from lpl import series
    outer = {"x": 0, "y": 0, "w": 1000, "h": 1000, "id": 1}
    a, b2 = {"x": 10, "y": 10, "w": 300, "h": 300, "id": 2}, {"x": 400, "y": 10, "w": 300, "h": 300, "id": 3}
    top = {"x": 1200, "y": 0, "w": 300, "h": 300, "id": 4}
    assert [f["id"] for f in series.frames_sorted([top, b2, outer, a])] == [1, 2, 3, 4]
    assert client.post("/api/miro/import", json={"key": p["key"]}).status_code == 410
    client.delete("/api/miro/token")
    assert not client.get("/api/miro").json()["has_token"]
    with db.write_lock:
        pass


def test_untitled_pictures_by_picture(client, monkeypatch):
    """Pictures dropped onto a Miro board have no title and no size in the API: found by picture, spaced by distance."""
    from lpl import media
    rows = db.conn().execute("SELECT id, relpath FROM photos ORDER BY id").fetchall()
    # other tests add copies of the same pictures: keep photos that look unlike every other one
    hs = {r["id"]: miro.phash(media.preview_paths(r["id"])[0].read_bytes()) for r in rows if media.preview_paths(r["id"])[0].exists()}
    alone = lambda i: all(miro.closest(hs[i], [j], miro.np_hashes([hs[j]]), __import__("numpy").array([hs[j][1]]))[0][0] > 90 for j in hs if j != i)
    rows = [r for r in rows if r["id"] in hs and alone(r["id"])][:3]
    assert len(rows) >= 2
    assert all(media.preview_paths(r["id"])[0].exists() for r in rows)
    pics = {"P%d" % r["id"]: media.preview_paths(r["id"])[0].read_bytes() for r in rows}
    items = [{"id": "G", "type": "frame", "data": {"title": "Grecia"}, "style": {}, "position": {"x": 0, "y": 0}, "geometry": {"width": 3000, "height": 1000}}]
    for k, mid in enumerate(pics):
        items.append({"id": mid, "type": "image", "data": {"imageUrl": "https://api.miro.com/x"}, "parent": {"id": "G"},
                      "position": {"x": 300 + k * 450, "y": 300, "origin": "center", "relativeTo": "parent_top_left"}})
    items.append({"id": "X", "type": "image", "data": {"imageUrl": "u"}, "position": {"x": 0, "y": 2000}})

    def fake2(url, token, raw=False):
        if "/items" in url:
            return {"data": items}
        if "/connectors" in url:
            return {"data": []}
        return {"name": "Grecia"}
    monkeypatch.setattr(miro, "fetch", fake2)
    monkeypatch.setattr(miro, "original_name", lambda it, tok: "")
    monkeypatch.setattr(miro, "image_bytes", lambda it, tok: pics.get(it["id"]) or b"not an image")
    r = client.post("/api/miro/preview", json={"url": "uXjVabcdef=", "token": "tok", "wait": True})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["state"] == "done" and p["matched"] == len(pics) and p["how"] == {"picture": len(pics)}, p
    assert [u["miro_id"] for u in p["unmatched"]] == ["X"] and p["unreadable"] == 1
    # reading the board again uses the pictures kept from the first time: same result, even if Miro fails now
    monkeypatch.setattr(miro, "image_bytes", lambda it, tok: None)
    p2 = client.post("/api/miro/preview", json={"url": "uXjVabcdef=", "token": "tok", "wait": True}).json()
    assert p2["matched"] == p["matched"] and p2["how"] == p["how"]
    sid = client.post("/api/miro/import", json={"key": p["key"], "name": "Grecia from Miro", "use": {"X": None}}).json()["id"]
    b = client.get("/api/series/%d/board" % sid).json()
    xs = sorted(it["x"] for it in b["items"])
    assert all(150 <= b2 - a2 <= 180 for a2, b2 in zip(xs, xs[1:]))  # 450 apart on Miro: one LPL cell apart
    assert {it["id"] for it in b["items"]} == {r["id"] for r in rows}
    assert client.get("/api/miro/pic/%s/P%d" % (p["key"], rows[0]["id"])).status_code == 410  # the job is gone after the import


def test_scope_by_folders(client):
    rows = db.conn().execute("SELECT id, folder FROM photos WHERE COALESCE(missing,0)=0").fetchall()
    walk = {r["id"] for r in rows if r["folder"].startswith("2024/2024-05-01")}
    assert set(miro.scope_ids({"folders": ["2024/2024-05-01 Walk"]})) == walk
    assert set(miro.scope_ids({"folders": ["2024"]})) >= walk  # subfolders count
    assert set(miro.scope_ids({"folder": "lake"})) == {r["id"] for r in rows if "lake" in r["folder"].lower()}


def test_find_missing_later(client, monkeypatch):
    """A 'Not found' note keeps Miro's picture: look for the photo later and put it where the note is."""
    from lpl import media
    row = db.conn().execute("SELECT id FROM photos WHERE filename='IMG_0002.jpg' ORDER BY id LIMIT 1").fetchone()
    thumb = media.preview_paths(row["id"])[0].read_bytes()
    items = [dict(BOARD["items"][0]), {"id": "Q", "type": "image", "data": {"imageUrl": "u"}, "parent": {"id": "F1"},
             "position": {"x": 900, "y": 700, "origin": "center", "relativeTo": "parent_top_left"}, "geometry": {"width": 1000, "height": 750}},
             dict(BOARD["items"][2]), dict(BOARD["items"][6])]
    conns = [{"id": "C", "startItem": {"id": "S1"}, "endItem": {"id": "Q"}}]

    def fake3(url, token, raw=False):
        if "/items" in url:
            return {"data": items}
        if "/connectors" in url:
            return {"data": conns}
        return {"name": "Later"}
    monkeypatch.setattr(miro, "fetch", fake3)
    monkeypatch.setattr(miro, "original_name", lambda it, tok: "")
    monkeypatch.setattr(miro, "image_bytes", lambda it, tok: thumb if it["id"] == "Q" else None)
    monkeypatch.setattr(miro, "LIKELY", -1)  # nothing is taken by picture: Q stays missing
    monkeypatch.setattr(miro, "SURE", -1)
    p = client.post("/api/miro/preview", json={"url": "uXjVabcdef=", "token": "tok", "wait": True}).json()
    sid = client.post("/api/miro/import", json={"key": p["key"], "name": "Later from Miro", "use": {"Q": None}}).json()["id"]
    b = client.get("/api/series/%d/board" % sid).json()
    note = next(x for x in b["shapes"] if x["kind"] == "note" and x.get("miro") == "Q")
    assert note["text"] == "Not found in the library"
    info = client.get("/api/miro/missing/%s" % note["key"]).json()
    assert info["has_picture"] and client.get("/api/miro/missing/%s/picture" % note["key"]).content == thumb
    assert client.post("/api/miro/missing/%s/search" % note["key"], json={}).status_code == 400
    found = client.post("/api/miro/missing/%s/search" % note["key"], json={"folders": ["2024"]}).json()["photos"]
    assert found[0]["id"] == row["id"] and found[0]["dist"] <= 10
    by_filters = client.post("/api/miro/missing/%s/search" % note["key"], json={"filters": {"folders": ["2024"], "subfolders": True}}).json()["photos"]
    assert by_filters[0]["id"] == row["id"]
    by_name = client.post("/api/miro/missing/%s/search" % note["key"], json={"names": "IMG_0002"}).json()["photos"]
    assert row["id"] in [x["id"] for x in by_name]
    r = client.post("/api/miro/missing/%s/place" % note["key"], json={"kind": "series", "id": sid, "photo": row["id"]})
    assert r.status_code == 200, r.text
    b = client.get("/api/series/%d/board" % sid).json()
    it = next(i for i in b["items"] if i["id"] == row["id"])
    assert abs(it["x"] + 75 - (note["x"] + 75)) <= 1 and it["num"]
    assert not any(x.get("key") == note["key"] for x in b["shapes"])
    arrow = next(x for x in b["shapes"] if x["kind"] == "arrow")
    assert arrow["b"]["photo"] == row["id"]
    assert client.get("/api/miro/missing/%s" % note["key"]).status_code == 404


def test_find_missing_on_a_folder_board(client):
    """The note can also be on a series folder's shared board: the photo joins the series whose frame it lands in."""
    import time as _t
    fid = client.post("/api/series-folders", json={"name": "Miro folder"}).json()["id"]
    sid = client.post("/api/series", json={"name": "Miro in folder", "folder_id": fid}).json()["id"]
    b = client.get("/api/series-folders/%d/board" % fid).json()
    fr = next(f for f in b["frames"] if f.get("series_id") == sid)
    note = {"key": "mfold1", "kind": "note", "x": fr["x"] + 20, "y": fr["y"] + 40, "w": 150, "h": 100, "text": "Not found", "color": "#d8d8dc", "miro": "Z"}
    assert client.put("/api/series-folders/%d/shapes" % fid, json={"shapes": [note]}).status_code == 200
    with db.write_lock:
        miro._missing_table(db.conn())
        db.conn().execute("INSERT INTO miro_missing(note_key, picture, alts, title, created) VALUES('mfold1', NULL, '[]', '', ?)", (_t.time(),))
        db.conn().commit()
    pid = db.conn().execute("SELECT id FROM photos ORDER BY id LIMIT 1").fetchone()["id"]
    r = client.post("/api/miro/missing/mfold1/place", json={"kind": "folder", "id": fid, "photo": pid})
    assert r.status_code == 200 and "warning" not in r.json(), r.text
    items = client.get("/api/series/%d/board" % sid).json()["items"]
    assert pid in [i["id"] for i in items]
    assert not client.get("/api/series-folders/%d/board" % fid).json()["shapes"]
