# SPDX-License-Identifier: GPL-3.0-or-later
"""Devices: names and renames (lpl/identity.py), one computer that came back under a new id, and photos a device
could not copy (Retry, "Clear from queue"; lpl/transfers.py)."""
import json
import time

import pytest

from lpl import db, identity, transfers

CAPS = ["batch", "cancel", "open"]


@pytest.fixture()
def c(clean):
    with db.write_lock:
        for t in ("transfers", "transfer_items", "edit_events"):
            db.conn().execute("DELETE FROM %s" % t)
        db.conn().execute("DELETE FROM settings WHERE key LIKE 'pref.%'")
        db.conn().commit()
    transfers._samples.clear()
    return clean


def beat(c, aid, name, machine="", reports=None, ws=()):
    body = {"id": aid, "name": name, "os": "linux", "state": {"working_set": list(ws)}, "caps": CAPS, "transfers": reports or []}
    if machine:
        body["machine"] = machine
    r = c.post("/api/agents/heartbeat", json=body)
    assert r.status_code == 200
    return r.json()


def agents(c):
    return {a["id"]: a for a in c.get("/api/agents").json()["agents"]}


def age(aid, seconds=3600):
    with db.write_lock:
        db.conn().execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time() - seconds, aid))
        db.conn().commit()


# ---------------------------------------------------------------- names
def test_a_name_changed_on_the_device_shows(c):
    beat(c, "pc-1", "fedora")
    beat(c, "pc-1", "Laptop")
    assert agents(c)["pc-1"]["name"] == "Laptop"


def test_rename_in_lpl_is_told_to_the_device_until_it_takes_it(c):
    beat(c, "pc-1", "fedora")
    assert c.put("/api/agents/pc-1/name", json={"name": "  Laptop "}).json()["name"] == "Laptop"
    a = agents(c)["pc-1"]
    assert (a["name"], a["renaming"]) == ("Laptop", "Laptop")
    r = beat(c, "pc-1", "fedora")                 # the device has not taken it yet: LPL keeps its name
    assert r["rename"] == "Laptop" and agents(c)["pc-1"]["name"] == "Laptop"
    r = beat(c, "pc-1", "Laptop")                 # taken
    assert "rename" not in r and agents(c)["pc-1"]["renaming"] is None
    beat(c, "pc-1", "Portatile")                  # from then on a change on the device wins again
    assert agents(c)["pc-1"]["name"] == "Portatile"


def test_rename_refused(c):
    assert c.put("/api/agents/nope/name", json={"name": "x"}).status_code == 404
    beat(c, "pc-1", "fedora")
    assert c.put("/api/agents/pc-1/name", json={"name": "   "}).status_code == 400


def test_rename_updates_the_edit_log(c):
    beat(c, "pc-1", "fedora")
    with db.write_lock:
        db.conn().execute("INSERT INTO edit_events(photo_id,ts,source,agent_id,device,modules) VALUES(1,1,'agent','pc-1','fedora','[]')")
        db.conn().commit()
    c.put("/api/agents/pc-1/name", json={"name": "Laptop"})
    assert db.conn().execute("SELECT device FROM edit_events WHERE agent_id='pc-1'").fetchone()[0] == "Laptop"


