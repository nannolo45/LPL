# SPDX-License-Identifier: GPL-3.0-or-later
"""The PC agent with darktable's LUT folder (R19, docs/luts-r19.md): it announces "luts", reads the folder from
darktablerc (plugins/darkroom/lut3d/def_path), says so when there is none, sends its files (a first upload does not
replace what LPL has; a change made on the PC does), takes in files only into empty places, never overwrites or deletes
a file in the folder, and does not take back a file the user removed there. darktable is not started."""
import hashlib
import os

import pytest

from lpl import db, luts
from test_pc_agent import agent_mod, base_url  # noqa: F401  (fixtures)

CUBE_A = b"TITLE \"a\"\nLUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"
CUBE_B = CUBE_A.replace(b"\"a\"", b"\"b\"")
CUBE_C = CUBE_A.replace(b"\"a\"", b"\"c\"")


def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def fresh(clean):
    with db.write_lock:
        for t in ("lut_files", "lut_history", "lut_devices"):
            db.conn().execute("DELETE FROM %s" % t)
        db.conn().commit()
    import shutil
    shutil.rmtree(luts.store_dir(), ignore_errors=True)
    return clean


def make_agent(agent_mod, base_url, tmp_path, monkeypatch, aid="pc-luts-agent", name="Desktop", lut_dir=True):
    d = tmp_path / aid
    conf = d / "dtconf"
    conf.mkdir(parents=True, exist_ok=True)
    folder = d / "luts"
    folder.mkdir(exist_ok=True)
    rc = "ui_last/theme=darktable-elegant-grey\n"
    if lut_dir:
        rc += "plugins/darkroom/lut3d/def_path=%s\n" % folder
    (conf / "darktablerc").write_text(rc, encoding="utf-8")
    monkeypatch.setattr(agent_mod, "STATE_FILE", d / "state.json")
    a = agent_mod.Agent({"id": aid, "name": name, "password": "", "auto_sync_seconds": 0, "server": base_url,
                         "darktable_configdir": str(conf)})
    beat(a)
    return a, folder


def beat(a):
    a.take(a.srv.json("POST", "/api/agents/heartbeat", a.heartbeat_body()))


def put(c, agent_id, path, data, base=None):
    h = {"x-sha256": sha(data)}
    if base is not None:
        h["x-base-sha256"] = base
    return c.put("/api/luts/file", params={"path": path, "agent": agent_id}, content=data, headers=h)


def listing(c):
    return {f["path"]: f for f in c.get("/api/luts?since=0").json()["files"]}


def report(c, aid):
    return next(f for f in c.get("/api/luts/overview").json()["devices"] if f["agent_id"] == aid)


def states(c, aid):
    out = {}
    for f in c.get("/api/luts/overview").json()["files"]:
        for d in f["devices"]:
            if d["agent_id"] == aid:
                out[f["path"]] = d["state"]
    return out


