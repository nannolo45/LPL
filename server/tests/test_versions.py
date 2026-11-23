# SPDX-License-Identifier: GPL-3.0-or-later
"""Duplicates and versions (R17): the scan of NAME_nn.EXT.xmp files, the checkout with and without the "versions"
capability, xmp / preview / delete with version=, the 409 for a number two devices made, the cover, series pins and
the edit events. Synthetic library only. See docs/versions-r17.md."""
import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from lpl import db, scanner, versions, xmp

DATA = Path(__file__).resolve().parent / "fixtures" / "xmp"
PHONE = {"id": "android-vers-test", "name": "Phone", "os": "android"}
PC = {"id": "pc-vers-test", "name": "Desktop", "os": "windows"}


def sidecar(rating=0, labels=(), tags=(), name=""):
    data = xmp.apply_meta(None, rating=rating, labels=list(labels), tags=list(tags), category_tags=[])
    return xmp.set_version_name(data, name) if name else data


@pytest.fixture()
def lib(client, tmp_path_factory):
    """A folder with synthetic photos: A has versions 1, 2 and 100 (gap, three digits), B has only _01 (no v0 sidecar),
    C has none, D_01.jpg is a photo of its own beside D.jpg."""
    from conftest import PHOTOS
    d = PHOTOS / "versions-lib"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    for n in ("A", "B", "C", "D", "D_01"):
        Image.new("RGB", (40, 30), (10, 20, 30)).save(d / (n + ".jpg"), quality=90)
    (d / "A.jpg.xmp").write_bytes(sidecar(rating=3, tags=["place|lake"]))
    (d / "A_01.jpg.xmp").write_bytes(sidecar(rating=5, labels=[1], name="black and white"))
    (d / "A_02.jpg.xmp").write_bytes(sidecar(rating=1, tags=["for print"]))
    (d / "A_100.jpg.xmp").write_bytes(sidecar(rating=2))
    (d / "B_01.jpg.xmp").write_bytes(sidecar(rating=4))
    (d / "D_01.jpg.xmp").write_bytes(sidecar(rating=2))   # the sidecar of the photo D_01.jpg, not a version of D
    (d / "Z_01.jpg.xmp").write_bytes(sidecar())            # no photo Z: ignored
    scanner.scan()
    ids = {r["filename"]: r["id"] for r in db.conn().execute("SELECT id, filename FROM photos WHERE folder='versions-lib'").fetchall()}
    yield d, ids
    with db.write_lock:
        db.conn().execute("DELETE FROM agents")
        db.conn().commit()


def beat(client, agent, caps=None):
    body = dict(agent, state={"working_set": []})
    if caps is not None:
        body["caps"] = caps
    assert client.post("/api/agents/heartbeat", json=body).status_code == 200


def ns(client, pid):
    return [v["n"] for v in client.get("/api/photos/%d/versions" % pid).json()["versions"]]


# ---------------------------------------------------------------- scan
def test_scan_lists_versions_under_their_photo(client, lib):
    d, ids = lib
    assert sorted(ids) == ["A.jpg", "B.jpg", "C.jpg", "D.jpg", "D_01.jpg"]  # a version sidecar is never a photo
    assert ns(client, ids["A.jpg"]) == [0, 1, 2, 100]  # gaps and three digits
    assert ns(client, ids["B.jpg"]) == [0, 1]           # v0 has no sidecar: still the photo, shown normally
    assert ns(client, ids["C.jpg"]) == [0]
    assert ns(client, ids["D.jpg"]) == [0] and ns(client, ids["D_01.jpg"]) == [0]
    vs = {v["n"]: v for v in client.get("/api/photos/%d/versions" % ids["A.jpg"]).json()["versions"]}
    assert vs[1]["name"] == "black and white" and vs[1]["rating"] == 5 and vs[1]["labels"] == [1]
    assert vs[2]["tags"] == ["for print"] and vs[0]["rating"] == 3 and vs[0]["cover"] and not vs[1]["cover"]
    assert vs[100]["rating"] == 2


