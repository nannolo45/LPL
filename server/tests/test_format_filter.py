# SPDX-License-Identifier: GPL-3.0-or-later
"""The Format filter: formats as upper-case extensions (JPG = jpg + jpeg, TIF = tif + tiff), OR within, AND with the rest."""
import sqlite3

import pytest

from lpl import db, query
from test_migration import old_library


@pytest.fixture()
def mixed(client):
    rows = [("fmt/a1.raf", "raf", 5), ("fmt/a2.raf", "raf", 1), ("fmt/a3.arw", "arw", 5), ("fmt/b1.jpeg", "jpeg", 5),
            ("fmt/b2.tiff", "tiff", 1), ("fmt/b3.tif", "tif", 1)]
    with db.write_lock:
        c = db.conn()
        for rel, ext, rating in rows:
            c.execute("INSERT INTO photos(relpath, folder, filename, ext, size, mtime, rating, first_seen, import_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                      (rel, "fmt", rel.split("/")[1], ext, 1, 1.0, rating, 1.0, 1.0))
        c.commit()
    yield client
    with db.write_lock:
        db.conn().execute("DELETE FROM photos WHERE folder='fmt'")
        db.conn().commit()


def names(c, **f):
    r = c.post("/api/photos/query", json={"filters": dict(folder="fmt", **f), "limit": 100}).json()
    assert r["total"] == len(r["items"])
    return sorted(p["filename"] for p in r["items"])


def test_one_format_or_several(mixed):
    assert names(mixed, formats=["RAF"]) == ["a1.raf", "a2.raf"]
    assert names(mixed, formats=["RAF", "ARW"]) == ["a1.raf", "a2.raf", "a3.arw"]
    assert len(names(mixed)) == 6 and names(mixed, formats=[]) == names(mixed)


def test_combined_with_another_filter(mixed):
    assert names(mixed, formats=["RAF", "ARW"], ratings=[5]) == ["a1.raf", "a3.arw"]


def test_jpg_jpeg_and_tif_tiff_are_one_format(mixed):
    assert names(mixed, formats=["JPG"]) == ["b1.jpeg"]
    assert names(mixed, formats=["jpeg"]) == ["b1.jpeg"]
    assert names(mixed, formats=["TIF"]) == ["b2.tiff", "b3.tif"]


def test_ids_and_position(mixed):
    f = {"folder": "fmt", "formats": ["ARW"]}
    ids = mixed.post("/api/photos/ids", json={"filters": f}).json()["ids"]
    assert len(ids) == 1
    assert mixed.post("/api/photos/position", json={"filters": f, "id": ids[0]}).json()["index"] == 0
    other = mixed.post("/api/photos/ids", json={"filters": {"folder": "fmt", "formats": ["RAF"]}}).json()["ids"][0]
    assert mixed.post("/api/photos/position", json={"filters": f, "id": other}).json()["index"] == -1


def test_facets_count_merge_and_order(mixed):
    fm = mixed.get("/api/facets").json()["formats"]
    d = {x["v"]: x["n"] for x in fm}
    assert d["RAF"] == 2 and d["ARW"] == 1 and d["JPG"] >= 1 and d["TIF"] == 2
    assert "JPEG" not in d and "TIFF" not in d
    ns = [x["n"] for x in fm]
    assert ns == sorted(ns, reverse=True)


def test_series_match_accepts_a_format_filter_alone(mixed):
    assert query.format_exts(["JPG", "TIFF"]) == ["jpg", "jpeg", "tif", "tiff"]
    sid = mixed.post("/api/series", json={"name": "fmt-s", "ids": []}).json()["id"]
    r = mixed.post("/api/series/%d/match" % sid, json={"filters": {"formats": ["RAF"]}})
    assert r.status_code != 400, r.text


def test_old_database_needs_no_backfill(tmp_path):
    p = tmp_path / "old.sqlite"
    old_library(p)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    db.migrate(c)
    assert "idx_photos_ext" in {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    where, args, join = query.build_where({"formats": ["RAF"]})
    n = c.execute("SELECT COUNT(*) FROM photos p%s WHERE %s" % (join, where), args).fetchone()[0]
    assert n == c.execute("SELECT COUNT(*) FROM photos WHERE ext='raf' AND COALESCE(hidden,0)=0").fetchone()[0] > 0
    c.close()
