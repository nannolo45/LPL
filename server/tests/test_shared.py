# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared darktable styles, presets and module layouts (R11, docs/shared-r11.md; contract PLAN.md 4.5 of darktable-android):
hash vectors, first sync, one-side change, stale base (409 + candidate), each resolve choice, newest wins at PUT, deletions
with delete / keep, a kept item changed again comes back, waiting per device, the shared-update job, caps."""
import copy
import json
from pathlib import Path

import pytest

from lpl import db, shared

VECTORS = json.loads((Path(__file__).resolve().parent / "fixtures" / "r11-hash-vectors.json").read_text(encoding="utf-8"))
PC = {"id": "pc-shared-a", "name": "Desktop", "os": "windows", "caps": ["shared"]}
PC2 = {"id": "pc-shared-b", "name": "Laptop", "os": "linux", "caps": ["shared"]}
PHONE = {"id": "android-shared", "name": "Phone", "os": "android", "caps": ["shared"]}


@pytest.fixture()
def c(clean):
    with db.write_lock:
        for t in ("shared_items", "shared_candidates", "shared_state", "shared_agents"):
            db.conn().execute("DELETE FROM %s" % t)
        db.conn().execute("DELETE FROM settings WHERE key='pref.settings'")
        db.conn().commit()
    for a in (PC, PC2, PHONE):
        assert clean.post("/api/agents/heartbeat", json=a).status_code == 200
    return clean


def preset(name="warm", params="0000803f", desc=""):
    b = copy.deepcopy(VECTORS["vectors"][0]["body"])
    b["row"].update(name=name, op_params=params, description=desc)
    return b


def put(c, agent, body, base_rev=0, changed_at=1000.0, **kw):
    row = body.get("row") or {}
    q = {"kind": body["kind"], "operation": row.get("operation", ""), "op_version": row.get("op_version", 0),
         "name": row.get("name") or body.get("style", {}).get("name"), "hash": shared.item_hash(body),
         "changed_at": changed_at, "base_rev": base_rev, "body": body}
    q.update(kw)
    return c.put("/api/shared?agent=%s" % agent["id"], json=q)


def waiting(c, agent):
    d = next(x for x in c.get("/api/shared/devices").json()["devices"] if x["id"] == agent["id"])
    return {(w["name"], w["what"]) for w in d["waiting"]}, d


def state(c, agent, items, policy=None):
    body = {"items": {str(k): v for k, v in items.items()}}
    if policy:
        body["policy"] = policy
    assert c.put("/api/shared/state?agent=%s" % agent["id"], json=body).status_code == 200


# ---------------------------------------------------------------- hash
@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["body"]["kind"])
def test_hash_vectors(v):
    assert shared.canonical(shared.core(v["body"])).decode("utf-8") == v["canonical"]
    assert shared.item_hash(v["body"]) == v["hash"]


def test_server_caps_lists_shared(c):
    r = c.post("/api/agents/heartbeat", json=PC).json()
    assert "shared" in r["server_caps"]
    old = c.post("/api/agents/heartbeat", json={"id": "pc-no-caps", "name": "Old"}).json()
    assert "server_caps" not in old


# ---------------------------------------------------------------- uploads
def test_first_sync_identical_items_on_two_devices_is_no_conflict(c):
    a = put(c, PC, preset()).json()
    assert a["rev"] == 1 and a["decided"] is None
    b = put(c, PC2, preset(), base_rev=0)
    assert b.status_code == 200 and b.json()["rev"] == 1 and b.json()["id"] == a["id"]
    assert waiting(c, PC)[0] == set() and waiting(c, PC2)[0] == set()
    assert waiting(c, PHONE)[0] == {("warm", "new")}
    lst = c.get("/api/shared?agent=%s&since=0" % PC2["id"]).json()
    assert lst["conflicts"] == [] and [i["name"] for i in lst["items"]] == ["warm"] and "body" not in lst["items"][0]
    assert c.get("/api/shared/%d" % a["id"]).json()["body"]["row"]["op_params"] == "0000803f"


def test_one_side_change_waits_on_the_others(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    seq0 = c.get("/api/shared?since=0").json()["seq"]
    r = put(c, PC, preset(params="cdcc4c3e"), base_rev=1, changed_at=2000).json()
    assert r["rev"] == 2 and r["seq"] > seq0
    assert waiting(c, PC2)[0] == {("warm", "changed")} and waiting(c, PC)[0] == set()
    since = c.get("/api/shared?since=%d" % seq0).json()
    assert [(i["name"], i["rev"], i["device_name"]) for i in since["items"]] == [("warm", 2, "Desktop")]
    assert c.get("/api/shared?since=%d" % since["seq"]).json()["items"] == []
    state(c, PC2, {a["id"]: {"rev": 2, "state": "ok"}})  # after its update
    assert waiting(c, PC2)[0] == set()


def test_stale_base_is_409_with_a_candidate_replaced_by_a_newer_put(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    put(c, PC, preset(params="11"), base_rev=1)
    r = put(c, PC2, preset(params="22"), base_rev=1, changed_at=1500)
    assert r.status_code == 409 and r.json()["error"] == "changed_elsewhere" and r.json()["item"]["rev"] == 2
    r = put(c, PC2, preset(params="33"), base_rev=1, changed_at=1600)
    assert r.status_code == 409
    k = db.conn().execute("SELECT * FROM shared_candidates WHERE agent_id=?", (PC2["id"],)).fetchall()
    assert len(k) == 1 and json.loads(k[0]["body"])["row"]["op_params"] == "33"
    lst = c.get("/api/shared?agent=%s" % PC2["id"]).json()
    assert lst["conflicts"] == [{"item_id": a["id"], "changed_at": 1600.0, "deleted": False}]
    w, d = waiting(c, PC2)
    assert w == set() and [x["name"] for x in d["conflicts"]] == ["warm"]  # in conflict: listed apart, not waiting
    items = c.get("/api/shared/devices").json()["items"]
    assert items[0]["devices"] == {PC["id"]: "newest", PC2["id"]: "conflict", PHONE["id"]: "waiting"}


def test_resolve_mine(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    put(c, PC, preset(params="11"), base_rev=1)
    put(c, PC2, preset(params="22"), base_rev=1)
    r = c.post("/api/shared/%d/resolve" % a["id"], json={"agent": PC2["id"], "choice": "mine"}).json()
    assert [(i["rev"], i["device_name"]) for i in r["items"]] == [(3, "Laptop")]
    assert c.get("/api/shared/%d" % a["id"]).json()["body"]["row"]["op_params"] == "22"
    assert waiting(c, PC)[0] == {("warm", "changed")} and waiting(c, PC2)[0] == set()
    assert c.get("/api/shared?agent=%s" % PC2["id"]).json()["conflicts"] == []
    assert c.post("/api/shared/%d/resolve" % a["id"], json={"agent": PC2["id"], "choice": "mine"}).status_code == 404


def test_resolve_theirs(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    put(c, PC, preset(params="11"), base_rev=1)
    put(c, PC2, preset(params="22"), base_rev=1)
    r = c.post("/api/shared/%d/resolve" % a["id"], json={"agent": PC2["id"], "choice": "theirs"}).json()
    assert r["items"] == []
    assert c.get("/api/shared/%d" % a["id"]).json()["body"]["row"]["op_params"] == "11"
    assert waiting(c, PC2)[0] == {("warm", "changed")}  # the next update overwrites the local copy


def test_resolve_both_keeps_the_previous_content_under_a_new_name(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    put(c, PC, preset(params="11"), base_rev=1)
    put(c, PC2, preset(params="22"), base_rev=1)
    r = c.post("/api/shared/%d/resolve" % a["id"], json={"agent": PC2["id"], "choice": "both"}).json()
    names = [(i["name"], i["rev"]) for i in r["items"]]
    assert names == [("warm", 3), ("warm (Desktop)", 1)]
    copy_ = c.get("/api/shared/%d" % r["items"][1]["id"]).json()
    assert copy_["body"]["row"]["name"] == "warm (Desktop)" and copy_["body"]["row"]["op_params"] == "11"
    assert copy_["hash"] == shared.item_hash(copy_["body"]) and copy_["device_name"] == "Desktop"
    assert waiting(c, PC)[0] == {("warm", "changed"), ("warm (Desktop)", "new")}
    assert waiting(c, PC2)[0] == {("warm (Desktop)", "new")}


def test_resolve_both_is_refused_for_a_deletion(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    put(c, PC, preset(params="11"), base_rev=1)
    assert c.delete("/api/shared/%d?agent=%s&base_rev=1" % (a["id"], PC2["id"])).status_code == 409
    assert c.post("/api/shared/%d/resolve" % a["id"], json={"agent": PC2["id"], "choice": "both"}).status_code == 400
    r = c.post("/api/shared/%d/resolve" % a["id"], json={"agent": PC2["id"], "choice": "mine"}).json()
    assert r["items"][0]["deleted"] is True and r["items"][0]["rev"] == 3


def test_newest_wins_is_decided_at_put(c):
    a = put(c, PC, preset(), changed_at=1000).json()
    put(c, PHONE, preset())
    state(c, PHONE, {a["id"]: {"rev": 1, "state": "ok"}}, policy="newest")
    put(c, PC, preset(params="11"), base_rev=1, changed_at=2000)
    older = put(c, PHONE, preset(params="22"), base_rev=1, changed_at=1500)
    assert older.status_code == 200 and older.json()["decided"] == "theirs" and older.json()["rev"] == 2
    newer = put(c, PHONE, preset(params="33"), base_rev=1, changed_at=3000)
    assert newer.status_code == 200 and newer.json()["decided"] == "mine" and newer.json()["rev"] == 3
    assert c.get("/api/shared/%d" % a["id"]).json()["body"]["row"]["op_params"] == "33"
    assert db.conn().execute("SELECT COUNT(*) FROM shared_candidates").fetchone()[0] == 0
    # a PC's own policy in the state is ignored; LPL's setting counts
    state(c, PC2, {}, policy="newest")
    assert put(c, PC2, preset(params="44"), base_rev=1, changed_at=9000).status_code == 409
    c.put("/api/prefs/settings", json={"value": {"sharedPolicy": "newest"}})
    r = put(c, PC2, preset(params="55"), base_rev=1, changed_at=9000).json()
    assert r["decided"] == "mine" and r["rev"] == 4
    assert c.get("/api/shared/devices").json()["policy"] == "newest"


def test_deletion_offer_delete_and_keep(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    d = c.delete("/api/shared/%d?agent=%s&base_rev=1" % (a["id"], PC["id"])).json()
    assert d["rev"] == 2
    meta = c.get("/api/shared/%d" % a["id"]).json()
    assert meta["deleted"] is True and meta["body"]["row"]["op_params"] == "0000803f"  # the last body is kept
    assert waiting(c, PC2)[0] == {("warm", "deleted")}
    assert waiting(c, PHONE)[0] == set()  # a deletion of an item it never had
    state(c, PC2, {a["id"]: {"rev": 2, "state": "kept"}})  # "keep": not offered again
    assert waiting(c, PC2)[0] == set()
    assert c.get("/api/shared/devices").json()["items"][0]["devices"][PC2["id"]] == "kept"
    # the kept item is changed there again: PUT with base_rev = the deletion's rev brings it back for everyone
    r = put(c, PC2, preset(params="77"), base_rev=2, changed_at=5000).json()
    assert r["rev"] == 3 and c.get("/api/shared/%d" % a["id"]).json()["deleted"] is False
    assert waiting(c, PC)[0] == {("warm", "changed")} and waiting(c, PHONE)[0] == {("warm", "new")}


def test_delete_choice_and_stale_delete(c):
    a = put(c, PC, preset()).json()
    put(c, PC2, preset())
    put(c, PC, preset(params="11"), base_rev=1)
    r = c.delete("/api/shared/%d?agent=%s&base_rev=1" % (a["id"], PC2["id"]))
    assert r.status_code == 409 and r.json()["error"] == "changed_elsewhere"
    assert c.get("/api/shared?agent=%s" % PC2["id"]).json()["conflicts"][0]["deleted"] is True
    c.post("/api/shared/%d/resolve" % a["id"], json={"agent": PC2["id"], "choice": "theirs"})
    assert waiting(c, PC2)[0] == {("warm", "changed")}
    # delete here: state ok at the deletion's rev
    c.delete("/api/shared/%d?agent=%s&base_rev=2" % (a["id"], PC["id"]))
    state(c, PC2, {a["id"]: {"rev": 3, "state": "ok"}})
    assert waiting(c, PC2)[0] == set()
    again = c.delete("/api/shared/%d?agent=%s&base_rev=1" % (a["id"], PC2["id"]))  # deleted on both: nothing to decide
    assert again.status_code == 200 and again.json()["rev"] == 3


def test_unusable_is_not_waiting_at_that_rev(c):
    a = put(c, PC, preset()).json()
    state(c, PHONE, {a["id"]: {"rev": 1, "state": "unusable"}})
    assert waiting(c, PHONE)[0] == set()
    assert c.get("/api/shared/devices").json()["items"][0]["devices"][PHONE["id"]] == "unusable"
    put(c, PC, preset(params="99"), base_rev=1)
    assert waiting(c, PHONE)[0] == {("warm", "changed")}


def test_styles_and_layouts(c):
    st = VECTORS["vectors"][2]["body"]
    lay = VECTORS["vectors"][1]["body"]
    s = put(c, PC, st, name=st["style"]["name"]).json()
    l = put(c, PC, lay).json()
    lst = {i["id"]: i for i in c.get("/api/shared?since=0").json()["items"]}
    assert lst[s["id"]]["hash"] == VECTORS["vectors"][2]["hash"] and lst[s["id"]]["operation"] == "" and lst[s["id"]]["op_version"] == 0
    assert lst[l["id"]]["kind"] == "layout" and lst[l["id"]]["hash"] == VECTORS["vectors"][1]["hash"]
    # the same name as a preset of another module is another item
    p = preset(name="my workflow · 4 groups")
    assert put(c, PC, p).json()["id"] not in (s["id"], l["id"])


def test_bad_puts(c):
    p = preset()
    assert put(c, PC, p, hash="0" * 64).json()["error"] == "hash_mismatch"
    assert put(c, PC, p, kind="layout").status_code == 400
    assert put(c, PC, p, name="other").status_code == 400
    lay = copy.deepcopy(VECTORS["vectors"][1]["body"])
    lay["kind"] = "preset"
    assert put(c, PC, lay, kind="preset").status_code == 400  # a modulegroups preset is a layout
    assert c.put("/api/shared", json={}).status_code == 400  # agent required


def test_update_job_for_a_pc_only(c):
    a = put(c, PC, preset()).json()
    r = c.post("/api/shared/update", json={"agent": PC2["id"], "ids": [a["id"]], "delete": [], "keep": []})
    assert r.status_code == 200
    r2 = c.post("/api/shared/update", json={"agent": PC2["id"], "ids": [a["id"]]}).json()  # replaces the queued one
    jobs = db.conn().execute("SELECT * FROM jobs WHERE kind='shared-update'").fetchall()
    assert [j["id"] for j in jobs] == [r2["id"]] and json.loads(jobs[0]["payload"]) == {"ids": [a["id"]], "delete": [], "keep": []}
    hb = c.post("/api/agents/heartbeat", json=PC2).json()
    assert [(j["kind"], j["payload"]["ids"]) for j in hb["jobs"]] == [("shared-update", [a["id"]])]
    _, d = waiting(c, PC2)
    assert d["job"]["status"] == "taken"
    assert c.post("/api/shared/update", json={"agent": PHONE["id"], "ids": [a["id"]]}).status_code == 400
    c.post("/api/agents/heartbeat", json={"id": "pc-old", "name": "Old"})
    assert c.post("/api/shared/update", json={"agent": "pc-old", "ids": []}).status_code == 400
    assert c.post("/api/shared/update", json={"agent": PC2["id"], "ids": ["x"]}).status_code == 400


def test_heartbeat_state_shared_update_reaches_the_devices_page(c):
    c.post("/api/agents/heartbeat", json=dict(PC, state={"shared_update": "waiting_for_darktable", "darktable_running": True}))
    _, d = waiting(c, PC)
    assert d["update"] == "waiting_for_darktable" and d["darktable_running"] is True and d["phone"] is False
    _, p = waiting(c, PHONE)
    assert p["phone"] is True


def test_migrate_twice_changes_nothing(c):
    put(c, PC, preset())
    with db.write_lock:
        db.migrate(db.conn())
        db.migrate(db.conn())
    assert db.conn().execute("SELECT COUNT(*) FROM shared_items").fetchone()[0] == 1


def test_delete_carries_changed_at_for_newest_wins(c):
    a = put(c, PC, preset(), changed_at=1000).json()
    put(c, PC2, preset())
    put(c, PC, preset(params="11"), base_rev=1, changed_at=3000)
    c.put("/api/prefs/settings", json={"value": {"sharedPolicy": "newest"}})
    old = c.delete("/api/shared/%d?agent=%s&base_rev=1&changed_at=2000" % (a["id"], PC2["id"])).json()
    assert old["decided"] == "theirs" and c.get("/api/shared/%d" % a["id"]).json()["deleted"] is False
    new = c.delete("/api/shared/%d?agent=%s&base_rev=1&changed_at=4000" % (a["id"], PC2["id"])).json()
    assert new["decided"] == "mine" and c.get("/api/shared/%d" % a["id"]).json()["changed_at"] == 4000.0


def test_devices_page_names_the_module(c):
    put(c, PC, preset())
    it = c.get("/api/shared/devices").json()["items"][0]
    assert it["operation"] == "channelmixerrgb" and it["module"] == "color calibration"
