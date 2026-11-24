# SPDX-License-Identifier: GPL-3.0-or-later
"""The board's Add photos panel: photos by file name (typed or a pasted list; an export's name finds the RAW)."""
from lpl import query


def test_name_tokens():
    assert query.name_tokens("DSCF3215.jpg, DSCF3216.JPG\nIMG_0001  a/b/DSCF1.raf") == ["DSCF3215", "DSCF3216", "IMG_0001", "DSCF1"]
    assert query.name_tokens(["x.jpeg", "x"]) == ["x"]


def test_match_by_names(client):
    sid = client.post("/api/series", json={"name": "By name"}).json()["id"]
    r = client.post("/api/series/%d/match" % sid, json={"filters": {"names": "img_0001.JPG\nIMG_0003.tif"}}).json()
    assert r["new"] >= 2 and r["names_missing"] == []
    assert client.post("/api/series/%d/match" % sid, json={"filters": {"names": "IMG_0001 DSCF9999"}}).json()["names_missing"] == ["DSCF9999"]
    rows = client.post("/api/series/%d/match" % sid, json={"filters": {"names": "IMG_0002", "folders": ["2024/2024-06-10 Lake"]}}).json()
    assert rows["new"] == 0  # together with the other criteria
    r = client.post("/api/series/%d/add-matching" % sid, json={"filters": {"names": ["IMG_0001.jpg"]}, "target": {"new": True}})
    assert r.status_code == 200 and r.json()["added"] >= 1
    b = client.get("/api/series/%d/board" % sid).json()
    assert all(it["filename"].lower().startswith("img_0001") for it in b["items"])