# ---------------------------------------------------------------- one computer, two ids
def test_reinstalled_agent_is_merged_into_the_old_entry(c):
    beat(c, "old-id", "fedora", machine="m1")
    jid = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": [1]}, "agent_id": "old-id"}).json()["id"]
    c.put("/api/devices/fold", json={"folds": {"old-id": True}})
    age("old-id")
    beat(c, "new-id", "Laptop", machine="m1")     # installed again: new id, same computer
    a = agents(c)
    assert "old-id" not in a and a["new-id"]["name"] == "Laptop"
    assert a["new-id"]["merged_from"] == ["fedora"] and a["new-id"]["folded"] is True
    assert db.conn().execute("SELECT agent_id FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "new-id"
    assert db.conn().execute("SELECT agent_id FROM transfers WHERE job_id=?", (jid,)).fetchone()[0] == "new-id"


def test_no_merge_while_the_other_still_reports(c):
    beat(c, "a", "One", machine="m1")
    beat(c, "b", "Two", machine="m1")             # two agents running on one computer: both stay
    a = agents(c)
    assert set(a) == {"a", "b"} and a["a"]["same_machine"] == ["b"]


def test_no_auto_merge_when_switched_off_then_by_hand(c):
    with db.write_lock:
        db.set_setting("pref.settings", json.dumps({"devicesAutoMerge": False}))
    beat(c, "old-id", "fedora", machine="m1")
    age("old-id")
    beat(c, "new-id", "Laptop", machine="m1")
    a = agents(c)
    assert set(a) == {"old-id", "new-id"} and a["old-id"]["same_machine"] == ["new-id"]
    assert c.post("/api/agents/new-id/merge", json={"into": "old-id"}).status_code == 409   # new-id is online
    assert c.post("/api/agents/old-id/merge", json={"into": "old-id"}).status_code == 400
    assert c.post("/api/agents/old-id/merge", json={"into": "new-id"}).json()["ok"]
    assert set(agents(c)) == {"new-id"}


def test_agents_without_machine_are_never_merged(c):
    beat(c, "old-id", "fedora")
    age("old-id")
    beat(c, "new-id", "fedora")
    assert set(agents(c)) == {"old-id", "new-id"}


# ---------------------------------------------------------------- photos that could not be copied
def failed_batch(c):
    beat(c, "pc-1", "Laptop")
    j = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": [1, 2, 3]}, "agent_id": "pc-1"}).json()["id"]
    beat(c, "pc-1", "Laptop")
    ws = [{"id": 2, "filename": "b", "status": "error", "error": "timed out", "copy_failed": True}]
    beat(c, "pc-1", "Laptop", reports=[{"job": j, "state": "done", "arrived": [1], "failed": [2, 3]}], ws=ws)
    return j, ws


def test_failed_photos_are_listed_after_the_batch_ended(c):
    j, _ = failed_batch(c)
    a = agents(c)["pc-1"]
    assert a["transfer"] is None
    assert sorted(f["id"] for f in a["failed"]) == [2, 3] and all(f["job"] == j for f in a["failed"])


def test_clear_from_queue(c):
    _, ws = failed_batch(c)
    r = c.post("/api/agents/pc-1/clear", json={"ids": [2, 3]}).json()
    assert (r["cleared"], r["on_device"]) == (2, 1)      # photo 2 left a failed copy on the PC: it deletes it
    assert agents(c)["pc-1"]["failed"] == []
    job = db.conn().execute("SELECT kind, payload FROM jobs WHERE id=?", (r["id"],)).fetchone()
    assert job[0] == "forget" and json.loads(job[1]) == {"ids": [2], "mode": "failed"}
    out = beat(c, "pc-1", "Laptop", ws=ws)["jobs"]
    assert [x["kind"] for x in out] == ["forget"]


def test_clear_never_touches_a_copy_with_edits(c):
    failed_batch(c)
    ws = [{"id": 2, "filename": "b", "status": "error", "error": "sync failed: timed out"}]
    beat(c, "pc-1", "Laptop", ws=ws)
    r = c.post("/api/agents/pc-1/clear", json={"ids": [2]}).json()
    assert r["on_device"] == 0 and r["id"] is None


def test_retry_sends_them_again(c):
    failed_batch(c)
    r = c.post("/api/agents/pc-1/retry", json={"ids": [2, 3]}).json()
    assert r["photos"] == 2
    a = agents(c)["pc-1"]
    assert a["failed"] == [] and a["transfer"]["photos_total"] == 2
    assert c.post("/api/agents/pc-1/retry", json={"ids": []}).status_code == 400
    assert c.post("/api/agents/nope/retry", json={"ids": [1]}).status_code == 404


def test_merge_keeps_version_checkouts(c):
    beat(c, "old-id", "fedora", machine="m1")
    with db.write_lock:
        db.conn().execute("INSERT INTO version_checkouts(agent_id,photo_id,ts,ns) VALUES('old-id',1,1,'[0]')")
        db.conn().commit()
    age("old-id")
    beat(c, "new-id", "Laptop", machine="m1")
    assert db.conn().execute("SELECT agent_id FROM version_checkouts WHERE photo_id=1").fetchall()[0][0] == "new-id"


def test_clean_machine():
    assert identity.clean_machine("ab-12_ZZ!/x") == "ab-12_ZZx" and identity.clean_machine(5) == ""