def test_numbers_are_never_reused(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    assert client.get("/api/photos/%d/versions" % pid).json()["next"] == 101
    assert client.delete("/api/photos/%d/xmp?version=100" % pid).status_code == 200
    assert client.get("/api/photos/%d/versions" % pid).json()["next"] == 101  # 100 is gone but not free
    scanner.scan()
    assert ns(client, pid) == [0, 1, 2]


def test_rescan_follows_files_outside_lpl(client, lib):
    d, ids = lib
    pid = ids["C.jpg"]
    (d / "C_03.jpg.xmp").write_bytes(sidecar(rating=4, name="new"))
    scanner.scan()
    assert ns(client, pid) == [0, 3]
    (d / "C_03.jpg.xmp").unlink()
    scanner.scan()
    assert ns(client, pid) == [0]
    assert not versions.has_rows(pid)  # a plain photo again


def test_photos_carry_versions_and_cover(client, lib):
    d, ids = lib
    items = {p["id"]: p for p in client.post("/api/photos/get", json={"ids": list(ids.values())}).json()["items"]}
    assert items[ids["A.jpg"]]["versions"] == 4 and items[ids["A.jpg"]]["cover"] == 0 and items[ids["A.jpg"]]["rating"] == 3
    assert items[ids["C.jpg"]]["versions"] == 1 and items[ids["C.jpg"]]["cover"] == 0


# ---------------------------------------------------------------- checkout
def test_checkout_lists_versions_only_with_the_cap(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    beat(client, PC)                                   # no caps: as before
    beat(client, PHONE, ["versions"])
    old = client.get("/api/photos/%d/checkout?agent=%s" % (pid, PC["id"])).json()
    assert "versions" not in old and old["relpath"].endswith("A.jpg")
    assert "versions" not in client.get("/api/photos/%d/checkout" % pid).json()
    new = client.get("/api/photos/%d/checkout?agent=%s" % (pid, PHONE["id"])).json()
    assert [v["n"] for v in new["versions"]] == [0, 1, 2, 100] and new["next"] == 101 and new["cover"] == 0
    assert {"n", "name", "mtime", "size"} <= set(new["versions"][1]) and new["versions"][1]["name"] == "black and white"


# ---------------------------------------------------------------- xmp / preview / delete
def test_get_xmp_with_version(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    assert client.get("/api/photos/%d/xmp" % pid).content == (d / "A.jpg.xmp").read_bytes()  # absent = 0
    assert client.get("/api/photos/%d/xmp?version=0" % pid).content == (d / "A.jpg.xmp").read_bytes()
    assert client.get("/api/photos/%d/xmp?version=2" % pid).content == (d / "A_02.jpg.xmp").read_bytes()
    assert client.get("/api/photos/%d/xmp?version=7" % pid).status_code == 404


def test_post_xmp_to_a_version_and_the_409(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    beat(client, PHONE, ["versions"])
    client.get("/api/photos/%d/checkout?agent=%s" % (pid, PHONE["id"]))
    body = sidecar(rating=4, name="phone edit")
    r = client.post("/api/photos/%d/xmp?version=2&agent=%s&checkout_ts=%f" % (pid, PHONE["id"], 1e12), content=body)
    assert r.status_code == 200  # 2 was in the checkout: an edit of it
    assert xmp.parse((d / "A_02.jpg.xmp").read_bytes())["rating"] == 4
    assert xmp.parse((d / "A.jpg.xmp").read_bytes())["rating"] == 3  # version 0 untouched
    # a new number the phone made offline: free, written beside the photo
    r = client.post("/api/photos/%d/xmp?version=101&agent=%s" % (pid, PHONE["id"]), content=sidecar(rating=2))
    assert r.status_code == 200 and (d / "A_101.jpg.xmp").is_file()
    assert ns(client, pid) == [0, 1, 2, 100, 101]
    # another device made 102 meanwhile; this phone's checkout did not have it
    (d / "A_102.jpg.xmp").write_bytes(sidecar(rating=1))
    scanner.scan()
    r = client.post("/api/photos/%d/xmp?version=102&agent=%s" % (pid, PHONE["id"]), content=sidecar(rating=5))
    assert r.status_code == 409 and r.json() == {"error": "version_exists", "next": 103}
    assert xmp.parse((d / "A_102.jpg.xmp").read_bytes())["rating"] == 1  # nothing overwritten
    r = client.post("/api/photos/%d/xmp?version=103&agent=%s" % (pid, PHONE["id"]), content=sidecar(rating=5))
    assert r.status_code == 200
    # an agent that never checked out: the number exists, so 409 as well
    r = client.post("/api/photos/%d/xmp?version=1" % pid, content=sidecar())
    assert r.status_code == 409


def test_old_post_without_version_is_unchanged(client, lib):
    d, ids = lib
    pid = ids["C.jpg"]
    r = client.post("/api/photos/%d/xmp?agent=%s" % (pid, PC["id"]), content=sidecar(rating=2))
    assert r.status_code == 200 and (d / "C.jpg.xmp").is_file() and not list(d.glob("C_*"))
    assert ns(client, pid) == [0]


def test_lpl_values_win_per_version(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    beat(client, PHONE, ["versions"])
    client.get("/api/photos/%d/checkout?agent=%s" % (pid, PHONE["id"]))
    import time
    ts = time.time() - 5
    assert client.post("/api/meta", json={"ids": [pid], "version": 2, "rating": 4}).status_code == 200
    r = client.post("/api/photos/%d/xmp?version=2&agent=%s&checkout_ts=%f" % (pid, PHONE["id"], ts), content=sidecar(rating=1))
    assert r.headers["X-LPL-Merged"] == "1" and xmp.parse(r.content)["rating"] == 4
    # version 1 was not changed in LPL: its own post is not merged
    r = client.post("/api/photos/%d/xmp?version=1&agent=%s&checkout_ts=%f" % (pid, PHONE["id"], ts), content=sidecar(rating=1))
    assert r.headers["X-LPL-Merged"] == "0"


def test_preview_per_version(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), (200, 0, 0)).save(buf, "JPEG")
    assert client.post("/api/photos/%d/preview?version=2" % pid, content=buf.getvalue()).status_code == 200
    r = client.get("/api/preview/%d?version=2&s=t" % pid)
    assert r.status_code == 200
    red = Image.open(io.BytesIO(r.content)).convert("RGB").getpixel((5, 5))
    assert red[0] > 150 and red[1] < 60
    v = {x["n"]: x for x in client.get("/api/photos/%d/versions" % pid).json()["versions"]}
    assert v[2]["preview_ver"] == 1 and v[1]["preview_ver"] == 0


def test_delete_a_version(client, lib):
    from lpl.config import config
    d, ids = lib
    pid = ids["A.jpg"]
    assert client.delete("/api/photos/%d/xmp" % pid).status_code == 400            # absent = 0: not deletable
    assert client.delete("/api/photos/%d/xmp?version=0" % pid).status_code == 400
    assert (d / "A.jpg.xmp").is_file()
    assert client.delete("/api/photos/%d/xmp?version=9" % pid).status_code == 404
    r = client.delete("/api/photos/%d/xmp?version=1" % pid)
    assert r.status_code == 200 and not (d / "A_01.jpg.xmp").exists()
    moved = Path(r.json()["moved_to"])
    assert moved.is_file() and config.backup_dir in moved.parents  # moved, not erased
    assert ns(client, pid) == [0, 2, 100]
    scanner.scan()
    assert ns(client, pid) == [0, 2, 100]


# ---------------------------------------------------------------- cover
def test_cover_follows_the_chosen_version(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    r = client.post("/api/photos/%d/cover?version=1" % pid)
    assert r.status_code == 200 and r.json()["cover"] == 1
    p = client.post("/api/photos/get", json={"ids": [pid]}).json()["items"][0]
    assert p["cover"] == 1 and p["rating"] == 5 and p["labels"] == [1]        # the tile shows the cover's values
    # an edit without version applies to the cover; version 0 stays as it is
    client.post("/api/meta", json={"ids": [pid], "rating": 2})
    assert xmp.parse((d / "A_01.jpg.xmp").read_bytes())["rating"] == 2
    assert xmp.parse((d / "A.jpg.xmp").read_bytes())["rating"] == 3
    # and an edit of version 0 does not change the tile
    client.post("/api/meta", json={"ids": [pid], "version": 0, "rating": 4})
    assert xmp.parse((d / "A.jpg.xmp").read_bytes())["rating"] == 4
    assert client.post("/api/photos/get", json={"ids": [pid]}).json()["items"][0]["rating"] == 2
    # the preview of the tile is the cover's
    v1 = io.BytesIO()
    Image.new("RGB", (20, 10), (0, 200, 0)).save(v1, "JPEG")
    client.post("/api/photos/%d/preview?version=1" % pid, content=v1.getvalue())
    px = Image.open(io.BytesIO(client.get("/api/preview/%d" % pid).content)).convert("RGB").getpixel((5, 5))
    assert px[1] > 150 and px[0] < 60
    assert client.post("/api/photos/%d/cover?version=55" % pid).status_code == 404
    # deleting the cover version gives the cover back to the original
    client.delete("/api/photos/%d/xmp?version=1" % pid)
    p = client.post("/api/photos/get", json={"ids": [pid]}).json()["items"][0]
    assert p["cover"] == 0 and p["rating"] == 4
    assert client.post("/api/photos/%d/cover?version=0" % pid).json()["cover"] == 0


def test_cover_of_a_photo_without_versions(client, lib):
    d, ids = lib
    pid = ids["C.jpg"]
    assert client.post("/api/photos/%d/cover?version=0" % pid).json()["cover"] == 0
    assert client.post("/api/photos/%d/cover?version=1" % pid).status_code == 404


def test_set_version_name(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    r = client.put("/api/photos/%d/version-name?version=2" % pid, json={"name": "for print"})
    assert r.status_code == 200
    assert xmp.parse((d / "A_02.jpg.xmp").read_bytes())["version_name"] == "for print"
    scanner.scan()
    v = {x["n"]: x for x in client.get("/api/photos/%d/versions" % pid).json()["versions"]}
    assert v[2]["name"] == "for print"


# ---------------------------------------------------------------- edit events, series pin
def test_edit_events_carry_the_version(client, lib):
    d, ids = lib
    pid = ids["D.jpg"]
    beat(client, PHONE, ["versions"])
    client.get("/api/photos/%d/checkout?agent=%s" % (pid, PHONE["id"]))
    for n in (2, 0):
        data = (DATA / "nop.xmp").read_bytes()
        assert client.post("/api/photos/%d/xmp?version=%d&agent=%s" % (pid, n, PHONE["id"]), content=data).status_code == 200
    evs = client.get("/api/photos/%d/edits" % pid).json()["edits"]
    assert sorted(e["version"] for e in evs) == [0, 2]  # saves of different versions are separate entries


def test_series_pin_and_board(client, lib):
    d, ids = lib
    pid = ids["A.jpg"]
    sid = client.post("/api/series", json={"name": "versions-pin"}).json()["id"]
    client.post("/api/series/%d/items" % sid, json={"ids": [pid]})
    assert client.put("/api/series/%d/pin" % sid, json={"photo_id": pid, "version": 2}).status_code == 200
    items = client.get("/api/series/%d/board" % sid).json()["items"]
    assert items[0]["pin"] == 2 and items[0]["show"] == 2
    assert client.put("/api/series/%d/pin" % sid, json={"photo_id": pid, "version": 55}).status_code == 404
    client.post("/api/photos/%d/cover?version=1" % pid)
    assert client.put("/api/series/%d/pin" % sid, json={"photo_id": pid, "version": None}).status_code == 200
    items = client.get("/api/series/%d/board" % sid).json()["items"]
    assert items[0]["pin"] is None and items[0]["show"] == 1  # follows the cover
    client.put("/api/series/%d/pin" % sid, json={"photo_id": pid, "version": 2})
    client.delete("/api/photos/%d/xmp?version=2" % pid)   # the pinned version is gone: back to the cover
    items = client.get("/api/series/%d/board" % sid).json()["items"]
    assert items[0]["pin"] is None and items[0]["show"] == 1
    client.delete("/api/series/%d" % sid)


def test_migration_of_an_older_library(tmp_path):
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "old.sqlite"))
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)  # a library from before R17 has none of the new tables or columns
    c.execute("DROP TABLE series_items"); c.execute("CREATE TABLE series_items (series_id INTEGER, photo_id INTEGER, x REAL, y REAL, ord INTEGER)")
    db.migrate(c)
    db.migrate(c)
    assert {r[1] for r in c.execute("PRAGMA table_info(photo_versions)")} >= {"photo_id", "n", "name", "xmp_path", "mtime", "cover"}
    assert "version" in {r[1] for r in c.execute("PRAGMA table_info(series_items)")}
