# SPDX-License-Identifier: GPL-3.0-or-later
"""The PC agent (agent/lpl_agent.py) against this server over real HTTP: it names itself in XMP posts
(agent=, R15) and reports step and progress while it copies and syncs a photo (R16). darktable is not
started (open_paths is replaced)."""
import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from lpl import db, scanner, xmp

AGENT_PY = Path(__file__).resolve().parents[2] / "agent" / "lpl_agent.py"
DATA = Path(__file__).resolve().parent / "fixtures" / "xmp"


@pytest.fixture(scope="module")
def base_url(client):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    from lpl import api
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield "http://127.0.0.1:%d" % port
    server.should_exit = True
    t.join(5)


@pytest.fixture()
def agent_mod(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("lpl_agent_under_test", AGENT_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "WORK", tmp_path / "work")
    monkeypatch.setattr(m, "BRIDGE", tmp_path / "bridge")
    monkeypatch.setattr(m, "STATE_FILE", tmp_path / "state.json")
    return m


def test_agent_names_itself_and_reports_progress(clean, base_url, agent_mod, monkeypatch):
    c = clean
    with db.write_lock:
        db.conn().execute("DELETE FROM edit_events")
        db.conn().execute("DELETE FROM edit_baseline")
        db.conn().commit()
    r = db.conn().execute("SELECT relpath FROM photos WHERE id=2").fetchone()
    sc = xmp.sidecar_path(scanner.abs_of(r["relpath"]))
    if sc.exists():
        sc.unlink()

    cfg = {"id": "pc-agent-test", "name": "Desktop", "server": base_url, "password": "", "auto_sync_seconds": 0}
    a = agent_mod.Agent(cfg)
    monkeypatch.setattr(a, "open_paths", lambda paths: None)
    seen = []  # the working-set item as each heartbeat would report it, at every step change
    real_step = a.step

    def spy(it, step=None, progress=None):
        real_step(it, step, progress)
        seen.append(next(w for w in a.state()["working_set"] if w["id"] == it["id"]).copy())
    monkeypatch.setattr(a, "step", spy)

    def heartbeat():
        assert c.post("/api/agents/heartbeat", json={"id": cfg["id"], "name": cfg["name"], "os": "linux",
                                                      "state": a.state()}).status_code == 200

    a.edit(["2"])
    downloading = [w for w in seen if w.get("step") == "downloading"]
    assert downloading and downloading[0]["progress"] == 0.0 and downloading[-1]["progress"] == 1.0
    assert "step" not in a.state()["working_set"][0]  # done: no step any more
    heartbeat()

    # darktable wrote the edit: the agent sends it, naming itself
    it = a.items["2"]
    Path(it["local_path"] + ".xmp").write_bytes((DATA / "exposure.xmp").read_bytes())
    seen.clear()
    jpg = Path(it["local_path"] + ".jpg")
    from PIL import Image
    Image.new("RGB", (32, 24), (90, 120, 150)).save(jpg)
    a.sync("2", force=True, preview=jpg)
    assert [w.get("step") for w in seen] == ["sending preview", None, "sending edit", None]
    assert a.items["2"]["status"] == "synced"
    ev = c.get("/api/photos/2/edits").json()["edits"]
    assert len(ev) == 1 and ev[0]["agent_id"] == "pc-agent-test" and ev[0]["device"] == "Desktop"
    assert c.get("/api/photos/2").json()["last_edit"]["device"] == "Desktop"

    # a step left in the state file by an agent that was stopped is not reported after a restart
    a.step(a.items["2"], "downloading", 0.3)
    a.save()
    b = agent_mod.Agent(cfg)
    assert "step" not in b.state()["working_set"][0]
    sc.unlink()


# ---------------------------------------------------------------- batches: progress, Stop, "Don't send", restart
BIG = 3 * 1024 * 1024  # bytes of each synthetic "RAW"


@pytest.fixture()
def big(clean):
    """Five large files in the library (rows made directly: they are not images, and nothing here reads them as such)."""
    import os
    from lpl import transfers
    from lpl.config import config
    d = config.photo_root / "big"
    d.mkdir(parents=True, exist_ok=True)
    ids = []
    with db.write_lock:
        c = db.conn()
        c.execute("DELETE FROM transfers")
        c.execute("DELETE FROM transfer_items")
        for i in range(5):
            f = d / ("BIG_%d.RAF" % i)
            f.write_bytes(os.urandom(BIG))
            cur = c.execute("INSERT INTO photos(relpath, folder, filename, ext, size, mtime) VALUES(?,?,?,?,?,?)",
                            ("big/" + f.name, "big", f.name, "raf", BIG, f.stat().st_mtime))
            ids.append(cur.lastrowid)
        c.commit()
    transfers._samples.clear()
    yield ids
    with db.write_lock:
        db.conn().execute("DELETE FROM photos WHERE folder='big'")
        db.conn().commit()
    for f in d.iterdir():
        f.unlink()


@pytest.fixture()
def running_agent(clean, base_url, agent_mod, monkeypatch):
    """The real agent loop (run(): heartbeats, worker, watch) in a thread, with a slow line for originals and a
    stand-in for starting darktable that only notes what would have been opened."""
    m = agent_mod
    launched = []
    monkeypatch.setattr(m, "darktable_running", lambda: False)
    monkeypatch.setattr(m, "launch_darktable", lambda cfg, image, guard=True: launched.append(Path(image).name) or True)
    real_req = m.Server._req
    slow = {"delay": 0.02, "chunk": 32 * 1024}   # about 1.6 MB/s: a 3 MB file takes two seconds

    def req(self, method, path, data=None, ctype=None, timeout=30):
        r = real_req(self, method, path, data, ctype, timeout)
        if path.endswith("/original"):
            read1 = r.read1

            def throttled(n=-1):
                time.sleep(slow["delay"])
                return read1(min(n, slow["chunk"]) if n and n > 0 else slow["chunk"])
            r.read1 = throttled
        return r
    monkeypatch.setattr(m.Server, "_req", req)
    cfg = {"id": "pc-batch-test", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
           "poll_seconds": 0.2, "local_library": False}
    a = m.Agent(cfg)
    t = threading.Thread(target=a.run, daemon=True)
    t.start()
    yield a, launched, slow
    a.quit.set()
    a.wake.set()
    a.cancel_jobs = {j for j in a.batches}  # whatever still copies ends at its next chunk
    t.join(10)


def device(c, aid="pc-batch-test"):
    return next((a for a in c.get("/api/agents").json()["agents"] if a["id"] == aid), None)


def wait_for(what, timeout=20.0, every=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = what()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timed out")


def test_batch_progress_stop_in_the_middle_of_a_file_and_resume(big, running_agent):
    from lpl import api
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    a, launched, slow = running_agent
    ids = big
    wait_for(lambda: device(c))
    assert device(c)["caps"] == ["batch", "cancel", "open", "versions", "shared"]
    j = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": ids}, "agent_id": "pc-batch-test"}).json()["id"]

    # the second file is under way: one arrived, the numbers on the server are the agent's
    def second():
        t = device(c)["transfer"]
        return t if t and t["photos_done"] == 1 and t["current"] and t["current"]["id"] == ids[1] and t["current"]["bytes"] > 200_000 else None
    t = wait_for(second)
    assert (t["photos_total"], t["bytes_total"], t["state"]) == (5, 5 * BIG, "running")
    assert BIG < t["bytes_done"] < 2 * BIG and [w["id"] for w in t["waiting"]] == ids[2:]
    ws = {w["id"]: w for w in device(c)["state"]["working_set"]}
    assert ws[ids[0]]["status"] == "ready" and ws[ids[1]]["status"] == "downloading" and ws[ids[1]]["step"] == "downloading"
    work = Path(a.items[str(ids[0])]["local_path"]).parent
    assert [p.name for p in work.glob("*.part")] == ["%d_BIG_1.RAF.part" % ids[1]]

    # Stop, in the middle of that file
    t0 = time.time()
    assert c.post("/api/agents/pc-batch-test/stop").json()["stopped"] == 1
    wait_for(lambda: not list(work.glob("*.part")), 5)
    gone = time.time() - t0
    s = wait_for(lambda: device(c)["stopped"], 5)
    took = time.time() - t0
    print("Stop: partial file gone after %.2f s, shown as stopped after %.2f s" % (gone, took))
    assert took < 2.0
    assert (s["photos_done"], s["photos_total"], s["reason"]) == (1, 5, "user") and [u["id"] for u in s["unsent"]] == ids[1:]
    d = device(c)
    assert d["transfer"] is None
    # the same heartbeat that said "stopped" brought the working set as it is now: no row still "copying"
    assert [(w["id"], w["status"], w.get("step")) for w in d["state"]["working_set"]] == [(ids[0], "ready", None)]
    time.sleep(0.6)
    assert sorted(p.name for p in work.iterdir()) == ["%d_BIG_0.RAF" % ids[0], "%d_BIG_0.RAF.xmp" % ids[0]]   # what arrived, nothing else
    assert list(a.items) == [str(ids[0])] and a.items[str(ids[0])]["status"] == "ready"
    assert [w["id"] for w in device(c)["state"]["working_set"]] == [ids[0]]
    assert launched == []                                    # darktable is not opened after a Stop
    wait_for(lambda: a.batches == {}, 5)                     # confirmed with /done after the heartbeat that said it
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (j,)).fetchone()["status"] == "stopped"

    # "Don't send" one of the rest, then Resume: the others arrive, and now darktable is opened (once)
    c.post("/api/agents/pc-batch-test/dont-send", json={"ids": [ids[4]]})
    slow["delay"] = 0.002
    r = c.post("/api/agents/pc-batch-test/resume").json()
    assert r["photos"] == 3
    wait_for(lambda: db.conn().execute("SELECT status FROM jobs WHERE id=?", (r["id"],)).fetchone()["status"] == "done")
    d = device(c)
    assert d["transfer"] is None and d["stopped"] is None
    # the first of the batch is the one opened in the darkroom; the others are there, ready
    wait_for(lambda: {w["id"]: w["status"] for w in device(c)["state"]["working_set"]}
             == {ids[0]: "ready", ids[1]: "editing", ids[2]: "ready", ids[3]: "ready"}, 5)
    assert launched == ["%d_BIG_1.RAF" % ids[1]]
    assert not list(work.glob("*.part"))


def test_dont_send_the_photo_being_copied_and_open_switched_off(big, running_agent):
    from lpl import api
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    a, launched, slow = running_agent
    ids = big[:3]
    c.put("/api/prefs/settings", json={"value": {"devicesAutoOpen": False}})
    try:
        wait_for(lambda: device(c))
        j = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": ids}, "agent_id": "pc-batch-test"}).json()["id"]
        wait_for(lambda: (device(c)["transfer"] or {}).get("current") and device(c)["transfer"]["current"]["bytes"] > 100_000)
        cur = device(c)["transfer"]["current"]["id"]
        assert cur == ids[0]
        t0 = time.time()
        c.post("/api/agents/pc-batch-test/dont-send", json={"ids": [cur]})
        slow["delay"] = 0.002
        # that file is given up, the batch carries on with the next
        wait_for(lambda: (device(c)["transfer"] or {}).get("current") and device(c)["transfer"]["current"]["id"] == ids[1] or device(c)["transfer"] is None, 5)
        print("Don't send (photo being copied): next file after %.2f s" % (time.time() - t0))
        wait_for(lambda: db.conn().execute("SELECT status FROM jobs WHERE id=?", (j,)).fetchone()["status"] == "done")
        assert sorted(a.items) == sorted(str(i) for i in ids[1:])
        work = Path(a.items[str(ids[1])]["local_path"]).parent
        assert sorted(p.name for p in work.iterdir()) == sorted(n for i, k in ((ids[1], 1), (ids[2], 2)) for n in ("%d_BIG_%d.RAF" % (i, k), "%d_BIG_%d.RAF.xmp" % (i, k)))
        wait_for(lambda: {w["status"] for w in device(c)["state"]["working_set"]} == {"ready"}, 5)   # (its next heartbeat)
        assert launched == []                                # "Open photos in darktable when they have arrived" is off
        assert db.conn().execute("SELECT state FROM transfer_items WHERE job_id=? AND photo_id=?", (j, cur)).fetchone()["state"] == "skipped"
    finally:
        c.put("/api/prefs/settings", json={"value": {}})


def test_agent_restarted_in_the_middle_of_a_batch(big, base_url, agent_mod, monkeypatch):
    """The agent is killed while it copies: after the restart the partial file and the half-made entry are gone,
    and the server shows the batch as stopped with what arrived."""
    from lpl import api
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    m = agent_mod
    monkeypatch.setattr(m, "darktable_running", lambda: False)
    launched = []
    monkeypatch.setattr(m, "launch_darktable", lambda cfg, image, guard=True: launched.append(image) or True)
    cfg = {"id": "pc-batch-test", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0,
           "poll_seconds": 0.2, "local_library": False}
    ids = big[:3]
    a = m.Agent(cfg)
    a.take(a.srv.json("POST", "/api/agents/heartbeat", a.heartbeat_body()))
    j = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": ids}, "agent_id": cfg["id"]}).json()["id"]
    a.take(a.srv.json("POST", "/api/agents/heartbeat", a.heartbeat_body()))
    job = a.jobs.get_nowait()

    class Killed(BaseException):
        pass
    real = m.Server.download
    n = {"calls": 0}

    def dying(self, path, dest, progress=None, cancel=None, tag=None):
        n["calls"] += 1
        if n["calls"] == 3:  # RAW and XMP of the first photo are there; the second RAW dies half way
            (dest.with_name(dest.name + ".part")).write_bytes(b"x" * 1000)
            a.save()
            raise Killed()
        return real(self, path, dest, progress, cancel, tag)
    monkeypatch.setattr(m.Server, "download", dying)
    with pytest.raises(Killed):
        a.edit([str(i) for i in ids], job)
    a.srv.json("POST", "/api/agents/heartbeat", a.heartbeat_body())
    assert device(c)["transfer"]["state"] == "running"
    monkeypatch.setattr(m.Server, "download", real)

    b = m.Agent(cfg)          # the new process: same state file, no memory of the job
    assert b.items[str(ids[1])]["status"] == "downloading"
    b.recover()
    work = m.WORK
    assert list(b.items) == [str(ids[0])] and not list(work.glob("*.part"))
    assert sorted(p.name for p in work.iterdir()) == ["%d_BIG_0.RAF" % ids[0], "%d_BIG_0.RAF.xmp" % ids[0]]
    r = b.srv.json("POST", "/api/agents/heartbeat", b.heartbeat_body())
    assert r["jobs"] == [] and r["cancel"] == {"jobs": [], "photos": []}
    d = device(c)
    assert d["transfer"] is None
    assert (d["stopped"]["photos_done"], d["stopped"]["reason"], [u["id"] for u in d["stopped"]["unsent"]]) == (1, "interrupted", ids[1:])
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (j,)).fetchone()["status"] == "stopped"
    assert launched == []


