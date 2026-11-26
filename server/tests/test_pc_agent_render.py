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
import json, os, sys
from PIL import Image
a = sys.argv[1:]
log = %r
with open(log, "a") as f:
    f.write(json.dumps(a) + "\\n")
def lacks(f, xmp):  # like darktable without the module an edit uses (it still exports, without it)
    try:
        if "spektrafilm" in open(xmp, encoding="utf-8", errors="ignore").read():
            sys.stderr.write("1,43 [dev_read_history] the module `spektrafilm' requested by image id=1 `%%s' is not installed on this computer!\\n" %% os.path.basename(f))
    except OSError:
        pass
if "--import" in a:  # a batch: --import <file> ... <outdir>/$(FILE_NAME) --out-ext jpg
    files = [a[i + 1] for i, x in enumerate(a) if x == "--import"]
    pattern = a[a.index("--out-ext") - 1]
    for f in files:
        if "unreadable" in f:
            continue
        lacks(f, f + ".xmp")
        stem = os.path.splitext(os.path.basename(f))[0]
        Image.new("RGB", (64, 40), (200, 30, 30)).save(pattern.replace("$(FILE_NAME)", stem) + ".jpg", "JPEG")
else:
    lacks(a[0], a[1])
    Image.new("RGB", (64, 40), (200, 30, 30)).save(a[2], "JPEG")
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
    monkeypatch.setattr(agent_mod, "BRIDGE", tmp_path / "bridge")
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


def test_sleep_and_restart_of_the_pc(clean):
    """A long job is not handed out beside itself; after the agent restarted it is handed out again at once, without
    the photos already rendered; one being stopped stays stopped."""
    c = clean
    with db.write_lock:
        db.conn().execute("DELETE FROM agents")
        db.conn().execute("DELETE FROM jobs")
        db.conn().commit()
    for i in (1, 2, 3):
        make_stale(i)
    hb = lambda st=None: c.post("/api/agents/heartbeat", json={"id": "pc-s", "name": "Laptop", "os": "linux", "caps": ["render"],
                                                                  "state": st or {}}).json()
    hb({"agent_started": time.time() - 100})
    jid = c.post("/api/previews/render", json={"ids": [1, 2, 3]}).json()["id"]
    first = time.time() - 100
    assert [j["id"] for j in hb({"agent_started": first})["jobs"]] == [jid]
    long_ago = time.time() - 4000      # taken over an hour ago, still running: kept fresh, not handed out again
    with db.write_lock:
        db.conn().execute("UPDATE jobs SET taken=? WHERE id=?", (long_ago, jid))
        db.conn().commit()
    assert hb({"agent_started": first, "render": {"job": jid, "ended": None, "total": 3, "done": 1}})["jobs"] == []
    assert db.conn().execute("SELECT taken FROM jobs WHERE id=?", (jid,)).fetchone()[0] > time.time() - 5
    # photo 1 got its preview, then the PC was restarted: the agent started after it took the job
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buf, "JPEG")
    c.post("/api/photos/1/preview", content=buf.getvalue())
    again = hb({"agent_started": time.time()})["jobs"]
    assert [j["id"] for j in again] == [jid] and again[0]["payload"]["ids"] == [2, 3]
    # a job being stopped when the agent restarted is stopped, not handed out
    j2 = c.post("/api/previews/render", json={"ids": [2]}).json()["id"]
    hb({"agent_started": time.time() - 50})
    c.post("/api/previews/render/%d/stop" % j2)
    hb({"agent_started": time.time()})
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (j2,)).fetchone()[0] == "stopped"


