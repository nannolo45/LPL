# SPDX-License-Identifier: GPL-3.0-or-later
"""Batches of photos sent to a device (lpl/transfers.py): what is known up front, progress as the device reports
it, Stop, "Don't send", Resume, a device that restarts or goes away, and devices that announce no capabilities
(they must get and send exactly what they did before). See docs/devices-stop-estimate.md."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from lpl import api, db, transfers
from lpl.config import config

PC = {"id": "pc-new", "name": "Laptop", "os": "linux"}
OLDPC = {"id": "pc-old", "name": "Desktop", "os": "windows"}
PHONE = {"id": "android-0000-test", "name": "Telefono", "os": "android"}
CAPS = ["batch", "cancel", "open"]
HERE = Path(__file__).resolve().parent


@pytest.fixture()
def c(clean):
    with db.write_lock:
        db.conn().execute("DELETE FROM transfers")
        db.conn().execute("DELETE FROM transfer_items")
        db.conn().execute("DELETE FROM settings WHERE key LIKE 'pref.%'")
        db.conn().commit()
    transfers._samples.clear()
    return clean


def beat(c, agent, reports=None, ws=(), caps=CAPS):
    """A heartbeat of a device with capabilities; returns the whole answer."""
    body = {**agent, "state": {"working_set": list(ws)}}
    if caps is not None:
        body.update(caps=caps, transfers=reports or [])
    r = c.post("/api/agents/heartbeat", json=body)
    assert r.status_code == 200
    return r.json()


def old_beat(c, agent, ws=()):
    """Exactly what today's devices send."""
    r = c.post("/api/agents/heartbeat", json={**agent, "state": {"working_set": list(ws)}})
    assert r.status_code == 200
    return r.json()


def send(c, ids, agent_id):
    return c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": ids}, "agent_id": agent_id}).json()["id"]


def dev(c, agent):
    return next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == agent["id"])


def sizes():
    return {r["id"]: r["size"] for r in db.conn().execute("SELECT id, size FROM photos").fetchall()}


def name(pid):
    return db.conn().execute("SELECT filename FROM photos WHERE id=?", (pid,)).fetchone()["filename"]


def job_status(jid):
    return db.conn().execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()["status"]


def rep(job, state="running", **kw):
    return {"job": job, "state": state, "arrived": [], "present": [], "failed": [], "skipped": [], "photo": None, "bytes": 0, **kw}


# ---------------------------------------------------------------- registration and totals
def test_batch_is_known_up_front(c):
    beat(c, PC)  # the device exists
    c.post("/api/agents/heartbeat", json={**PC, "state": {}, "caps": CAPS, "transfers": []})
    j = send(c, [1, 2, 3], PC["id"])
    t = dev(c, PC)["transfer"]
    sz = sizes()
    assert t["state"] == "queued" and t["photos_total"] == 3 and t["photos_done"] == 0
    assert t["bytes_total"] == sz[1] + sz[2] + sz[3] and t["bytes_done"] == 0
    assert [(w["id"], w["job"]) for w in t["waiting"]] == [(1, j), (2, j), (3, j)]
    assert t["waiting"][0]["filename"] == name(1)
    a = beat(c, PC)
    assert a["jobs"] == [{"id": j, "kind": "edit", "payload": {"ids": [1, 2, 3], "open": True}}]
    assert a["cancel"] == {"jobs": [], "photos": []}
    assert dev(c, PC)["transfer"]["state"] == "running"