def test_download_never_leaves_a_short_file(base_url, agent_mod, tmp_path, big):
    """A connection that ends early is an error, not a photo; the partial file is removed either way."""
    m = agent_mod
    srv = m.Server({"server": base_url, "password": ""})
    dest = tmp_path / "x.RAF"
    real_req = m.Server._req

    def req(self, method, path, data=None, ctype=None, timeout=30):
        r = real_req(self, method, path, data, ctype, timeout)
        read1, n = r.read1, {"n": 0}

        def cut(k=-1):
            n["n"] += 1
            return b"" if n["n"] > 2 else read1(1000)
        r.read1 = cut
        return r
    m.Server._req = req
    try:
        with pytest.raises(IOError, match="ended after 2000 of %d bytes" % BIG):
            srv.download("/api/photos/%d/original" % big[0], dest)
    finally:
        m.Server._req = real_req
    assert not dest.exists() and not (tmp_path / "x.RAF.part").exists()
    with pytest.raises(m.Cancelled):
        srv.download("/api/photos/%d/original" % big[0], dest, cancel=lambda: True)
    assert not dest.exists() and not (tmp_path / "x.RAF.part").exists()
    srv.download("/api/photos/%d/original" % big[0], dest)
    assert dest.stat().st_size == BIG and srv.current is None