def test_agent_retries_a_photo_once_and_ignores_a_job_it_has(clean, base_url, agent_mod, fake_dt, monkeypatch):  # noqa: F811
    a = agent_mod.Agent({"id": "pc-render4", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)
    real, calls = a.render_source, []

    def flaky(pid, tmp_root):
        calls.append(pid)
        if calls.count(pid) == 1 and pid == "1":
            raise OSError("connection reset (woke from sleep)")
        return real(pid, tmp_root)
    monkeypatch.setattr(a, "render_source", flaky)
    assert a.render_job(["1", "2"], 21) == "done"
    assert a.state()["render"]["done"] == 2 and a.state()["render"]["failed"] == 0 and calls == ["1", "1", "2"]
    reported = []
    monkeypatch.setattr(a, "report_done", lambda jid, st: reported.append((jid, st)) or True)
    a.render_seen.add(30)
    a.handle({"id": 30, "kind": "render", "payload": {"ids": [1]}})
    assert reported == [] and calls == ["1", "1", "2"]     # handed out again while still had: left to the first
    assert "agent_started" in a.state()


def test_photos_are_rendered_in_batches_at_preview_quality(clean, base_url, agent_mod, fake_dt, monkeypatch):  # noqa: F811
    a = agent_mod.Agent({"id": "pc-render5", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)
    ids = [str(r[0]) for r in db.conn().execute("SELECT id FROM photos ORDER BY id LIMIT 3")]
    for i in ids:
        make_stale(int(i))
    before = len(fake_dt["log"].read_text().splitlines()) if fake_dt["log"].exists() else 0
    assert a.render_job(ids, 40) == "done"
    runs = [json.loads(l) for l in fake_dt["log"].read_text().splitlines()[before:]]
    assert all("--hq" in r and r[r.index("--hq") + 1] == "false" for r in runs)
    batched = [r for r in runs if "--import" in r]
    assert len(batched) == 1 and batched[0].count("--import") == 3      # all three in one darktable-cli run
    assert sum(r.count("--import") for r in batched) + len(runs) - len(batched) == 3
    assert all(db.conn().execute("SELECT preview_src FROM photos WHERE id=?", (int(i),)).fetchone()[0] == "rendered" for i in ids)


def test_a_photo_the_batch_left_out_is_rendered_alone(agent_mod, fake_dt, tmp_path):  # noqa: F811
    files = []
    for n in ("1.NEF", "2-unreadable.NEF"):
        f = tmp_path / n
        f.write_bytes(b"x")
        files.append(f)
    got = agent_mod.render_batch({"darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]},
                                 files, tmp_path / "out")
    assert list(got) == [files[0]] and got[files[0]].name == "1.jpg"


def test_darktable_sees_the_progress_and_can_cancel(clean, base_url, agent_mod, fake_dt, tmp_path, monkeypatch):  # noqa: F811
    a = agent_mod.Agent({"id": "pc-render6", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)
    assert a.render_job(["1", "2"], 50) == "done"
    assert (tmp_path / "bridge" / "render.txt").read_text().split("\t")[:6] == ["50", "2", "0", "2", "1", "0"]
    p = a.state()["render"]
    assert p["errors"] == [] and p["download_s"] >= 0 and p["render_s"] > 0 and p["bytes"] > 0
    (tmp_path / "bridge" / "render-stop.txt").write_text("51")   # Cancel in darktable
    assert a.render_job(["1", "2"], 51) == "stopped"
    assert not (tmp_path / "bridge" / "render-stop.txt").exists()
    assert (tmp_path / "bridge" / "render.txt").read_text().split("\t")[4:6] == ["1", "1"]


def test_failures_say_why(clean, base_url, agent_mod, fake_dt, monkeypatch):  # noqa: F811
    a = agent_mod.Agent({"id": "pc-render7", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    monkeypatch.setattr(agent_mod.time, "sleep", lambda s: None)
    assert a.render_job(["999999"], 52) == "failed"                     # no such photo on the NAS
    e = a.state()["render"]["errors"]
    assert len(e) == 1 and e[0]["error"].startswith("download:")


def test_job_list_stop_offline_clear_and_several_pcs(clean):
    c = clean
    with db.write_lock:
        db.conn().execute("DELETE FROM agents")
        db.conn().execute("DELETE FROM jobs")
        db.conn().commit()
    for i in (1, 2, 3):
        make_stale(i)
    hb = lambda aid, name, st=None: c.post("/api/agents/heartbeat", json={"id": aid, "name": name, "os": "linux",
                                                                         "caps": ["render"], "state": st or {}}).json()
    hb("pc-d", "Desktop")
    hb("pc-l", "Laptop")
    r = c.post("/api/previews/render", json={"ids": [1, 2, 3], "agent_ids": ["pc-d", "pc-l"]}).json()
    assert [(j["agent_name"], j["count"]) for j in r["jobs"]] == [("Desktop", 2), ("Laptop", 1)]
    assert r["agent_name"] == "Desktop and Laptop"
    jd, jl = r["jobs"][0]["id"], r["jobs"][1]["id"]
    jobs = {j["id"]: j for j in c.get("/api/previews/jobs").json()["jobs"]}
    assert jobs[jd]["status"] == "queued" and jobs[jd]["total"] == 2 and jobs[jd]["progress"] is None
    hb("pc-l", "Laptop", {"agent_started": time.time() - 10})              # the Laptop takes its job ...
    hb("pc-l", "Laptop", {"agent_started": time.time() - 10, "render": {"job": jl, "done": 0, "total": 1, "ended": None}})
    assert c.get("/api/previews/jobs").json()["jobs"][1]["progress"]["job"] == jl
    with db.write_lock:                                                   # ... and goes to sleep
        db.conn().execute("UPDATE agents SET last_seen=? WHERE id='pc-l'", (time.time() - 3600,))
        db.conn().commit()
    c.post("/api/previews/render/%d/stop" % jl)                           # Stop while it is away: stopped at once
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (jl,)).fetchone()[0] == "stopped"
    back = hb("pc-l", "Laptop", {"agent_started": time.time() - 10, "render": {"job": jl, "done": 0, "total": 1, "ended": None}})
    assert back.get("stop_render") == [jl] and back["jobs"] == []         # back and still at it: told to stop
    c.post("/api/previews/render/%d/clear" % jd)                          # cleared: stopped (queued) and off the bar
    ids_on_bar = [j["id"] for j in c.get("/api/previews/jobs").json()["jobs"]]
    assert jd not in ids_on_bar and jl in ids_on_bar                      # Laptop's ended a moment ago: still shown
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (jd,)).fetchone()[0] == "stopped"
    with db.write_lock:
        db.conn().execute("UPDATE jobs SET ended=? WHERE id=?", (time.time() - 120, jl))
        db.conn().commit()
    assert c.get("/api/previews/jobs").json()["jobs"] == []               # ended over a minute ago: gone by itself


def test_an_installed_bridge_is_refreshed_when_the_agent_starts(agent_mod, tmp_path):  # noqa: F811
    conf = tmp_path / "dtconf"
    dest = conf / "lua" / "lightphotolibrary" / "lpl_bridge.lua"
    assert agent_mod.refresh_bridge({"darktable_configdir": str(conf)}) is None       # none installed: none added
    assert not dest.exists()
    dest.parent.mkdir(parents=True)
    dest.write_text("-- old bridge")
    assert agent_mod.refresh_bridge({"darktable_configdir": str(conf)}) == dest
    assert "render.txt" in dest.read_text()
    assert agent_mod.refresh_bridge({"darktable_configdir": str(conf)}) is None       # same now: left alone


def test_windows_output_paths_use_forward_slashes(agent_mod, monkeypatch):  # noqa: F811
    from pathlib import PureWindowsPath
    monkeypatch.setattr(agent_mod, "IS_WIN", True)
    p = PureWindowsPath(r"C:\Users\tomma\AppData\Local\LightPhotoLibrary\render\job-54\out-ec00\$(FILE_NAME)")
    assert agent_mod.cli_out(p) == "C:/Users/tomma/AppData/Local/LightPhotoLibrary/render/job-54/out-ec00/$(FILE_NAME)"
    monkeypatch.setattr(agent_mod, "IS_WIN", False)
    assert agent_mod.cli_out(agent_mod.Path("/tmp/x.jpg")) == "/tmp/x.jpg"


def test_missing_module_messages():
    from test_pc_agent import agent_mod as _  # noqa: F401
    import importlib.util
    spec = importlib.util.spec_from_file_location("ag_mm", str(__import__("pathlib").Path(__file__).resolve().parents[2] / "agent" / "lpl_agent.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    said = ("Magick: caught exception 0xC0000005 \"Access violation\"...\n1,4363 [dev_read_history] the module `spektrafilm' "
            "requested by image id=1 `43457.RAF' is not installed on this computer!\n1,4776 cannot get iop-order for agx instance 0")
    assert m.missing_modules(said) == {"spektrafilm": "43457.RAF"}
    # 27 Nov: several exposure instances etc. on the Desktop, wrongly taken for missing modules
    assert m.missing_modules("cannot get iop-order for channelmixerrgb instance 1\ncannot get iop-order for exposure instance 2\n"
                             "cannot get iop-order for toneequal instance 1\n[export_job] exported to `x.jpg'") == {}
    assert m.missing_modules("[export_job] exported to `x.jpg'") == {}


def test_an_edit_with_a_module_this_darktable_lacks_is_left_for_another_pc(clean, base_url, agent_mod, fake_dt, monkeypatch):  # noqa: F811
    a = agent_mod.Agent({"id": "pc-render8", "name": "Desktop", "server": base_url, "password": "", "auto_sync_seconds": 0,
                         "darktable": fake_dt["darktable"], "darktable_configdir": fake_dt["darktable_configdir"]})
    slept = []
    monkeypatch.setattr(agent_mod.time, "sleep", lambda s: slept.append(s))
    real = a.render_source

    def with_spektra(pid, root):
        src = real(pid, root)
        if pid == "2":
            src["xmp"].write_text("<x darktable:operation='spektrafilm'/>")
        return src
    monkeypatch.setattr(a, "render_source", with_spektra)
    make_stale(1)
    make_stale(2)
    make_stale(3)
    assert a.render_job(["1", "2", "3"], 60) == "done"
    p = a.state()["render"]
    assert (p["done"], p["failed"], p["lacking"], p["lacking_modules"]) == (2, 1, [2], ["spektrafilm"])
    assert "spektrafilm, which darktable on this PC does not have" in p["errors"][0]["error"]
    assert 5 not in slept                                             # not tried again: it would fail the same way
    assert db.conn().execute("SELECT preview_src FROM photos WHERE id=2").fetchone()[0] != "rendered"   # no wrong look posted


def test_previews_use_the_darktable_that_runs_not_another_install(agent_mod, tmp_path, monkeypatch):  # noqa: F811
    """Desktop, 26 Nov: the darktable in Program Files was older than the one Tommaso runs (no spektrafilm)."""
    monkeypatch.setattr(agent_mod, "data_dir", lambda: tmp_path / "agentdata")
    monkeypatch.setattr(agent_mod, "save_config", lambda cfg: None)
    old = tmp_path / "ProgramFiles" / "darktable" / "bin"
    new = tmp_path / "Apps" / "darktable-dev" / "bin"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "darktable").write_text("x")
        (d / "darktable-cli").write_text("x")
    cfg = {"darktable": str(old / "darktable")}
    assert agent_mod.darktable_cli_cmd(cfg) == [str(old / "darktable-cli")]
    monkeypatch.setattr(agent_mod, "running_darktable_exe", lambda: str(new / "darktable"))
    agent_mod.note_running_darktable(cfg)
    assert cfg["darktable_seen"] == str(new / "darktable")
    assert agent_mod.darktable_cli_cmd(cfg) == [str(new / "darktable-cli")]
    monkeypatch.setattr(agent_mod, "running_darktable_exe", lambda: None)   # closed again: still the one seen
    agent_mod.note_running_darktable(cfg)
    assert agent_mod.darktable_cli_cmd(cfg) == [str(new / "darktable-cli")]
    monkeypatch.setattr(agent_mod, "running_darktable_exe", lambda: "/tmp/.mount_Darktab1/usr/bin/darktable")
    agent_mod.note_running_darktable(cfg)                                     # an AppImage's mount is not kept
    assert cfg["darktable_seen"] == str(new / "darktable")


def test_the_dialog_learns_each_pcs_darktable(clean):
    with db.write_lock:
        db.conn().execute("DELETE FROM agents")
        db.conn().commit()
    make_stale(1)
    clean.post("/api/agents/heartbeat", json={"id": "pc-v", "name": "Desktop", "os": "windows", "caps": ["render"],
                                              "state": {"render_with": {"cli": "x", "version": "this is darktable 5.5.0+412"}}})
    assert stale(clean)["agents"] == [{"id": "pc-v", "name": "Desktop", "darktable": "this is darktable 5.5.0+412"}]