def test_progress_as_the_device_reports_it(c):
    beat(c, PC)
    j = send(c, [1, 2, 3], PC["id"])
    beat(c, PC)
    sz = sizes()
    beat(c, PC, [rep(j, arrived=[1], photo=2, bytes=100)])
    t = dev(c, PC)["transfer"]
    assert (t["photos_done"], t["photos_total"]) == (1, 3)
    assert t["bytes_done"] == sz[1] + 100 and t["bytes_total"] == sz[1] + sz[2] + sz[3]
    assert t["current"] == {"id": 2, "filename": name(2), "bytes": 100, "size": sz[2]}
    assert [w["id"] for w in t["waiting"]] == [3]           # the one being copied is not "waiting"
    assert t["arrived"] == [1]
    assert len(t["samples"]) == 2 and t["samples"][-1][1:] == [sz[1] + 100, 1]
    # bytes beyond the file's size are clamped; a photo that was on the device already costs no bytes
    beat(c, PC, [rep(j, arrived=[1], present=[2], photo=3, bytes=10 ** 12)])
    t = dev(c, PC)["transfer"]
    assert t["photos_done"] == 2 and t["bytes_total"] == sz[1] + sz[3] and t["bytes_done"] == sz[1] + sz[3]
    # done: nothing left to show; the job is done
    assert c.post("/api/jobs/%d/done" % j, json={"status": "done", "arrived": [1, 3], "present": [2]}).status_code == 200
    d = dev(c, PC)
    assert d["transfer"] is None and d["stopped"] is None
    assert job_status(j) == "done"
    assert beat(c, PC)["jobs"] == []


def test_the_same_photo_twice(c):
    beat(c, PC)
    j1 = send(c, [1, 1, 2, "2"], PC["id"])                  # twice in one request: once
    assert dev(c, PC)["transfer"]["photos_total"] == 2
    j2 = send(c, [2, 3], PC["id"])                          # again while it still waits: counted once
    t = dev(c, PC)["transfer"]
    assert t["photos_total"] == 3 and [w["id"] for w in t["waiting"]] == [1, 2, 3]
    assert t["bytes_total"] == sum(sizes()[i] for i in (1, 2, 3))
    a = beat(c, PC)
    assert [(x["id"], x["payload"]["ids"]) for x in a["jobs"]] == [(j1, [1, 2]), (j2, [2, 3])]


def test_batches_for_one_device_add_up(c):
    beat(c, PC)
    j1 = send(c, [1, 2], PC["id"])
    beat(c, PC)
    beat(c, PC, [rep(j1, arrived=[1])])
    j2 = send(c, [3], PC["id"])
    beat(c, PC, [rep(j1, arrived=[1])])
    t = dev(c, PC)["transfer"]
    assert (t["photos_done"], t["photos_total"], t["state"]) == (1, 3, "running")
    # the first one ends, the second carries on: still one figure
    c.post("/api/jobs/%d/done" % j1, json={"status": "done", "arrived": [1, 2]})
    beat(c, PC, [rep(j2, photo=3, bytes=5)])
    t = dev(c, PC)["transfer"]
    assert (t["photos_done"], t["photos_total"]) == (2, 3) and t["current"]["id"] == 3
    c.post("/api/jobs/%d/done" % j2, json={"status": "done", "arrived": [3]})
    assert dev(c, PC)["transfer"] is None
    # the next batch starts from nothing
    send(c, [1], PC["id"])
    assert dev(c, PC)["transfer"]["photos_total"] == 1


def test_a_photo_the_device_could_not_copy(c):
    beat(c, PC)
    j = send(c, [1, 2, 999999], PC["id"])                   # 999999: no such photo
    beat(c, PC)
    beat(c, PC, [rep(j, arrived=[1], failed=[999999])])
    t = dev(c, PC)["transfer"]
    assert (t["photos_done"], t["photos_total"], t["failed"]) == (1, 3, 1)
    assert t["bytes_total"] == sizes()[1] + sizes()[2]      # what cannot arrive is not waited for


