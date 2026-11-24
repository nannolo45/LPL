# SPDX-License-Identifier: GPL-3.0-or-later
"""The PC agent with shared styles, presets and module layouts (R11): it announces "shared", reads darktable's data.db only
from a read-only snapshot, shares processing-module presets, layouts and styles (not built-in ones, not other lib presets),
uploads changes and deletions, marks conflicts, and applies a shared-update job only while darktable is closed, by column
name in one transaction. data.db is made from darktable's own CREATE statements (fixtures/darktable-data-schema.sql);
darktable is not started."""
import json
import sqlite3
from pathlib import Path

import pytest

from lpl import db, shared
from test_pc_agent import agent_mod, base_url  # noqa: F401  (fixtures)

FIX = Path(__file__).resolve().parent / "fixtures"
SCHEMA = (FIX / "darktable-data-schema.sql").read_text(encoding="utf-8")
VECTORS = json.loads((FIX / "r11-hash-vectors.json").read_text(encoding="utf-8"))["vectors"]
PRESET_COLS = ["name", "description", "operation", "op_version", "op_params", "enabled", "blendop_params", "blendop_version",
               "multi_priority", "multi_name", "multi_name_hand_edited", "model", "maker", "lens", "iso_min", "iso_max",
               "exposure_min", "exposure_max", "aperture_min", "aperture_max", "focal_length_min", "focal_length_max",
               "writeprotect", "autoapply", "filter", "def", "format"]


def make_db(path: Path) -> Path:
    c = sqlite3.connect(str(path))
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    return path


def add_preset(path, name, op="exposure", ver=6, params=b"\x00\x00\x80\x3f", writeprotect=0, **kw):
    row = {"name": name, "description": "", "operation": op, "op_version": ver, "op_params": params, "enabled": 1,
           "blendop_params": b"gd", "blendop_version": 14, "multi_priority": 0, "multi_name": "", "multi_name_hand_edited": 0,
           "model": "%", "maker": "%", "lens": "%", "iso_min": 0.0, "iso_max": 340282346638528859811704183484516925440.0,
           "exposure_min": 0.0, "exposure_max": 100000000.0, "aperture_min": 0.0, "aperture_max": 100000000.0,
           "focal_length_min": 0.0, "focal_length_max": 1000.0, "writeprotect": writeprotect, "autoapply": 0, "filter": 0,
           "def": 0, "format": 0}
    row.update(kw)
    c = sqlite3.connect(str(path))
    c.execute("INSERT OR REPLACE INTO presets(%s) VALUES(%s)" % (",".join(row), ",".join("?" * len(row))), list(row.values()))
    c.commit()
    c.close()


