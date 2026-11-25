# SPDX-License-Identifier: GPL-3.0-or-later
"""R13 (a job never reported done is handed out again), R14 (checking the x-lpl-token header alone) and
R16 (step and progress of a working-set item). See docs/android-edit-record-progress.md."""
import time

import pytest

from lpl import api, db
from lpl.config import config

PC = {"id": "pc-test", "name": "Desktop", "os": "windows"}
PC2 = {"id": "pc-other", "name": "Laptop", "os": "linux"}
PHONE = {"id": "android-0000-test", "name": "Phone", "os": "android"}


def beat(c, agent, ws=()):
    r = c.post("/api/agents/heartbeat", json={**agent, "state": {"working_set": list(ws)}})
    assert r.status_code == 200
    return [j["id"] for j in r.json()["jobs"]]


def job(c, agent_id=None):
    body = {"kind": "edit", "payload": {"ids": [1]}}
    if agent_id:
        body["agent_id"] = agent_id
    return c.post("/api/jobs", json=body).json()["id"]


def age_jobs(seconds: float):
    with db.write_lock:
        db.conn().execute("UPDATE jobs SET taken=taken-?", (seconds,))
        db.conn().commit()


# ---------------------------------------------------------------- R13
def test_taken_job_handed_out_again_after_a_while(clean, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    c = clean
    j = job(c, PHONE["id"])
    assert beat(c, PHONE) == [j]
    assert beat(c, PHONE) == []          # not yet
    age_jobs(599)
    assert beat(c, PHONE) == []          # still not
    age_jobs(2)
    assert beat(c, PC) == []             # never to another device
    assert beat(c, PHONE) == [j]         # the app died with it: handed out again
    assert beat(c, PHONE) == []
    row = db.conn().execute("SELECT status, attempts FROM jobs WHERE id=?", (j,)).fetchone()
    assert row["status"] == "taken" and row["attempts"] == 2


def test_done_job_never_handed_out_again(clean, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    c = clean
    j = job(c, PC["id"])
    assert beat(c, PC) == [j]
    assert c.post("/api/jobs/%d/done" % j, json={"status": "done"}).status_code == 200
    age_jobs(10000)
    assert beat(c, PC) == []
    j2 = job(c, PC["id"])
    assert beat(c, PC) == [j2]
    c.post("/api/jobs/%d/done" % j2, json={"status": "failed"})
    age_jobs(10000)
    assert beat(c, PC) == []


def test_unaddressed_job_comes_back_to_the_pc_that_took_it(clean, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    c = clean
    j = job(c)
    assert beat(c, PC) == [j]
    age_jobs(601)
    assert beat(c, PC2) == [] and beat(c, PHONE) == []
    assert beat(c, PC) == [j]


def test_hand_out_limit(clean, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    c = clean
    j = job(c, PHONE["id"])
    got = 0
    for _ in range(6):
        got += beat(c, PHONE).count(j)
        age_jobs(601)
    assert got == api.JOB_MAX_HANDOUTS


def test_retry_off(clean, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 0)
    c = clean
    j = job(c, PHONE["id"])
    assert beat(c, PHONE) == [j]
    age_jobs(10 ** 7)
    assert beat(c, PHONE) == []


def test_jobs_taken_before_the_update_stay_as_they_were(clean, monkeypatch):
    """A job taken by an LPL that did not count hand-outs (attempts 0) may be weeks old: never handed out again."""
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    c = clean
    with db.write_lock:
        db.conn().execute("INSERT INTO jobs(agent_id,kind,payload,created,taken,status,attempts) VALUES(?,?,?,?,?,?,0)",
                          (PC["id"], "edit", '{"ids": [1]}', time.time() - 10 ** 6, time.time() - 10 ** 6, "taken"))
        db.conn().commit()
    assert beat(c, PC) == []


def test_retry_default_is_a_setting():
    assert config.job_retry_seconds == 1800  # JOB_RETRY_SECONDS, see config.py for the reason


# ---------------------------------------------------------------- R14
@pytest.fixture()
def password(client, monkeypatch):
    monkeypatch.setattr(config, "password", "s3cret-test")
    return client


def test_token_check(password):
    c = password
    assert c.get("/api/auth/token", headers={"x-lpl-token": "s3cret-test"}).json() == {"required": True, "ok": True}
    r = c.get("/api/auth/token", headers={"x-lpl-token": "wrong"})
    assert r.status_code == 401 and r.json() == {"detail": "login required"}
    assert c.get("/api/auth/token").status_code == 401
    # the login cookie does not count: only the header is checked
    c.cookies.set("lpl_token", api._token())
    try:
        assert c.get("/api/auth/token").status_code == 401
        assert c.get("/api/auth/token", headers={"x-lpl-token": api._token()}).status_code == 200
    finally:
        c.cookies.clear()
    # the rest of the API is unchanged
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", headers={"x-lpl-token": "s3cret-test"}).status_code == 200
    assert c.get("/api/auth").json() == {"required": True, "ok": False}


def test_token_check_without_password(client):
    assert client.get("/api/auth/token").json() == {"required": False, "ok": True}
    assert client.get("/api/auth/token", headers={"x-lpl-token": "anything"}).status_code == 200


# ---------------------------------------------------------------- R16
def item(pid, **kw):
    return {"id": pid, "filename": "IMG_%04d.jpg" % pid, "status": "downloading", "checkout_ts": 1.0, "synced_ts": 0, "error": "", **kw}


def working_set(c, aid):
    a = next(a for a in c.get("/api/agents").json()["agents"] if a["id"] == aid)
    return {w["id"]: w for w in a["state"]["working_set"]}


def test_step_and_progress_kept(clean):
    c = clean
    beat(c, PHONE, [item(1, step="downloading", progress=0.42), item(2, status="syncing", step="sending edit"),
                    item(3, status="synced")])
    ws = working_set(c, PHONE["id"])
    assert ws[1]["step"] == "downloading" and ws[1]["progress"] == 0.42
    assert ws[2]["step"] == "sending edit" and "progress" not in ws[2]
    assert "step" not in ws[3] and "progress" not in ws[3]
    # the step ended: the next heartbeat has no step, and none is kept
    beat(c, PHONE, [item(1, status="opening")])
    ws = working_set(c, PHONE["id"])
    assert "step" not in ws[1] and "progress" not in ws[1]


def test_bad_progress_cleaned(clean):
    c = clean
    beat(c, PHONE, [item(1, step="sending preview", progress=1.7), item(2, step="downloading", progress="half"),
                    item(3, step="", progress=-1), item(4, step="rendering preview", progress=True)])
    ws = working_set(c, PHONE["id"])
    assert ws[1]["progress"] == 1.0
    assert "progress" not in ws[2] and ws[2]["step"] == "downloading"
    assert "step" not in ws[3] and ws[3]["progress"] == 0.0
    assert "progress" not in ws[4]


def test_old_agent_heartbeat_unchanged(clean):
    c = clean
    old = {"id": 1, "filename": "IMG_0001.jpg", "status": "dirty", "checkout_ts": 5.0, "synced_ts": 0, "error": ""}
    beat(c, PC, [old])
    assert working_set(c, PC["id"])[1] == old


def test_progress_dropped_when_the_device_stops_reporting(clean):
    c = clean
    beat(c, PHONE, [item(1, step="downloading", progress=0.5)])
    assert working_set(c, PHONE["id"])[1]["progress"] == 0.5
    with db.write_lock:  # last heartbeat 20 s ago: offline
        db.conn().execute("UPDATE agents SET last_seen=last_seen-20 WHERE id=?", (PHONE["id"],))
        db.conn().commit()
    ws = working_set(c, PHONE["id"])
    assert "step" not in ws[1] and "progress" not in ws[1] and ws[1]["status"] == "downloading"
    st = next(a for a in c.get("/api/status").json()["agents"] if a["id"] == PHONE["id"])
    assert not st["online"] and "step" not in st["state"]["working_set"][0]


# ---------------------------------------------------------------- R13/R14 follow-up, checkout sha256
def test_late_done_from_first_taker_is_accepted_once(clean, monkeypatch):
    monkeypatch.setattr(config, "job_retry_seconds", 600)
    c = clean
    j = job(c, PHONE["id"])
    assert beat(c, PHONE) == [j]
    age_jobs(700)
    assert beat(c, PHONE) == [j]                      # handed out again
    assert c.post("/api/jobs/%d/done" % j, json={"status": "done"}).status_code == 200
    assert c.post("/api/jobs/%d/done" % j, json={"status": "done"}).status_code == 200  # a repeat changes nothing
    age_jobs(5000)
    assert beat(c, PHONE) == []                       # done: never again
    assert db.conn().execute("SELECT status FROM jobs WHERE id=?", (j,)).fetchone()["status"] == "done"


def test_token_check_alias(password):
    c = password
    assert c.get("/api/auth/check", headers={"x-lpl-token": "s3cret-test"}).json() == {"required": True, "ok": True}
    assert c.get("/api/auth/check").status_code == 401
    assert c.get("/api/auth/check", headers={"x-lpl-token": "wrong"}).status_code == 401


def test_checkout_has_sha256_and_size_cached(client, monkeypatch):
    import hashlib
    from lpl import scanner
    pid = db.conn().execute("SELECT id FROM photos ORDER BY id LIMIT 1").fetchone()["id"]
    r = client.get("/api/photos/%d/checkout" % pid).json()
    p = scanner.abs_of(r["relpath"])
    assert r["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest() and r["size"] == p.stat().st_size
    calls = []
    real = hashlib.sha256
    monkeypatch.setattr(api.hashlib, "sha256", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert client.get("/api/photos/%d/checkout" % pid).json()["sha256"] == r["sha256"]
    assert calls == []                                 # cached: not hashed again
    data = p.read_bytes()
    st = p.stat()
    p.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))   # same size, new mtime
    import os
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    try:
        r2 = client.get("/api/photos/%d/checkout" % pid).json()
        assert r2["sha256"] != r["sha256"] and calls
    finally:
        p.write_bytes(data)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
