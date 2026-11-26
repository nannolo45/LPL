# SPDX-License-Identifier: GPL-3.0-or-later
"""Previews that show darktable's edit (26 Nov 2026): the NAS has no darktable, so a PC renders them with darktable-cli.
The server counts edited photos whose preview is from before the edit and hands a "render" job to a PC that says it
can render; the agent renders (here with a stand-in darktable-cli that writes a JPEG) and posts the preview."""
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from lpl import db

from test_pc_agent import agent_mod, base_url  # noqa: F401  (fixtures)

FAKE_CLI = '''#!%s
import json, sys
from PIL import Image
a = sys.argv[1:]
out = a[2]
log = %r
with open(log, "a") as f:
    f.write(json.dumps(a) + "\\n")
Image.new("RGB", (64, 40), (200, 30, 30)).save(out, "JPEG")
'''


@pytest.fixture()
def fake_dt(tmp_path, agent_mod, monkeypatch):  # noqa: F811
    b = tmp_path / "bin"
    b.mkdir()
    (b / "darktable").write_text("#!/bin/sh\n")
    log = tmp_path / "cli.log"
    cli = b / "darktable-cli"
    cli.write_text(FAKE_CLI % (sys.executable, str(log)))
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    conf = tmp_path / "dtconf"
    conf.mkdir()
    (conf / "darktablerc").write_text("plugins/darkroom/lut3d/def_path=/luts\n")
    (conf / "data.db").write_bytes(b"styles")
    monkeypatch.setattr(agent_mod, "data_dir", lambda: tmp_path / "agentdata")
    return {"darktable": str(b / "darktable"), "darktable_configdir": str(conf), "log": log}


def stale(client):
    return client.get("/api/previews/stale").json()


def make_stale(pid):
    with db.write_lock:
        db.conn().execute("UPDATE photos SET edited=1, preview_src='embedded', preview_ts=NULL WHERE id=?", (pid,))
        db.conn().commit()


def test_stale_count_render_job_and_preview_clears_it(clean):
    with db.write_lock:
        db.conn().execute("UPDATE photos SET edited=0")
        db.conn().execute("DELETE FROM agents")
        db.conn().commit()
    make_stale(1)
    make_stale(2)
    assert stale(clean)["count"] == 2 and stale(clean)["agents"] == []
    assert clean.post("/api/previews/render", json={"stale": True}).status_code == 409   # no PC that can render
    clean.post("/api/agents/heartbeat", json={"id": "pc-r", "name": "Laptop", "os": "linux", "caps": ["batch", "render"]})
    clean.post("/api/agents/heartbeat", json={"id": "pc-n", "name": "Old", "os": "windows", "caps": ["batch"]})
    assert [a["name"] for a in stale(clean)["agents"]] == ["Laptop"]
    r = clean.post("/api/previews/render", json={"stale": True}).json()
    assert r["count"] == 2 and r["agent_id"] == "pc-r" and r["agent_name"] == "Laptop"
    job = db.conn().execute("SELECT * FROM jobs WHERE id=?", (r["id"],)).fetchone()
    assert job["kind"] == "render" and json.loads(job["payload"])["ids"] == [1, 2]
    jobs = clean.post("/api/agents/heartbeat", json={"id": "pc-r", "name": "Laptop", "os": "linux", "caps": ["render"]}).json()["jobs"]
    assert [j["kind"] for j in jobs] == ["render"]
    assert clean.post("/api/previews/render", json={"ids": [1], "agent_id": "pc-n"}).status_code == 409
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 200, 10)).save(buf, "JPEG")
    assert clean.post("/api/photos/1/preview", content=buf.getvalue()).status_code == 200
    assert stale(clean)["count"] == 1
    # an edit long after the rendering makes it stale again
    with db.write_lock:
        db.conn().execute("UPDATE photos SET xmp_mtime=? WHERE id=1", (time.time() + 3600,))
        db.conn().commit()
    assert stale(clean)["count"] == 2