def test_caps_and_status_without_a_lut_folder(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    assert "luts" in agent_mod.CAPS
    a, _ = make_agent(agent_mod, base_url, tmp_path, monkeypatch, lut_dir=False)
    assert "luts" in a.heartbeat_body()["caps"]
    assert a.luts.active()
    a.luts.tick()
    a.luts.running.join(10)  # the pass runs in its own thread
    assert a.luts.status["folder"] is None and "no LUT folder" in a.luts.status["reason"]
    r = report(fresh, a.cfg["id"])
    assert r["folder"] is None and "no LUT folder" in r["reason"]
    beat(a)
    st = db.conn().execute("SELECT state FROM agents WHERE id=?", (a.cfg["id"],)).fetchone()["state"]
    assert "no LUT folder" in st


def test_a_server_without_luts_is_left_alone(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    a.server_caps = ["shared"]  # as an older server answers
    (folder / "x.cube").write_bytes(CUBE_A)
    a.luts.tick()
    assert a.luts.running is None  # no pass started at all
    assert listing(fresh) == {}


def test_sends_its_files_and_reports_them(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    (folder / "film").mkdir()
    (folder / "film" / "a.cube").write_bytes(CUBE_A)
    (folder / "pack.gmz").write_bytes(b"gmz bytes")      # travels between PCs (darktable with G'MIC)
    (folder / "notes.txt").write_bytes(b"not a LUT")     # left alone
    a.luts.sync_pass()
    files = listing(fresh)
    assert set(files) == {"film/a.cube", "pack.gmz"}
    assert files["film/a.cube"]["sha256"] == sha(CUBE_A) and files["film/a.cube"]["device_name"] == "Desktop"
    assert states(fresh, a.cfg["id"]) == {"film/a.cube": "ok", "pack.gmz": "ok"}
    assert report(fresh, a.cfg["id"])["folder"] == str(folder)
    # nothing changed: nothing sent again
    seq = fresh.get("/api/luts?since=0").json()["seq"]
    a.luts.sync_pass()
    assert fresh.get("/api/luts?since=0").json()["seq"] == seq


def test_takes_in_new_files_into_empty_places(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    assert put(fresh, "android-phone", "phone/new.cube", CUBE_B, base="").status_code == 200
    a.luts.sync_pass()
    assert (folder / "phone" / "new.cube").read_bytes() == CUBE_B
    assert not [p for p in folder.rglob(".lpl-lut-*")]
    assert states(fresh, a.cfg["id"])["phone/new.cube"] == "ok"


def test_never_replaces_a_file_in_the_folder(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    (folder / "w.cube").write_bytes(CUBE_A)
    a.luts.sync_pass()
    # another PC changes it (newest wins in LPL); this PC's file is not replaced, it is reported
    assert put(fresh, "pc-other", "w.cube", CUBE_C).status_code == 200
    a.luts.sync_pass()
    assert (folder / "w.cube").read_bytes() == CUBE_A
    assert states(fresh, a.cfg["id"])["w.cube"] == "different"
    assert listing(fresh)["w.cube"]["sha256"] == sha(CUBE_C)


def test_a_file_found_on_a_pc_that_joins_later_does_not_replace_lpls(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    assert put(fresh, "pc-first", "w.cube", CUBE_A).status_code == 200
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    (folder / "w.cube").write_bytes(CUBE_B)
    a.luts.sync_pass()
    assert listing(fresh)["w.cube"]["sha256"] == sha(CUBE_A)
    assert (folder / "w.cube").read_bytes() == CUBE_B
    assert states(fresh, a.cfg["id"])["w.cube"] == "different"
    seq = fresh.get("/api/luts?since=0").json()["seq"]
    a.luts.sync_pass()  # the same 409 is not asked again
    assert fresh.get("/api/luts?since=0").json()["seq"] == seq
    assert a.luts.data["refused"]["w.cube"] == [sha(CUBE_B), sha(CUBE_A)]


def test_a_change_on_the_pc_wins(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    (folder / "w.cube").write_bytes(CUBE_A)
    a.luts.sync_pass()
    (folder / "w.cube").write_bytes(CUBE_B)
    os.utime(folder / "w.cube", ns=(1, 10 ** 18))  # a different time, so the cached hash is not used
    a.luts.sync_pass()
    f = listing(fresh)["w.cube"]
    assert f["sha256"] == sha(CUBE_B) and f["device_name"] == "Desktop"
    assert fresh.get("/api/luts/overview").json()["files"][0]["history"] == 1


def test_a_file_the_user_removed_is_not_taken_back(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    (folder / "w.cube").write_bytes(CUBE_A)
    a.luts.sync_pass()
    (folder / "w.cube").unlink()
    a.luts.sync_pass()
    assert not (folder / "w.cube").exists()
    assert states(fresh, a.cfg["id"])["w.cube"] == "removed"
    assert "w.cube" in listing(fresh)  # LPL keeps it
    a.luts.sync_pass()
    assert not (folder / "w.cube").exists()
    # put back by the user: sent again as a change
    (folder / "w.cube").write_bytes(CUBE_C)
    a.luts.sync_pass()
    assert listing(fresh)["w.cube"]["sha256"] == sha(CUBE_C)
    assert "w.cube" not in a.luts.data["removed"]


def test_the_lut_folder_is_read_from_darktablerc(agent_mod, tmp_path):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "darktablerc").write_text("plugins/darkroom/lut3d/def_path=relative/luts\n", encoding="utf-8")
    folder, reason = agent_mod.lut_folder({"darktable_configdir": str(conf)})
    assert folder is None and "not an absolute path" in reason
    (conf / "darktablerc").write_text("plugins/darkroom/lut3d/def_path=%s\n" % (tmp_path / "missing"), encoding="utf-8")
    assert "does not exist" in agent_mod.lut_folder({"darktable_configdir": str(conf)})[1]
    assert "not found" in agent_mod.lut_folder({"darktable_configdir": str(tmp_path / "none")})[1]
    for bad in ("../x.cube", "a//b.cube", "x.txt", "/abs.cube"):
        assert not agent_mod.lut_path_ok(bad)
    assert agent_mod.lut_path_ok("Film/K 2383.CUBE")


def test_hidden_folders_and_files_are_not_sent(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    (folder / ".cache" / "icons").mkdir(parents=True)
    (folder / ".cache" / "icons" / "app.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    (folder / ".hidden.cube").write_bytes(CUBE_B)
    (folder / "Film").mkdir()
    (folder / "Film" / "k.cube").write_bytes(CUBE_A)
    a.luts.sync_pass()
    assert set(listing(fresh)) == {"Film/k.cube"}


def test_a_home_folder_or_a_huge_folder_is_not_a_lut_folder(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    monkeypatch.setattr(agent_mod.Path, "home", staticmethod(lambda: folder))
    f, reason = agent_mod.lut_folder(a.cfg)
    assert f is None and "whole home folder" in reason
    monkeypatch.setattr(agent_mod.Path, "home", staticmethod(lambda: folder / "Me"))   # the folder above home
    assert agent_mod.lut_folder(a.cfg)[0] is None
    monkeypatch.undo()
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch, aid="pc-luts-many")
    monkeypatch.setattr(agent_mod, "LUT_MAX_FILES", 3)
    for i in range(5):
        (folder / ("p%d.png" % i)).write_bytes(b"\x89PNG\r\n\x1a\nx")
    a.luts.sync_pass()
    assert listing(fresh) == {}
    assert a.luts.status["folder"] is None and "only LUT files" in a.luts.status["reason"]
    assert "only LUT files" in report(fresh, a.cfg["id"])["reason"]


def test_a_long_pass_does_not_hold_up_the_main_loop(agent_mod, base_url, fresh, tmp_path, monkeypatch):
    import threading
    a, folder = make_agent(agent_mod, base_url, tmp_path, monkeypatch)
    gate = threading.Event()
    monkeypatch.setattr(a.luts, "sync_pass", lambda: gate.wait(10))
    a.luts.tick()
    assert a.luts.running.is_alive()       # tick came back while the pass is still running
    a.luts.tick(force=True)                # a second pass is not started on top of it
    gate.set()
    a.luts.running.join(10)
    assert not a.luts.running.is_alive()
