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
    r = client.post("/api/miro/preview", json={"url": "https://miro.com/app/board/uXjVabcdef=/", "token": "tok"})
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
    assert client.post("/api/miro/import", json={"key": p["key"]}).status_code == 410
    client.delete("/api/miro/token")
    assert not client.get("/api/miro").json()["has_token"]
    with db.write_lock:
        pass
