# SPDX-License-Identifier: GPL-3.0-or-later
"""Hand-off to darktable with a PC agent and an Android agent (the darktable phone app) side by side,
and resumable downloads of originals. See docs/android-handoff.md."""
from lpl import scanner

PC = {"id": "pc-test", "name": "Desktop", "os": "windows"}
PHONE = {"id": "android-0000-test", "name": "Phone", "os": "android"}


def beat(c, agent):
    r = c.post("/api/agents/heartbeat", json={**agent, "state": {"working_set": []}})
    assert r.status_code == 200
    return [j["id"] for j in r.json()["jobs"]]


def job(c, agent_id=None):
    body = {"kind": "edit", "payload": {"ids": [1]}}
    if agent_id:
        body["agent_id"] = agent_id
    r = c.post("/api/jobs", json=body)
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------- job routing (R3)
def test_unaddressed_job_goes_to_pc_never_to_phone(clean):
    c = clean
    beat(c, PHONE)                      # only the phone is online: the job stays unaddressed
    j = job(c)
    assert j["agent_id"] is None
    assert beat(c, PHONE) == []         # the phone never takes it
    assert beat(c, PC) == [j["id"]]     # the PC does
    assert beat(c, PC) == []            # once


def test_unaddressed_job_waits_for_a_pc(clean):
    c = clean
    j = job(c)                          # nobody online
    assert j["agent_id"] is None
    assert beat(c, PHONE) == []
    assert beat(c, PHONE) == []
    assert beat(c, PC) == [j["id"]]


def test_job_addressed_to_phone_goes_only_to_phone(clean):
    c = clean
    beat(c, PC)
    j = job(c, PHONE["id"])             # the phone is offline (never seen): the job waits for it
    assert j["agent_id"] == PHONE["id"]
    assert beat(c, PC) == []
    assert beat(c, PHONE) == [j["id"]]


def test_job_addressed_to_pc_goes_only_to_pc(clean):
    c = clean
    j = job(c, PC["id"])
    assert beat(c, PHONE) == []
    assert beat(c, PC) == [j["id"]]


def test_automatic_choice_ignores_android(clean):
    c = clean
    beat(c, PC)
    beat(c, PHONE)
    j = job(c)                          # PC and phone online: the PC is the only candidate
    assert j["agent_id"] == PC["id"]
    assert beat(c, PHONE) == []
    assert beat(c, PC) == [j["id"]]


def test_automatic_choice_with_two_pcs_is_unchanged(clean):
    c = clean
    beat(c, PC)
    beat(c, {"id": "pc-test-2", "name": "Laptop", "os": "linux"})
    beat(c, PHONE)
    j = job(c)                          # two PCs: no choice made, as before; the first PC to ask takes it
    assert j["agent_id"] is None
    assert beat(c, PHONE) == []
    assert beat(c, PC) == [j["id"]]


# ---------------------------------------------------------------- resumable download (R4)
def original(c):
    pid = c.post("/api/photos/ids", json={"filters": {}, "sort": [], "seed": 1}).json()["ids"][0]
    rel = c.get("/api/photos/%d" % pid).json()["relpath"]
    return pid, scanner.abs_of(rel).read_bytes()


def test_original_without_range(client):
    pid, data = original(client)
    r = client.get("/api/photos/%d/original" % pid)
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == data
    assert len(data) > 1000


def test_original_first_bytes(client):
    pid, data = original(client)
    r = client.get("/api/photos/%d/original" % pid, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 0-99/%d" % len(data)
    assert r.headers["content-length"] == "100"
    assert r.content == data[:100]


def test_original_middle_to_end(client):
    """What a resumed download sends: everything from the byte it already has."""
    pid, data = original(client)
    n = len(data)
    r = client.get("/api/photos/%d/original" % pid, headers={"Range": "bytes=500-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 500-%d/%d" % (n - 1, n)
    assert r.content == data[500:]


def test_original_middle(client):
    pid, data = original(client)
    r = client.get("/api/photos/%d/original" % pid, headers={"Range": "bytes=200-299"})
    assert r.status_code == 206
    assert r.content == data[200:300]


def test_original_suffix(client):
    pid, data = original(client)
    n = len(data)
    r = client.get("/api/photos/%d/original" % pid, headers={"Range": "bytes=-50"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes %d-%d/%d" % (n - 50, n - 1, n)
    assert r.content == data[-50:]


def test_original_end_past_the_file_is_clamped(client):
    pid, data = original(client)
    n = len(data)
    r = client.get("/api/photos/%d/original" % pid, headers={"Range": "bytes=10-%d" % (n + 5000)})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 10-%d/%d" % (n - 1, n)
    assert r.content == data[10:]


def test_original_beyond_the_end(client):
    pid, data = original(client)
    n = len(data)
    r = client.get("/api/photos/%d/original" % pid, headers={"Range": "bytes=%d-" % n})
    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */%d" % n