# ---------------------------------------------------------------- stop
def test_stop_a_running_batch(c, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    beat(c, PC)
    j = send(c, [1, 2, 3], PC["id"])
    beat(c, PC)
    beat(c, PC, [rep(j, arrived=[1], photo=2, bytes=50)])
    assert c.post("/api/agents/%s/stop" % PC["id"]).json() == {"ok": True, "stopped": 1}
    assert dev(c, PC)["transfer"]["state"] == "stopping"
    # told in every heartbeat answer until the device confirms
    assert beat(c, PC, [rep(j, arrived=[1], photo=2, bytes=60)])["cancel"] == {"jobs": [j], "photos": []}
    assert beat(c, PC, [rep(j, arrived=[1], photo=2, bytes=70)])["cancel"]["jobs"] == [j]
    assert c.post("/api/jobs/%d/done" % j, json={"status": "stopped", "arrived": [1]}).status_code == 200
    d = dev(c, PC)
    assert d["transfer"] is None
    s = d["stopped"]
    assert (s["photos_done"], s["photos_total"], s["reason"]) == (1, 3, "user")
    assert [u["id"] for u in s["unsent"]] == [2, 3] and s["arrived"] == [1] and s["bytes_done"] == sizes()[1]
    assert job_status(j) == "stopped"
    assert beat(c, PC)["cancel"] == {"jobs": [], "photos": []}
    # never handed out again, however long ago
    with db.write_lock:
        db.conn().execute("UPDATE jobs SET taken=taken-100000")
        db.conn().commit()
    assert beat(c, PC)["jobs"] == []


def test_stop_reported_in_a_heartbeat_instead_of_done(c):
    beat(c, PC)
    j = send(c, [1, 2], PC["id"])
    beat(c, PC)
    c.post("/api/agents/%s/stop" % PC["id"])
    beat(c, PC, [rep(j, "stopped", arrived=[1])])           # the device's "done" call got lost; its heartbeat says it
    s = dev(c, PC)["stopped"]
    assert (s["photos_done"], [u["id"] for u in s["unsent"]]) == (1, [2]) and job_status(j) == "stopped"
    c.post("/api/jobs/%d/done" % j, json={"status": "stopped", "arrived": [1]})   # and arrives later after all
    assert dev(c, PC)["stopped"]["photos_done"] == 1


def test_stop_arrives_just_as_the_batch_finished(c):
    beat(c, PC)
    j = send(c, [1, 2], PC["id"])
    beat(c, PC)
    c.post("/api/agents/%s/stop" % PC["id"])
    c.post("/api/jobs/%d/done" % j, json={"status": "done", "arrived": [1, 2]})
    d = dev(c, PC)
    assert d["transfer"] is None and d["stopped"] is None   # everything arrived: nothing was stopped


def test_stop_what_no_device_has_taken_yet(c):
    old_beat(c, PHONE)                                      # a device without capabilities; then its app is closed
    j = send(c, [1, 2], PHONE["id"])
    t = dev(c, PHONE)["transfer"]
    assert t["state"] == "queued" and t["can_stop"] and t["can_skip"] and t["known"]
    assert c.post("/api/agents/%s/stop" % PHONE["id"]).json()["stopped"] == 1
    d = dev(c, PHONE)
    assert d["transfer"] is None and [u["id"] for u in d["stopped"]["unsent"]] == [1, 2]
    assert old_beat(c, PHONE) == {"jobs": [], "server_time": pytest.approx(time.time(), abs=5)}
    assert job_status(j) == "stopped"


def test_stop_when_nothing_runs(c):
    beat(c, PC)
    assert c.post("/api/agents/%s/stop" % PC["id"]).json() == {"ok": True, "stopped": 0}
    assert beat(c, PC)["cancel"] == {"jobs": [], "photos": []}
    assert c.post("/api/agents/nobody/stop").status_code == 404
    d = dev(c, PC)
    assert d["transfer"] is None and d["stopped"] is None


def test_stop_while_the_device_is_away(c):
    beat(c, PC)
    j = send(c, [1, 2, 3], PC["id"])
    beat(c, PC)
    beat(c, PC, [rep(j, arrived=[1])])
    with db.write_lock:  # the laptop's lid was closed
        db.conn().execute("UPDATE agents SET last_seen=last_seen-60 WHERE id=?", (PC["id"],))
        db.conn().commit()
    d = dev(c, PC)
    assert not d["online"] and d["transfer"]["state"] == "running" and d["transfer"]["photos_done"] == 1
    assert d["transfer"]["current"] is None and d["transfer"]["samples"] == []     # nothing claims to move
    c.post("/api/agents/%s/stop" % PC["id"])
    d = dev(c, PC)
    assert d["transfer"] is None and d["stopped"]["photos_done"] == 1              # stopped at once, as far as LPL goes
    # it wakes up still copying: it is told to stop, and what arrived meanwhile counts
    a = beat(c, PC, [rep(j, arrived=[1, 2], photo=3, bytes=10)])
    assert a["cancel"]["jobs"] == [j] and a["jobs"] == []
    c.post("/api/jobs/%d/done" % j, json={"status": "stopped", "arrived": [1, 2]})
    s = dev(c, PC)["stopped"]
    assert (s["photos_done"], [u["id"] for u in s["unsent"]]) == (2, [3])
    assert beat(c, PC)["cancel"]["jobs"] == []


# ---------------------------------------------------------------- don't send
def test_dont_send_a_waiting_photo_of_a_job_not_taken_yet(c):
    old_beat(c, PHONE)
    j = send(c, [1, 2, 3], PHONE["id"])
    assert c.post("/api/agents/%s/dont-send" % PHONE["id"], json={"ids": [2]}).json() == {"ok": True, "photos": 1}
    assert [w["id"] for w in dev(c, PHONE)["transfer"]["waiting"]] == [1, 3]
    assert old_beat(c, PHONE)["jobs"] == [{"id": j, "kind": "edit", "payload": {"ids": [1, 3]}}]   # what it gets is a plain job


def test_dont_send_the_last_waiting_photo_cancels_the_job(c):
    old_beat(c, PHONE)
    j = send(c, [1], PHONE["id"])
    c.post("/api/agents/%s/dont-send" % PHONE["id"], json={"ids": [1]})
    d = dev(c, PHONE)
    assert d["transfer"] is None and d["stopped"] is None and job_status(j) == "cancelled"
    assert old_beat(c, PHONE)["jobs"] == []


def test_dont_send_while_the_batch_runs(c):
    beat(c, PC)
    j = send(c, [1, 2, 3], PC["id"])
    beat(c, PC)
    c.post("/api/agents/%s/dont-send" % PC["id"], json={"ids": [3]})
    t = dev(c, PC)["transfer"]
    assert t["photos_total"] == 2 and [w["id"] for w in t["waiting"]] == [1, 2]   # gone from the page at once
    assert beat(c, PC, [rep(j, photo=1, bytes=5)])["cancel"] == {"jobs": [], "photos": [3]}
    assert beat(c, PC, [rep(j, arrived=[1, 2], skipped=[3])])["cancel"]["photos"] == []   # confirmed
    c.post("/api/jobs/%d/done" % j, json={"status": "done", "arrived": [1, 2], "skipped": [3]})
    assert dev(c, PC)["transfer"] is None and job_status(j) == "done"


def test_dont_send_loses_the_race(c):
    beat(c, PC)
    j = send(c, [1, 2], PC["id"])
    beat(c, PC)
    c.post("/api/agents/%s/dont-send" % PC["id"], json={"ids": [2]})
    beat(c, PC, [rep(j, arrived=[1, 2])])                   # it was there before the device heard
    t = dev(c, PC)["transfer"]
    assert (t["photos_done"], t["photos_total"]) == (2, 2)


def test_dont_send_what_a_stop_left_over(c):
    beat(c, PC)
    j = send(c, [1, 2, 3], PC["id"])
    beat(c, PC)
    c.post("/api/agents/%s/stop" % PC["id"])
    c.post("/api/jobs/%d/done" % j, json={"status": "stopped", "arrived": [1]})
    c.post("/api/agents/%s/dont-send" % PC["id"], json={"ids": [2]})
    assert [u["id"] for u in dev(c, PC)["stopped"]["unsent"]] == [3]
    c.post("/api/agents/%s/dont-send" % PC["id"], json={"ids": [3]})
    assert dev(c, PC)["stopped"] is None                    # nothing left to resume


def test_a_device_that_cannot_cancel(c):
    beat(c, PC, caps=["batch"])
    j = send(c, [1, 2], PC["id"])
    a = beat(c, PC, caps=["batch"])
    assert a["jobs"][0]["payload"] == {"ids": [1, 2]} and "cancel" not in a
    t = dev(c, PC)["transfer"]
    assert t["state"] == "running" and not t["can_stop"] and not t["can_skip"]
    assert c.post("/api/agents/%s/stop" % PC["id"]).status_code == 409
    assert c.post("/api/agents/%s/dont-send" % PC["id"], json={"ids": [2]}).status_code == 409
    assert dev(c, PC)["transfer"]["photos_total"] == 2


# ---------------------------------------------------------------- resume
def test_resume_sends_the_rest_as_a_new_job(c):
    beat(c, PC)
    j = send(c, [1, 2, 3], PC["id"])
    beat(c, PC)
    c.post("/api/agents/%s/stop" % PC["id"])
    c.post("/api/jobs/%d/done" % j, json={"status": "stopped", "arrived": [1]})
    r = c.post("/api/agents/%s/resume" % PC["id"]).json()
    assert r["photos"] == 2 and r["id"] not in (None, j)   # a new job id: a device may refuse to run an id twice
    d = dev(c, PC)
    assert d["stopped"] is None
    assert (d["transfer"]["photos_done"], d["transfer"]["photos_total"]) == (1, 3)   # carries on from 1 of 3
    a = beat(c, PC)
    assert a["jobs"] == [{"id": r["id"], "kind": "edit", "payload": {"ids": [2, 3], "open": True}}]
    c.post("/api/jobs/%d/done" % r["id"], json={"status": "done", "arrived": [2, 3]})
    d = dev(c, PC)
    assert d["transfer"] is None and d["stopped"] is None
    assert c.post("/api/agents/%s/resume" % PC["id"]).json() == {"ok": True, "id": None, "photos": 0}


def test_dismiss_keeps_what_arrived_and_forgets_the_rest(c):
    beat(c, PC)
    j = send(c, [1, 2], PC["id"])
    beat(c, PC)
    c.post("/api/agents/%s/stop" % PC["id"])
    c.post("/api/jobs/%d/done" % j, json={"status": "stopped", "arrived": [1]})
    assert c.post("/api/agents/%s/dismiss" % PC["id"]).json() == {"ok": True}
    assert dev(c, PC)["stopped"] is None
    assert c.post("/api/agents/%s/resume" % PC["id"]).json()["photos"] == 0


# ---------------------------------------------------------------- a device that restarts in the middle
def test_stopped_after_the_device_restarted(c):
    beat(c, PC)
    j = send(c, [1, 2, 3], PC["id"])
    beat(c, PC)
    beat(c, PC, [rep(j, arrived=[1], photo=2, bytes=10)])
    # the agent was restarted: it knows of no job; photo 2 had arrived just before, as its working set shows
    ws = [{"id": 1, "filename": "IMG_0001.jpg", "status": "ready"}, {"id": 2, "filename": "IMG_0002.jpg", "status": "ready"}]
    a = beat(c, PC, [], ws=ws)
    assert a["jobs"] == [] and a["cancel"]["jobs"] == []
    d = dev(c, PC)
    assert d["transfer"] is None                            # not "still running"
    s = d["stopped"]
    assert (s["photos_done"], s["photos_total"], s["reason"]) == (2, 3, "interrupted")
    assert [u["id"] for u in s["unsent"]] == [3] and job_status(j) == "stopped"
    assert c.post("/api/agents/%s/resume" % PC["id"]).json()["photos"] == 1


def test_a_job_just_handed_out_is_not_taken_for_lost(c):
    beat(c, PC)
    j = send(c, [1], PC["id"])
    assert [x["id"] for x in beat(c, PC, [])["jobs"]] == [j]   # this very answer carries it
    assert dev(c, PC)["transfer"]["state"] == "running"
    beat(c, PC, [rep(j, "queued")])                             # the worker is busy: it waits on the device
    assert dev(c, PC)["transfer"]["state"] == "running"


def test_not_handed_out_again_while_the_device_works_on_it(c, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    beat(c, PC)
    j = send(c, [1, 2], PC["id"])
    beat(c, PC)
    for _ in range(3):  # a long batch: the job's clock is wound up by every report
        with db.write_lock:
            db.conn().execute("UPDATE jobs SET taken=taken-500")
            db.conn().commit()
        assert beat(c, PC, [rep(j, photo=1, bytes=1)])["jobs"] == []
    # asleep for an hour, then back with the job still in hand: not given the same job a second time
    with db.write_lock:
        db.conn().execute("UPDATE jobs SET taken=taken-3600")
        db.conn().commit()
    assert beat(c, PC, [rep(j, photo=1, bytes=2)])["jobs"] == []
    assert dev(c, PC)["transfer"]["state"] == "running"


# ---------------------------------------------------------------- devices without capabilities: as before
def test_old_agent_exchange_is_unchanged(c, monkeypatch):
    """Today's exchange, call for call: heartbeat, edit job, working set with step and progress, done, sync job,
    hand-out again after JOB_RETRY_SECONDS. Nothing new in what the device receives."""
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    for agent in (PHONE, OLDPC):
        a = old_beat(c, agent)
        assert set(a) == {"jobs", "server_time"} and a["jobs"] == []
        j = send(c, [1, 2], agent["id"])
        a = old_beat(c, agent)
        assert set(a) == {"jobs", "server_time"}
        assert a["jobs"] == [{"id": j, "kind": "edit", "payload": {"ids": [1, 2]}}]
        d = dev(c, agent)
        assert d["transfer"] is None and d["stopped"] is None and d["caps"] == []   # no totals are made up
        ws = [{"id": 1, "filename": "IMG_0001.jpg", "status": "downloading", "checkout_ts": 1.0, "synced_ts": 0, "error": "",
               "step": "downloading", "progress": 0.4}]
        assert old_beat(c, agent, ws)["jobs"] == []
        assert dev(c, agent)["state"]["working_set"] == ws
        # Stop is not there for it: asking anyway changes nothing and tells the device nothing
        assert c.post("/api/agents/%s/stop" % agent["id"]).json()["stopped"] == 0
        assert set(old_beat(c, agent, ws)) == {"jobs", "server_time"}
        # handed out again when never reported done
        with db.write_lock:
            db.conn().execute("UPDATE jobs SET taken=taken-601 WHERE id=?", (j,))
            db.conn().commit()
        assert old_beat(c, agent, ws)["jobs"] == [{"id": j, "kind": "edit", "payload": {"ids": [1, 2]}}]
        assert c.post("/api/jobs/%d/done" % j, json={"status": "done"}).json() == {"ok": True}
        assert job_status(j) == "done"
        s = c.post("/api/jobs", json={"kind": "sync", "payload": {"ids": [1]}, "agent_id": agent["id"]}).json()["id"]
        assert old_beat(c, agent)["jobs"] == [{"id": s, "kind": "sync", "payload": {"ids": [1]}}]
        assert c.post("/api/jobs/%d/done" % s, json={"status": "done"}).json() == {"ok": True}


def test_unaddressed_job_taken_by_an_old_pc(c):
    old_beat(c, OLDPC)
    beat(c, PC)
    j = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": [1]}}).json()   # two PCs online: nobody is chosen
    assert j["agent_id"] is None
    assert old_beat(c, PHONE)["jobs"] == []                 # never a phone
    assert old_beat(c, OLDPC)["jobs"] == [{"id": j["id"], "kind": "edit", "payload": {"ids": [1]}}]
    assert dev(c, OLDPC)["transfer"] is None and dev(c, PC)["transfer"] is None


def test_unaddressed_job_taken_by_a_new_pc_joins_its_run(c):
    beat(c, PC)
    old_beat(c, OLDPC)
    j1 = send(c, [1], PC["id"])
    beat(c, PC)
    j2 = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": [2]}}).json()["id"]
    assert [x["id"] for x in beat(c, PC, [rep(j1, photo=1, bytes=1)])["jobs"]] == [j2]
    t = dev(c, PC)["transfer"]
    assert t["photos_total"] == 2 and [w["id"] for w in t["waiting"]] == [2]


def test_open_setting_goes_with_the_job_only_to_devices_that_know_it(c):
    c.put("/api/prefs/settings", json={"value": {"devicesAutoOpen": False, "boardSnap": True}})
    beat(c, PC)
    old_beat(c, OLDPC)
    send(c, [1], PC["id"])
    send(c, [2], OLDPC["id"])
    assert beat(c, PC)["jobs"][0]["payload"] == {"ids": [1], "open": False}
    assert old_beat(c, OLDPC)["jobs"][0]["payload"] == {"ids": [2]}
    send(c, [3], PC["id"])
    assert beat(c, PC, caps=["batch", "cancel"])["jobs"][0]["payload"] == {"ids": [3]}   # says batch but not open


def test_sync_and_release_jobs_are_not_batches(c):
    beat(c, PC)
    s = c.post("/api/jobs", json={"kind": "sync", "payload": {"ids": [1]}, "agent_id": PC["id"]}).json()["id"]
    r = c.post("/api/jobs", json={"kind": "release", "payload": {"all": True}, "agent_id": PC["id"]}).json()["id"]
    assert dev(c, PC)["transfer"] is None
    a = beat(c, PC)
    assert a["jobs"] == [{"id": s, "kind": "sync", "payload": {"ids": [1]}}, {"id": r, "kind": "release", "payload": {"all": True}}]
    ws = [{"id": 1, "filename": "IMG_0001.jpg", "status": "syncing", "step": "sending edit"}]
    beat(c, PC, ws=ws)
    assert dev(c, PC)["state"]["working_set"] == ws         # their progress is shown as it always was
    for j in (s, r):
        assert c.post("/api/jobs/%d/done" % j, json={"status": "done"}).json() == {"ok": True}
    assert db.conn().execute("SELECT COUNT(*) n FROM transfers").fetchone()["n"] == 0


def test_cancel_a_job_nobody_took(c):
    old_beat(c, OLDPC)
    j = c.post("/api/jobs", json={"kind": "sync", "payload": {"all": True}, "agent_id": OLDPC["id"]}).json()["id"]
    assert c.post("/api/jobs/%d/cancel" % j).json() == {"ok": True}
    assert old_beat(c, OLDPC)["jobs"] == [] and job_status(j) == "cancelled"
    e = send(c, [1], OLDPC["id"])
    old_beat(c, OLDPC)
    assert c.post("/api/jobs/%d/cancel" % e).status_code == 409    # taken: only the device can stop it now
    assert c.post("/api/jobs/999999/cancel").status_code == 404


def test_bad_reports_do_no_harm(c):
    beat(c, PC)
    old_beat(c, OLDPC)
    j = send(c, [1, 2], PC["id"])
    other = send(c, [3], OLDPC["id"])
    beat(c, PC)
    junk = [None, 5, {"job": "x"}, {"job": other, "state": "stopped", "arrived": [3]},   # another device's job
            {"job": j, "state": "running", "arrived": ["1", True, None, 77], "photo": "2", "bytes": float("nan") if False else -5}]
    r = c.post("/api/agents/heartbeat", json={**PC, "state": "not an object", "caps": ["BATCH", 7, "cancel"], "transfers": junk})
    assert r.status_code == 200
    t = dev(c, PC)["transfer"]
    assert (t["photos_done"], t["photos_total"], t["current"]) == (1, 2, None)
    assert dev(c, OLDPC)["transfer"]["state"] == "queued" and job_status(other) == "queued"
    assert dev(c, PC)["caps"] == ["batch", "cancel"]


# ---------------------------------------------------------------- fold choices
def test_fold_choices_are_kept_per_device(c):
    beat(c, PC)
    old_beat(c, OLDPC)
    assert dev(c, PC)["folded"] is None
    assert c.put("/api/devices/fold", json={"folds": {PC["id"]: True}}).json() == {"folds": {PC["id"]: True}}
    c.put("/api/devices/fold", json={"folds": {OLDPC["id"]: False}})          # another browser: merged, not replaced
    assert (dev(c, PC)["folded"], dev(c, OLDPC)["folded"]) == (True, False)
    c.put("/api/devices/fold", json={"folds": {PC["id"]: None, "phone:" + PC["id"]: False}})
    assert dev(c, PC)["folded"] is None
    assert (dev(c, PC)["folded_phone"], dev(c, OLDPC)["folded_phone"]) == (False, None)   # the phone layout's own choice
    assert c.put("/api/devices/fold", json={"folds": []}).status_code == 400
    assert c.get("/api/status").json()["agents"][0]["folded"] in (None, False)


# ---------------------------------------------------------------- migration
def test_migrating_a_library_from_before(tmp_path):
    """A library as the last version left it, with devices and jobs: rows untouched, new tables and agents.caps
    added, twice without harm; old jobs have no batch and are still handed out."""
    p = tmp_path / "lpl.sqlite"
    con = sqlite3.connect(str(p))
    con.executescript((HERE / "fixtures" / "schema-before-devices-7572235.sql").read_text())
    con.execute("INSERT INTO agents VALUES('pc-1','Desktop','windows',1.7e9,?)", (json.dumps({"working_set": [{"id": 5, "status": "dirty"}]}),))
    con.execute("INSERT INTO agents VALUES('android-1','Telefono','android',1.7e9,'{}')")
    con.execute("INSERT INTO jobs(agent_id,kind,payload,created,taken,status,attempts) VALUES('pc-1','edit','{\"ids\":[5]}',1.0,2.0,'taken',1)")
    con.execute("INSERT INTO jobs(agent_id,kind,payload,created,status) VALUES('android-1','edit','{\"ids\":[6,7]}',1.0,'queued')")
    con.execute("INSERT INTO settings VALUES('pref.settings','{\"boardSnap\":true}')")
    con.commit()
    before = {t: con.execute("SELECT * FROM %s ORDER BY rowid" % t).fetchall() for t in ("agents", "jobs", "settings")}
    con.row_factory = sqlite3.Row
    db.migrate(con)
    db.migrate(con)
    con.row_factory = None
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {"transfers", "transfer_items", "idx_transfers_agent"} <= names
    assert [r[1] for r in con.execute("PRAGMA table_info(agents)")][:6] == ["id", "name", "os", "last_seen", "state", "caps"]
    assert [r[:5] for r in con.execute("SELECT * FROM agents ORDER BY rowid")] == before["agents"]
    assert [r[5] for r in con.execute("SELECT * FROM agents")] == [None, None]
    assert [r[:len(b)] for r, b in zip(con.execute("SELECT * FROM jobs ORDER BY rowid").fetchall(), before["jobs"])] == before["jobs"]
    assert con.execute("SELECT * FROM settings ORDER BY rowid").fetchall() == before["settings"]
    assert con.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] == 0
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_jobs_from_before_the_update_still_work(c):
    """An edit job queued before the update has no batch: it is handed out and finished as always, to old and new devices."""
    for agent, hb in ((OLDPC, old_beat), (PC, beat)):
        hb(c, agent)
        with db.write_lock:
            cur = db.conn().execute("INSERT INTO jobs(agent_id,kind,payload,created) VALUES(?,?,?,?)", (agent["id"], "edit", '{"ids": [1]}', time.time()))
            db.conn().commit()
        j = cur.lastrowid
        assert hb(c, agent)["jobs"] == [{"id": j, "kind": "edit", "payload": {"ids": [1]}}]
        assert dev(c, agent)["transfer"] is None
        if agent is PC:  # a new agent reports it; LPL has no batch for it and is not confused
            beat(c, PC, [rep(j, arrived=[1])])
        assert c.post("/api/jobs/%d/done" % j, json={"status": "done", "arrived": [1]}).json() == {"ok": True}
        assert job_status(j) == "done"


def test_online_limit_is_the_one_the_api_uses():
    assert api.AGENT_ONLINE_SECONDS == 15
