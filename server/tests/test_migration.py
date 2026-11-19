# SPDX-License-Identifier: GPL-3.0-or-later
"""Updating a library made by LPL on main (schema in fixtures/schema-main-0f1139c.sql) in place: nothing is lost,
the new tables and columns appear, and the server runs on it. 20 000 photos, the size of the owner's library."""
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from lpl import db

HERE = Path(__file__).resolve().parent
MAIN_SCHEMA = HERE / "fixtures" / "schema-main-0f1139c.sql"
N = 20000


def old_library(path: Path) -> None:
    c = sqlite3.connect(str(path))
    c.executescript(MAIN_SCHEMA.read_text())
    t = 1.7e9
    c.executemany(
        "INSERT INTO photos(id, relpath, folder, filename, ext, size, mtime, xmp_mtime, taken, camera, lens, rating, edited,"
        " preview_ver, preview_state, meta_mtime, first_seen, hidden) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(i, "2024/d%03d/IMG_%05d.RAF" % (i % 300, i), "2024/d%03d" % (i % 300), "IMG_%05d.RAF" % i, "raf", 30_000_000 + i,
          t + i, t + i + 1, "2024-05-01 10:00:00", "X-T5", "XF23", i % 6 - 1, i % 2, i % 3, 1, t, t, int(i % 97 == 0))
         for i in range(1, N + 1)])
    c.executemany("INSERT INTO photo_labels VALUES(?,?)", [(i, i % 5) for i in range(1, N + 1, 3)])
    c.executemany("INSERT INTO tags(id, path) VALUES(?,?)", [(1, "places|Lisbon"), (2, "series|Walk")])
    c.executemany("INSERT INTO photo_tags VALUES(?,?)", [(i, 1 + i % 2) for i in range(1, N + 1, 2)])
    c.execute("INSERT INTO series(id, name, created) VALUES(1, 'Walk', ?)", (t,))
    c.executemany("INSERT INTO series_items VALUES(1,?,?,?,?)", [(i, i * 10.0, 5.0, i) for i in range(1, 200)])
    c.execute("INSERT INTO series_frames(series_id,label,x,y,w,h,maybe,color,border) VALUES(1,'01',0,0,500,300,0,'#334','dashed')")
    c.execute("INSERT INTO series_shapes VALUES(1, '[{\"key\":\"n1\",\"kind\":\"note\"}]')")
    c.execute("INSERT INTO agents VALUES('pc-1','Desktop','windows',?,?)", (t, json.dumps({"working_set": [{"id": 5, "status": "dirty"}]})))
    c.execute("INSERT INTO jobs(agent_id,kind,payload,created,taken,status) VALUES('pc-1','edit','{\"ids\":[5]}',?,?,'taken')", (t, t))
    c.execute("INSERT INTO jobs(agent_id,kind,payload,created,status) VALUES(NULL,'sync','{\"all\":true}',?,'queued')", (t,))
    c.execute("INSERT INTO sent VALUES(7,'2024/x/IMG_7.RAF',1,'ab',1.0,2.0)")
    c.execute("INSERT INTO folder_order VALUES('2024',0)")
    c.execute("INSERT INTO settings VALUES('auth_salt','0123')")
    c.commit()
    c.close()


def dump(path: Path, tables_from: Path = None) -> dict:
    """Every row of every table that main has, by its columns on main."""
    c = sqlite3.connect(str(path))
    ref = sqlite3.connect(":memory:")
    ref.executescript(MAIN_SCHEMA.read_text())
    out = {}
    for (t,) in ref.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in ref.execute("PRAGMA table_info(%s)" % t)]
        out[t] = c.execute("SELECT %s FROM %s ORDER BY rowid" % (",".join(cols), t)).fetchall()
    c.close()
    return out


def test_migrate_from_main(tmp_path):
    p = tmp_path / "lpl.sqlite"
    old_library(p)
    before = dump(p)
    assert len(before["photos"]) == N
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    t0 = time.perf_counter()
    db.migrate(c)
    took = time.perf_counter() - t0
    db.migrate(c)  # every start runs it again: nothing happens
    c.close()
    assert took < 5, "migration took %.2f s" % took
    print("migration of %d photos: %.3f s" % (N, took))
    assert dump(p) == before  # nothing lost or changed
    c = sqlite3.connect(str(p))
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
    assert {"edit_events", "edit_baseline", "idx_edit_events_photo"} <= names
    assert [r[0] for r in c.execute("SELECT attempts FROM jobs ORDER BY id")] == [0, 0]  # taken before: never handed out again
    assert c.execute("SELECT COUNT(*) FROM edit_events").fetchone()[0] == 0
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()


def test_server_runs_on_a_migrated_library(tmp_path):
    """The real start-up (create_app -> db.init) on main's library, in a separate process with its own data folder."""
    data = tmp_path / "data"
    data.mkdir()
    old_library(data / "lpl.sqlite")
    (tmp_path / "photos").mkdir()
    code = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, %r)
        from fastapi.testclient import TestClient
        from lpl import main
        c = TestClient(main.create_app())
        d = c.get("/api/photos/5").json()
        e = c.get("/api/photos/5/edits").json()
        b = c.post("/api/agents/heartbeat", json={"id": "pc-1", "name": "Desktop", "os": "windows", "state": {}}).json()
        q = c.post("/api/photos/query", json={"filters": {}, "limit": 1}).json()
        print(json.dumps({"rating": d["rating"], "last_edit": d["last_edit"], "edits": e["edits"], "jobs": b["jobs"], "total": q["total"]}))
    """ % str(HERE.parent))
    env = dict(os.environ, DATA_DIR=str(data), PHOTO_ROOT=str(tmp_path / "photos"), WEB_DIR=str(tmp_path / "no-web"),
               LPL_NO_SCAN="1", LPL_PASSWORD="")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["rating"] == 5 % 6 - 1 and out["last_edit"] is None and out["edits"] == []
    assert [j["kind"] for j in out["jobs"]] == ["sync"]  # the queued job; the old taken one stays taken
    assert out["total"] == N - len(range(97, N + 1, 97))  # hidden photos stay hidden