def test_agent_renders_photos_on_the_nas_and_posts_them(clean, base_url, agent_mod, fake_dt, tmp_path):  # noqa: F811
    make_stale(3)
    a = agent_mod.Agent({"id": "pc-render", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    assert "render" in a.heartbeat_body()["caps"]
    v = db.conn().execute("SELECT preview_ver FROM photos WHERE id=3").fetchone()[0]
    assert a.render_job(["3"], 99) == "done"
    row = db.conn().execute("SELECT preview_src, preview_ver FROM photos WHERE id=3").fetchone()
    assert row["preview_src"] == "rendered" and row["preview_ver"] == v + 1
    args = json.loads(fake_dt["log"].read_text().splitlines()[-1])
    assert args[0].endswith(".jpg") and args[1].endswith(".jpg.xmp")      # the original and its XMP, downloaded
    assert args[args.index("--configdir") + 1] != fake_dt["darktable_configdir"]   # a copy, not darktable's own
    assert args[args.index("--library") + 1] == ":memory:" and args[args.index("--width") + 1] == "1920"
    assert not (tmp_path / "agentdata" / "render" / "job-99").exists()        # temporary files gone
    assert not list((tmp_path / "agentdata" / "render-conf").iterdir())


def test_agent_renders_after_a_sync_that_came_without_a_preview(clean, base_url, agent_mod, fake_dt, tmp_path):  # noqa: F811
    a = agent_mod.Agent({"id": "pc-render2", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    raw = tmp_path / "IMG_0002.jpg"
    raw.write_bytes(b"x")
    (tmp_path / "IMG_0002.jpg.xmp").write_text("<x/>")
    a.items["2"] = {"id": "2", "local_path": str(raw), "filename": raw.name}
    make_stale(2)
    a.renderer.request("2")
    for _ in range(200):
        if a.items["2"].get("preview_mtime"):
            break
        time.sleep(0.05)
    assert db.conn().execute("SELECT preview_src FROM photos WHERE id=2").fetchone()[0] == "rendered"
    assert a.items["2"]["preview_mtime"] == (tmp_path / "IMG_0002.jpg.xmp").stat().st_mtime
    n = len(fake_dt["log"].read_text().splitlines())
    a.renderer.request("2")            # same edit: not rendered again
    time.sleep(0.5)
    assert len(fake_dt["log"].read_text().splitlines()) == n


def test_no_darktable_cli_no_render_cap(agent_mod, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent_mod.shutil, "which", lambda n: None)
    assert agent_mod.darktable_cli_cmd({"darktable": str(tmp_path / "nothing" / "darktable")}) is None
    assert agent_mod.darktable_cli_cmd({"darktable": "flatpak"})[:3] == ["flatpak", "run", "--command=darktable-cli"]


def test_an_appimage_gets_a_darktable_cli_link(agent_mod, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent_mod, "data_dir", lambda: tmp_path / "agentdata")
    img = tmp_path / "Apps" / "Darktable-5.2.1-x86_64.AppImage"
    img.parent.mkdir()
    img.write_bytes(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\0" * 32)
    cmd = agent_mod.darktable_cli_cmd({"darktable": str(img)})
    link = tmp_path / "agentdata" / "bin" / "darktable-cli"
    assert cmd == [str(link)] and link.is_symlink() and link.resolve() == img.resolve()
    assert agent_mod.darktable_cli_cmd({"darktable": str(img)}) == [str(link)]   # made once, then reused
    renamed = tmp_path / "Apps" / "darktable"                                    # no .AppImage name: the magic bytes
    renamed.write_bytes(img.read_bytes())
    assert agent_mod.darktable_cli_cmd({"darktable": str(renamed)}) == [str(link)] and link.resolve() == renamed.resolve()


def test_the_409_says_why_each_pc_cannot_render(clean):
    with db.write_lock:
        db.conn().execute("DELETE FROM agents")
        db.conn().commit()
    make_stale(1)
    assert "No PC is online" in clean.post("/api/previews/render", json={"stale": True}).json()["detail"]
    clean.post("/api/agents/heartbeat", json={"id": "pc-a", "name": "Laptop", "os": "linux", "caps": ["batch"],
                                               "state": {"render_unavailable": "darktable-cli not found next to /opt/dt"}})
    clean.post("/api/agents/heartbeat", json={"id": "pc-b", "name": "Desktop", "os": "windows", "caps": ["batch"]})
    d = clean.post("/api/previews/render", json={"stale": True}).json()["detail"]
    assert "Laptop: darktable-cli not found next to /opt/dt" in d and "Desktop: its agent is from before" in d


def test_scopes_count_and_stop(clean):
    c = clean
    with db.write_lock:
        db.conn().execute("UPDATE photos SET edited=0")
        db.conn().execute("DELETE FROM agents")
        db.conn().execute("DELETE FROM jobs")
        db.conn().commit()
    ids = [r[0] for r in db.conn().execute("SELECT id FROM photos ORDER BY id LIMIT 3")]
    for i in ids:
        make_stale(i)
    with db.write_lock:  # the third one already shows its edit
        db.conn().execute("UPDATE photos SET preview_src='rendered', preview_ts=?, xmp_mtime=? WHERE id=?", (time.time(), time.time(), ids[2]))
        db.conn().commit()
    fid = c.post("/api/series-folders", json={"name": "Trip"}).json()["id"]
    sid = c.post("/api/series", json={"name": "Render scope", "ids": ids, "folder_id": fid}).json()["id"]
    count = lambda b: c.post("/api/previews/count", json=b).json()["count"]
    assert count({"series": sid}) == 2 and count({"series": sid, "all": True}) == 3
    assert count({"series_folder": fid}) == 2
    folder = db.conn().execute("SELECT folder FROM photos WHERE id=?", (ids[0],)).fetchone()[0]
    top = folder.split("/")[0]
    assert count({"folder": top}) >= 1 and count({"folder": "no/such"}) == 0
    assert count({"ids": ids + [999999]}) == 3          # selected: every edited one, out of date or not
    # stop: one not handed out never starts; a running one is told with the heartbeat until it is done
    c.post("/api/agents/heartbeat", json={"id": "pc-r", "name": "Laptop", "os": "linux", "caps": ["render"]})
    j1 = c.post("/api/previews/render", json={"series": sid}).json()["id"]
    assert c.post("/api/previews/render/%d/stop" % j1).status_code == 200
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (j1,)).fetchone()[0] == "stopped"
    j2 = c.post("/api/previews/render", json={"series": sid}).json()["id"]
    hb = c.post("/api/agents/heartbeat", json={"id": "pc-r", "name": "Laptop", "os": "linux", "caps": ["render"]}).json()
    assert [j["id"] for j in hb["jobs"]] == [j2] and "stop_render" not in hb
    c.post("/api/previews/render/%d/stop" % j2)
    hb = c.post("/api/agents/heartbeat", json={"id": "pc-r", "name": "Laptop", "os": "linux", "caps": ["render"]}).json()
    assert hb["stop_render"] == [j2]
    c.post("/api/jobs/%d/done" % j2, json={"status": "stopped"})
    assert "stop_render" not in c.post("/api/agents/heartbeat", json={"id": "pc-r", "name": "Laptop", "os": "linux", "caps": ["render"]}).json()
    assert c.post("/api/previews/render/999999/stop").status_code == 404


def test_agent_reports_progress_and_stops(clean, base_url, agent_mod, fake_dt, tmp_path):  # noqa: F811
    a = agent_mod.Agent({"id": "pc-render3", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    assert a.render_job(["1", "2"], 7) == "done"
    p = a.state()["render"]
    assert (p["job"], p["total"], p["done"], p["failed"], p["stopped"]) == (7, 2, 2, 0, False) and p["ended"]
    a.take({"jobs": [], "stop_render": [8]})
    assert a.render_job(["1", "2"], 8) == "stopped"
    p = a.state()["render"]
    assert p["stopped"] and p["done"] == 0