# ---------------------------------------------------------------- a copy that failed; names
def test_failed_copy_is_retried_and_can_be_forgotten(clean, base_url, agent_mod, monkeypatch, tmp_path):
    """A copy that failed is not taken for a photo that is here: Retry copies it again, release/forget delete what
    it left (release used to return early for any photo with an error, so it could never leave the list)."""
    cfg = {"id": "pc-agent-fail", "name": "Laptop", "server": base_url, "password": "", "auto_sync_seconds": 0}
    a = agent_mod.Agent(cfg)
    monkeypatch.setattr(a, "open_paths", lambda paths: None)
    real = a.srv.download
    boom = {"on": True}

    def flaky(path, dest, *args, **kw):
        if boom["on"] and path.endswith("/xmp"):
            raise OSError("NAS read timed out")
        return real(path, dest, *args, **kw)
    monkeypatch.setattr(a.srv, "download", flaky)
    a.edit(["2"], {"id": 0, "payload": {}})
    w = a.state()["working_set"][0]
    assert w["status"] == "error" and w["copy_failed"] is True
    boom["on"] = False
    a.edit(["2"], {"id": 0, "payload": {}})                       # Retry: copied again, not "present"
    assert a.items["2"]["status"] == "ready" and "copy_failed" not in a.state()["working_set"][0]
    a.forget("2", "failed")                                        # a good copy is not a failed one: stays
    assert "2" in a.items
    a.forget("2", "keep")                                          # forget, keep the files
    assert "2" not in a.items
    boom["on"] = True
    a.edit(["2"], {"id": 0, "payload": {}})
    p = Path(a.items["2"]["local_path"])
    a.handle({"id": 999999, "kind": "release", "payload": {"ids": [2]}})
    assert "2" not in a.items and not p.exists()


def test_agent_takes_a_rename_and_rereads_its_config(base_url, agent_mod, monkeypatch, tmp_path):
    cfgfile = tmp_path / "agent.json"
    monkeypatch.setattr(agent_mod, "config_path", lambda: cfgfile)
    cfg = {"id": "pc-agent-name", "name": "fedora", "server": base_url, "password": ""}
    agent_mod.save_config(cfg)
    a = agent_mod.Agent(dict(cfg))
    assert a.heartbeat_body()["name"] == "fedora"
    import json as _j, os as _os
    cfgfile.write_text(_j.dumps(dict(cfg, name="Laptop")))         # the setup run again while the agent runs
    _os.utime(cfgfile, (time.time() + 5, time.time() + 5))
    assert a.heartbeat_body()["name"] == "Laptop"
    a.take({"rename": "Portatile"})
    assert a.cfg["name"] == "Portatile" and _j.loads(cfgfile.read_text())["name"] == "Portatile"
    assert isinstance(agent_mod.machine_id(), str)