def add_style(path, name, items, description="", iop_list=None):
    c = sqlite3.connect(str(path))
    sid = c.execute("INSERT INTO styles(name,description,iop_list) VALUES(?,?,?)", (name, description, iop_list)).lastrowid
    for it in items:
        c.execute("INSERT INTO style_items(styleid,num,module,operation,op_params,enabled,blendop_params,blendop_version,"
                  "multi_priority,multi_name,multi_name_hand_edited) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (sid,) + tuple(it))
    c.commit()
    c.close()


def rows(path, sql, *a):
    c = sqlite3.connect(str(path))
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


@pytest.fixture()
def fresh(clean):
    with db.write_lock:
        for t in ("shared_items", "shared_candidates", "shared_state", "shared_agents"):
            db.conn().execute("DELETE FROM %s" % t)
        db.conn().execute("DELETE FROM settings WHERE key='pref.settings'")
        db.conn().commit()
    return clean


class Dt:
    """darktable_running() as the tests say."""
    running = False


def make_agent(agent_mod, base_url, tmp_path, monkeypatch, aid="pc-shared-agent", name="Desktop"):
    monkeypatch.setattr(agent_mod, "darktable_running", lambda: Dt.running)
    monkeypatch.setattr(agent_mod, "darktable_running_strict", lambda: Dt.running)
    d = tmp_path / aid
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(agent_mod, "STATE_FILE", d / "state.json")
    path = d / "data.db"
    if not path.exists():
        make_db(path)
    a = agent_mod.Agent({"id": aid, "name": name, "password": "", "auto_sync_seconds": 0, "server": base_url,
                         "darktable_datadb": str(path)})
    beat(a)
    return a, path


def beat(a):
    a.take(a.srv.json("POST", "/api/agents/heartbeat", a.heartbeat_body()))
    while not a.jobs.empty():
        a.handle(a.jobs.get())


def server_items(c):
    return {i["name"]: i for i in c.get("/api/shared?since=0").json()["items"]}


def devices(c, aid):
    return next(d for d in c.get("/api/shared/devices").json()["devices"] if d["id"] == aid)


def test_caps_and_server_without_shared(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    assert "shared" in agent_mod.CAPS
    a, _ = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    assert "shared" in a.heartbeat_body()["caps"] and a.shared.active()
    a.take({"jobs": []})  # an answer from a server before R11: no server_caps
    assert not a.shared.active() and "shared_update" not in a.state()
    a.shared.tick(force=True)
    assert server_items(fresh) == {}


def test_snapshot_picks_shared_items_and_matches_the_hash_vectors(agent_mod, tmp_path):
    path = make_db(tmp_path / "data.db")
    v = VECTORS[0]["body"]["row"]
    add_preset(path, v["name"], op=v["operation"], ver=v["op_version"], params=bytes.fromhex(v["op_params"]),
               blendop_params=bytes.fromhex(v["blendop_params"]))
    add_preset(path, "built-in", writeprotect=1)
    add_preset(path, "export preset", op="export", ver=1)       # a lib module, not a processing module
    lay = VECTORS[1]["body"]["row"]
    add_preset(path, lay["name"], op="modulegroups", ver=1, params=bytes.fromhex(lay["op_params"]))
    s = VECTORS[2]["body"]
    add_style(path, s["style"]["name"], [(it["num"], it["module"], it["operation"], bytes.fromhex(it["op_params"]), it["enabled"],
                                          bytes.fromhex(it["blendop_params"]) if it["blendop_params"] else None, it["blendop_version"],
                                          it["multi_priority"], it["multi_name"], it["multi_name_hand_edited"]) for it in s["items"]],
              description=s["style"]["description"])
    add_style(path, "_l10n_darktable|_l10n_camera styles|x", [])
    mtime = path.stat().st_mtime_ns
    snap = agent_mod.snapshot_shared(path, agent_mod.module_operations())
    assert path.stat().st_mtime_ns == mtime
    kinds = sorted((json.loads(k)[0], json.loads(k)[3]) for k in snap)
    assert kinds == [("layout", lay["name"]), ("preset", v["name"]), ("style", s["style"]["name"])]
    by_kind = {b["kind"]: b for b in snap.values()}
    assert "writeprotect" not in by_kind["preset"]["row"] and by_kind["preset"]["row"]["iso_max"] == "3.4028234663852886e+38"
    for vec in VECTORS:  # agent and reference agree, through a real data.db
        assert agent_mod.shared_hash(by_kind[vec["body"]["kind"]]) == vec["hash"] == shared.item_hash(by_kind[vec["body"]["kind"]])
    assert agent_mod.snapshot_shared(tmp_path / "missing.db", set()) is None


def test_uploads_changes_and_deletions(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    Dt.running = False
    a, path = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    add_preset(path, "bright")
    add_preset(path, "dark", params=b"\x00\x00\x80\xbf")
    a.shared.tick(force=True)
    it = server_items(fresh)
    assert sorted(it) == ["bright", "dark"] and it["bright"]["device_name"] == "Desktop"
    seq = fresh.get("/api/shared").json()["seq"]
    a.shared.sync_pass()
    assert fresh.get("/api/shared").json()["seq"] == seq  # nothing changed: nothing uploaded
    add_preset(path, "bright", params=b"\x00\x00\x00\x40")
    c = sqlite3.connect(str(path))
    c.execute("DELETE FROM presets WHERE name='dark'")
    c.commit()
    c.close()
    a.shared.sync_pass()
    it = server_items(fresh)
    assert it["bright"]["rev"] == 2 and it["dark"]["deleted"] is True
    st = {s["id"]: s for s in a.shared.data["items"].values()}
    assert st[it["dark"]["id"]]["local_hash"] is None and st[it["bright"]["id"]]["rev"] == 2
    assert devices(fresh, "pc-shared-agent")["waiting"] == []


def test_snapshot_every_ten_minutes_while_running_and_when_darktable_closes(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    Dt.running = True
    a, path = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    a.shared.tick(force=True)
    add_preset(path, "while running")
    a.shared.tick()
    assert server_items(fresh) == {}  # less than ten minutes since the last snapshot
    Dt.running = False
    a.shared.tick()  # darktable has closed
    assert "while running" in server_items(fresh)
    add_preset(path, "later")
    a.shared.last_pass -= agent_mod.SHARED_EVERY + 1
    a.shared.tick()
    assert "later" in server_items(fresh)


def test_update_waits_for_darktable_then_applies(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    Dt.running = False
    a, pa = make_agent(agent_mod, base_url, tmp_path, monkeypatch, "pc-shared-a", "Desktop")
    b, pb = make_agent(agent_mod, base_url, tmp_path, monkeypatch, "pc-shared-b", "Laptop")
    add_preset(pa, "bright")
    lay = VECTORS[1]["body"]["row"]
    add_preset(pa, lay["name"], op="modulegroups", ver=1, params=bytes.fromhex(lay["op_params"]))
    s = VECTORS[2]["body"]
    add_style(pa, s["style"]["name"], [(1, 1, "sigmoid", b"\x00\x00\xa0\x40", 1, None, 14, 0, "", 0),
                                       (0, 3, "channelmixerrgb", b"\xde\xad", 1, b"gd", 14, 1, "mono", 1)])
    add_style(pb, s["style"]["name"], [(0, 1, "exposure", b"\x01", 1, None, 14, 0, "", 0)])  # older copy here, never shared
    a.shared.tick(force=True)
    it = server_items(fresh)
    b.shared.tick(force=True)  # B's own style copy differs from the shared one with base 0: conflict
    assert b.shared.data["items"][agent_mod.shared_key("style", "", 0, s["style"]["name"])]["state"] == "conflict"
    sid = it[s["style"]["name"]]["id"]
    assert fresh.post("/api/shared/%d/resolve" % sid, json={"agent": "pc-shared-b", "choice": "theirs"}).status_code == 200
    b.shared.sync_pass()
    assert b.shared.data["items"][agent_mod.shared_key("style", "", 0, s["style"]["name"])]["state"] == "ok"
    w = {x["name"]: x["what"] for x in devices(fresh, "pc-shared-b")["waiting"]}
    assert w == {"bright": "new", lay["name"]: "new", s["style"]["name"]: "changed"}
    ids = [i["id"] for i in server_items(fresh).values()]
    r = fresh.post("/api/shared/update", json={"agent": "pc-shared-b", "ids": ids, "delete": [], "keep": []})
    assert r.status_code == 200
    Dt.running = True
    beat(b)  # takes the job and holds it
    b.shared.tick()
    beat(b)
    assert devices(fresh, "pc-shared-b")["update"] == "waiting_for_darktable"
    assert rows(pb, "SELECT COUNT(*) FROM presets")[0][0] == 0  # nothing written while darktable runs
    Dt.running = False
    b.shared.tick()
    beat(b)
    d = devices(fresh, "pc-shared-b")
    assert d["update"] is None and d["waiting"] == [] and d["job"] is None
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (r.json()["id"],)).fetchone()[0] == "done"
    p = rows(pb, "SELECT name, op_params, writeprotect, iso_max FROM presets ORDER BY name")
    assert p == [("bright", b"\x00\x00\x80\x3f", 0, 340282346638528859811704183484516925440.0),
                 (lay["name"], bytes.fromhex(lay["op_params"]), 0, 340282346638528859811704183484516925440.0)]
    st = rows(pb, "SELECT s.name, i.num, i.operation, i.op_params, i.blendop_params FROM styles s JOIN style_items i ON i.styleid=s.id ORDER BY i.num")
    assert st == [(s["style"]["name"], 0, "channelmixerrgb", b"\xde\xad", b"gd"), (s["style"]["name"], 1, "sigmoid", b"\x00\x00\xa0\x40", None)]
    assert rows(pb, "SELECT COUNT(*) FROM styles")[0][0] == 1
    b.shared.sync_pass()  # what was taken in is not a local change
    assert all(i["rev"] == 1 for i in server_items(fresh).values())


def test_deletion_delete_here_and_keep(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    Dt.running = False
    a, pa = make_agent(agent_mod, base_url, tmp_path, monkeypatch, "pc-shared-a", "Desktop")
    b, pb = make_agent(agent_mod, base_url, tmp_path, monkeypatch, "pc-shared-b", "Laptop")
    for p in (pa, pb):
        add_preset(p, "one")
        add_preset(p, "two", params=b"\x02")
    a.shared.tick(force=True)
    b.shared.tick(force=True)
    assert db.conn().execute("SELECT COUNT(*) FROM shared_candidates").fetchone()[0] == 0  # identical: no conflict
    c = sqlite3.connect(str(pa))
    c.execute("DELETE FROM presets")
    c.commit()
    c.close()
    a.shared.sync_pass()
    it = server_items(fresh)
    assert {x["name"]: x["what"] for x in devices(fresh, "pc-shared-b")["waiting"]} == {"one": "deleted", "two": "deleted"}
    fresh.post("/api/shared/update", json={"agent": "pc-shared-b", "ids": [], "delete": [it["one"]["id"]], "keep": [it["two"]["id"]]})
    beat(b)
    b.shared.tick()
    assert [r[0] for r in rows(pb, "SELECT name FROM presets")] == ["two"]
    assert devices(fresh, "pc-shared-b")["waiting"] == []
    dots = {i["name"]: i["devices"]["pc-shared-b"] for i in fresh.get("/api/shared/devices").json()["items"]}
    assert dots == {"one": "absent", "two": "kept"}
    add_preset(pb, "two", params=b"\x03")  # the kept one changed again: back for everyone
    b.shared.sync_pass()
    two = server_items(fresh)["two"]
    assert two["deleted"] is False and two["rev"] == 3
    assert {x["name"]: x["what"] for x in devices(fresh, "pc-shared-a")["waiting"]} == {"two": "changed"}


def test_never_writes_data_db_unless_surely_closed(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    """A failed process check counts as running; darktable's data.db.lock with a live pid blocks; a stale one does not."""
    import os
    import subprocess
    Dt.running = False
    strict = agent_mod.darktable_running_strict  # the real one (make_agent replaces it)
    a, pa = make_agent(agent_mod, base_url, tmp_path, monkeypatch, "pc-shared-a", "Desktop")
    b, pb = make_agent(agent_mod, base_url, tmp_path, monkeypatch, "pc-shared-b", "Laptop")
    monkeypatch.setattr(agent_mod, "darktable_running_strict", strict)
    add_preset(pa, "bright")
    a.shared.tick(force=True)
    fresh.post("/api/shared/update", json={"agent": "pc-shared-b", "ids": [server_items(fresh)["bright"]["id"]]})

    def broken(*args, **kw):
        raise OSError("no process list")
    assert agent_mod.darktable_running() is False  # the plain check (patched by the tests) ...
    monkeypatch.setattr(agent_mod.subprocess, "run", broken)
    assert agent_mod.darktable_running_strict() is True  # ... but a failed strict check counts as running
    beat(b)
    b.shared.tick()
    assert b.shared.update == "waiting_for_darktable" and rows(pb, "SELECT COUNT(*) FROM presets")[0][0] == 0
    monkeypatch.setattr(agent_mod.subprocess, "run", subprocess.run)
    monkeypatch.setattr(agent_mod, "darktable_running_strict", lambda: False)
    lock = Path(str(pb) + ".lock")
    lock.write_text(str(os.getpid()))  # a live pid: darktable holds data.db
    b.shared.tick()
    assert b.shared.update == "waiting_for_darktable" and rows(pb, "SELECT COUNT(*) FROM presets")[0][0] == 0
    dead = subprocess.Popen(["true"]); dead.wait()
    lock.write_text(str(dead.pid))  # stale lock of a darktable that is gone: not running
    assert agent_mod.data_db_locked(pb) is False
    b.shared.tick()
    assert b.shared.update is None and rows(pb, "SELECT name FROM presets") == [("bright",)]
