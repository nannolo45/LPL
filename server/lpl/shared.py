# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared darktable styles, presets and module layouts (R11, docs/shared-r11.md; contract: darktable-android PLAN.md 4.5).

Items travel as rows (JSON bodies), never as files. The server keeps, per item, its current revision (body + meta; a
deletion is a tombstone that keeps the last body), per agent and item at most one conflict candidate, per agent and item
the revision the device reported (`PUT /api/shared/state`), and per agent its policy (phones send theirs; for PCs the
policy is LPL's setting `sharedPolicy` in the `settings` preference).

A device takes part only when its heartbeat caps contain "shared"; the heartbeat answer then lists "shared" in
server_caps. Nothing here imports api.py: api.py mounts `router` and adds SERVER_CAPS to the heartbeat answer."""
import hashlib
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from . import db

SERVER_CAPS = ["shared"]
KINDS = ("style", "preset", "layout")
LAYOUT_OP = "modulegroups"
MAX_BODY = 4 * 1024 * 1024  # bytes of one item's JSON body
STATES = ("ok", "kept", "unusable")

PRESET_CORE = ["name", "description", "operation", "op_version", "op_params", "enabled", "blendop_params",
               "blendop_version", "multi_priority", "multi_name", "multi_name_hand_edited", "model", "maker", "lens",
               "iso_min", "iso_max", "exposure_min", "exposure_max", "aperture_min", "aperture_max",
               "focal_length_min", "focal_length_max", "autoapply", "filter", "def", "format"]
LAYOUT_CORE = ["name", "operation", "op_version", "op_params"]
STYLE_CORE = ["name", "description", "iop_list"]
ITEM_CORE = ["num", "module", "operation", "op_params", "enabled", "blendop_params", "blendop_version",
             "multi_priority", "multi_name", "multi_name_hand_edited"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                 -- style / preset / layout
    operation TEXT NOT NULL DEFAULT '', -- '' for styles
    op_version INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    rev INTEGER NOT NULL,               -- 1 for a new item, +1 for every change or deletion
    seq INTEGER NOT NULL,               -- one counter over all items: GET /api/shared?since=
    hash TEXT NOT NULL,                 -- of the current body (a deletion keeps the last body and its hash)
    changed_at REAL,
    device_id TEXT,
    device_name TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    body TEXT NOT NULL,
    UNIQUE(kind, operation, op_version, name)
);
CREATE INDEX IF NOT EXISTS idx_shared_items_seq ON shared_items(seq);
CREATE TABLE IF NOT EXISTS shared_candidates (   -- a copy that was changed on a device while it changed elsewhere
    agent_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    hash TEXT,
    changed_at REAL,
    deleted INTEGER NOT NULL DEFAULT 0,
    base_rev INTEGER,
    body TEXT,
    created REAL,
    PRIMARY KEY(agent_id, item_id)
);
CREATE TABLE IF NOT EXISTS shared_state (        -- what each device reported it has
    agent_id TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    rev INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'ok',
    PRIMARY KEY(agent_id, item_id)
);
CREATE TABLE IF NOT EXISTS shared_agents (
    agent_id TEXT PRIMARY KEY,
    policy TEXT,                        -- ask / newest (used for phones; PCs follow LPL's setting)
    updated REAL
);
"""

router = APIRouter()


def migrate(c) -> None:
    """New tables; called by db.migrate at every start, changes nothing the second time."""
    c.executescript(SCHEMA)


# ---------------------------------------------------------------- hash (PLAN.md 4.5; vectors in tests/fixtures/r11-hash-vectors.json)
def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def core(body: dict) -> dict:
    k = body["kind"]
    if k == "style":
        items = sorted((it for it in body.get("items") or [] if isinstance(it, dict)), key=lambda i: (i.get("num") is None, i.get("num") or 0))
        return {"kind": k, "style": {c: (body.get("style") or {}).get(c) for c in STYLE_CORE},
                "items": [{c: it.get(c) for c in ITEM_CORE} for it in items]}
    cols = LAYOUT_CORE if k == "layout" else PRESET_CORE
    return {"kind": k, "row": {c: (body.get("row") or {}).get(c) for c in cols}}


def item_hash(body: dict) -> str:
    return hashlib.sha256(canonical(core(body))).hexdigest()


# ---------------------------------------------------------------- helpers
def _policy_pc() -> str:
    try:
        s = json.loads(db.get_setting("pref.settings") or "null")
    except ValueError:
        s = None
    return "newest" if isinstance(s, dict) and s.get("sharedPolicy") == "newest" else "ask"


def _agent(c, aid: str):
    return c.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()


def _is_phone(a) -> bool:
    return bool(a) and a["os"] == "android"


def policy_of(c, aid: str) -> str:
    a = _agent(c, aid)
    if _is_phone(a):
        r = c.execute("SELECT policy FROM shared_agents WHERE agent_id=?", (aid,)).fetchone()
        return "newest" if r and r["policy"] == "newest" else "ask"
    return _policy_pc()


def _caps(c, aid: str) -> List[str]:
    from . import transfers
    return transfers.caps_of(c, aid)


def _time(v) -> float:
    """changed_at: seconds since 1970 (a number), or an ISO 8601 text. Anything else: now."""
    if isinstance(v, bool):
        return time.time()
    if isinstance(v, (int, float)) and v == v:
        return float(v)
    if isinstance(v, str) and v.strip():
        try:
            return float(v)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(v.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


def meta(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "operation": r["operation"], "op_version": r["op_version"], "name": r["name"],
            "rev": r["rev"], "seq": r["seq"], "hash": r["hash"], "changed_at": r["changed_at"], "device_id": r["device_id"],
            "device_name": r["device_name"], "deleted": bool(r["deleted"]), "size": r["size"]}


def _next_seq(c) -> int:
    return (c.execute("SELECT COALESCE(MAX(seq),0) FROM shared_items").fetchone()[0] or 0) + 1


def _dump(body: dict) -> str:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _device_name(c, aid: str) -> str:
    a = _agent(c, aid)
    return (a["name"] if a and a["name"] else aid) or ""


def _item(c, iid: int):
    return c.execute("SELECT * FROM shared_items WHERE id=?", (iid,)).fetchone()


def _set_state(c, aid: str, iid: int, rev: int, state: str = "ok") -> None:
    c.execute("INSERT INTO shared_state(agent_id,item_id,rev,state) VALUES(?,?,?,?) ON CONFLICT(agent_id,item_id) "
              "DO UPDATE SET rev=excluded.rev, state=excluded.state", (aid, iid, rev, state))


def _new_revision(c, r, *, body: Optional[str], hsh: str, deleted: bool, changed_at: float, aid: str, dev: str) -> None:
    """The item's next revision: new content, or a deletion (keeps the last body and its hash)."""
    seq = _next_seq(c)
    if deleted:
        c.execute("UPDATE shared_items SET rev=rev+1, seq=?, deleted=1, changed_at=?, device_id=?, device_name=? WHERE id=?",
                  (seq, changed_at, aid, dev, r["id"]))
    else:
        c.execute("UPDATE shared_items SET rev=rev+1, seq=?, deleted=0, changed_at=?, device_id=?, device_name=?, hash=?, body=?, "
                  "size=? WHERE id=?", (seq, changed_at, aid, dev, hsh, body, len(body.encode("utf-8")), r["id"]))


def _key(body: Dict[str, Any]):
    """(kind, operation, op_version, name) of a PUT, checked against its body; HTTPException 400 when it does not fit."""
    kind = body.get("kind")
    if kind not in KINDS:
        raise HTTPException(400, "kind must be style, preset or layout")
    name = body.get("name")
    if not isinstance(name, str) or not name or len(name) > 1000:
        raise HTTPException(400, "name required")
    b = body.get("body")
    if not isinstance(b, dict) or b.get("kind") != kind:
        raise HTTPException(400, "body must be an object of the same kind")
    if kind == "style":
        st = b.get("style")
        if not isinstance(st, dict) or st.get("name") != name or not isinstance(b.get("items"), list) \
                or not all(isinstance(i, dict) for i in b["items"]):
            raise HTTPException(400, "a style body needs style.name = name and a list of items")
        return kind, "", 0, name
    op, ver = body.get("operation"), body.get("op_version")
    if not isinstance(op, str) or not op or isinstance(ver, bool) or not isinstance(ver, int):
        raise HTTPException(400, "operation and op_version required")
    if (kind == "layout") != (op == LAYOUT_OP):
        raise HTTPException(400, "a layout is a preset of %s, a preset is not" % LAYOUT_OP)
    row = b.get("row")
    if not isinstance(row, dict) or row.get("name") != name or row.get("operation") != op or row.get("op_version") != ver:
        raise HTTPException(400, "body.row must carry the same name, operation and op_version")
    return kind, op, ver, name


def _conflict(c, aid: str, r, *, body: Optional[str], hsh: Optional[str], deleted: bool, changed_at: float, base_rev: int):
    """The item changed elsewhere. Policy ask: keep this copy as the agent's candidate, 409. Policy newest: decide now."""
    dev = _device_name(c, aid)
    if policy_of(c, aid) == "newest":
        if changed_at > (r["changed_at"] or 0):
            _new_revision(c, r, body=body, hsh=hsh or r["hash"], deleted=deleted, changed_at=changed_at, aid=aid, dev=dev)
            c.execute("DELETE FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, r["id"]))
            r = _item(c, r["id"])
            _set_state(c, aid, r["id"], r["rev"])
            c.commit()
            return {"id": r["id"], "rev": r["rev"], "seq": r["seq"], "decided": "mine"}
        c.execute("DELETE FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, r["id"]))
        c.commit()
        return {"id": r["id"], "rev": r["rev"], "seq": r["seq"], "decided": "theirs"}
    c.execute("INSERT INTO shared_candidates(agent_id,item_id,hash,changed_at,deleted,base_rev,body,created) VALUES(?,?,?,?,?,?,?,?) "
              "ON CONFLICT(agent_id,item_id) DO UPDATE SET hash=excluded.hash, changed_at=excluded.changed_at, deleted=excluded.deleted, "
              "base_rev=excluded.base_rev, body=excluded.body, created=excluded.created",
              (aid, r["id"], hsh, changed_at, 1 if deleted else 0, base_rev, body, time.time()))
    c.commit()
    return JSONResponse({"error": "changed_elsewhere", "item": meta(r)}, status_code=409)


def _agent_id(agent: str) -> str:
    aid = (agent or "")[:80]
    if not aid:
        raise HTTPException(400, "agent required")
    return aid


# ---------------------------------------------------------------- what a device waits for
def waiting_for(c, aid: str) -> List[dict]:
    """Items whose current rev is above the device's, except a deletion of an item it does not have, an item it reported
    kept / unusable at that rev, and items in conflict for it. Each with what: new / changed / deleted."""
    states = {r["item_id"]: r for r in c.execute("SELECT * FROM shared_state WHERE agent_id=?", (aid,))}
    conflicts = {r[0] for r in c.execute("SELECT item_id FROM shared_candidates WHERE agent_id=?", (aid,))}
    out = []
    for r in c.execute("SELECT * FROM shared_items ORDER BY changed_at DESC, id"):
        if r["id"] in conflicts:
            continue
        st = states.get(r["id"])
        if st is not None and st["rev"] >= r["rev"]:
            continue  # has it, or reported it kept / unusable at this rev
        if r["deleted"]:
            if st is None:
                continue  # a deletion of an item it never had
            what = "deleted"
        else:
            what = "new" if st is None else "changed"
        out.append(dict(meta(r), what=what))
    return out


def conflicts_of(c, aid: str) -> List[dict]:
    out = []
    for k in c.execute("SELECT k.*, i.id AS iid FROM shared_candidates k JOIN shared_items i ON i.id=k.item_id WHERE k.agent_id=? "
                       "ORDER BY k.created", (aid,)).fetchall():
        r = _item(c, k["iid"])
        out.append(dict(meta(r), candidate={"changed_at": k["changed_at"], "deleted": bool(k["deleted"]), "hash": k["hash"],
                                            "base_rev": k["base_rev"]}))
    return out


# ---------------------------------------------------------------- calls (contract, PLAN.md 4.5)
@router.get("/api/shared")
def shared_list(agent: str = "", since: int = 0):
    c = db.conn()
    rows = c.execute("SELECT * FROM shared_items WHERE seq>? ORDER BY seq", (since,)).fetchall()
    seq = c.execute("SELECT COALESCE(MAX(seq),0) FROM shared_items").fetchone()[0] or 0
    conf = []
    if agent:
        conf = [{"item_id": k["item_id"], "changed_at": k["changed_at"], "deleted": bool(k["deleted"])}
                for k in c.execute("SELECT * FROM shared_candidates WHERE agent_id=? ORDER BY item_id", (agent[:80],))]
    return {"seq": seq, "items": [meta(r) for r in rows], "conflicts": conf}


@router.get("/api/shared/devices")
def shared_devices():
    """For the web UI: every agent with "shared" in its caps, what it waits for and its open conflicts; per item, which
    agents have its current rev (newest / waiting / conflict / kept / unusable / absent)."""
    c = db.conn()
    from .api import list_agents  # api imports this module at load time; this call runs later
    devices, per = [], {}
    for a in list_agents():
        if "shared" not in (a.get("caps") or []):
            continue
        aid = a["id"]
        w = waiting_for(c, aid)
        k = conflicts_of(c, aid)
        job = c.execute("SELECT id, status FROM jobs WHERE agent_id=? AND kind='shared-update' AND status IN ('queued','taken') "
                        "ORDER BY id DESC LIMIT 1", (aid,)).fetchone()
        st = (a.get("state") or {}) if isinstance(a.get("state"), dict) else {}
        devices.append({"id": aid, "name": a["name"] or aid, "os": a["os"], "phone": a["os"] == "android", "online": a["online"],
                        "policy": policy_of(c, aid), "update": st.get("shared_update"),
                        "darktable_running": bool(st.get("darktable_running")),
                        "job": dict(job) if job else None, "waiting": w, "conflicts": k})
        states = {r["item_id"]: r for r in c.execute("SELECT * FROM shared_state WHERE agent_id=?", (aid,))}
        per[aid] = (states, {x["id"] for x in w}, {x["id"] for x in k})
    items = []
    for r in c.execute("SELECT * FROM shared_items ORDER BY kind, name COLLATE NOCASE, id"):
        dots = {}
        for aid, (states, wait, conf) in per.items():
            st = states.get(r["id"])
            if r["id"] in conf:
                dots[aid] = "conflict"
            elif r["id"] in wait:
                dots[aid] = "waiting"
            elif st is not None and st["state"] in ("kept", "unusable") and st["rev"] >= r["rev"]:
                dots[aid] = st["state"]
            elif st is not None and st["rev"] >= r["rev"] and not r["deleted"]:
                dots[aid] = "newest"
            else:
                dots[aid] = "absent"
        items.append(dict(meta(r), devices=dots))
    return {"policy": _policy_pc(), "devices": devices, "items": items}


@router.get("/api/shared/{iid:int}")
def shared_get(iid: int):
    r = _item(db.conn(), iid)
    if not r:
        raise HTTPException(404, "no such item")
    return dict(meta(r), body=json.loads(r["body"]))


@router.put("/api/shared/state")
def shared_state(agent: str = "", body: Dict[str, Any] = Body(...)):
    aid = _agent_id(agent)
    items = body.get("items")
    if not isinstance(items, dict):
        raise HTTPException(400, "items must be an object")
    rows = []
    for k, v in items.items():
        try:
            iid = int(k)
        except (TypeError, ValueError):
            raise HTTPException(400, "item ids are numbers")
        if not isinstance(v, dict) or isinstance(v.get("rev"), bool) or not isinstance(v.get("rev"), int):
            raise HTTPException(400, "each item needs rev")
        st = v.get("state") or "ok"
        if st not in STATES:
            raise HTTPException(400, "state must be ok, kept or unusable")
        rows.append((aid, iid, v["rev"], st))
    with db.write_lock:
        c = db.conn()
        c.execute("DELETE FROM shared_state WHERE agent_id=?", (aid,))
        c.executemany("INSERT INTO shared_state(agent_id,item_id,rev,state) VALUES(?,?,?,?)", rows)
        pol = body.get("policy")
        if pol in ("ask", "newest"):
            c.execute("INSERT INTO shared_agents(agent_id,policy,updated) VALUES(?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
                      "policy=excluded.policy, updated=excluded.updated", (aid, pol, time.time()))
        c.commit()
    return {"ok": True, "items": len(rows)}


@router.put("/api/shared")
def shared_put(agent: str = "", body: Dict[str, Any] = Body(...)):
    aid = _agent_id(agent)
    kind, op, ver, name = _key(body)
    b = body["body"]
    hsh = item_hash(b)
    if body.get("hash") not in (None, hsh):
        return JSONResponse({"error": "hash_mismatch", "hash": hsh}, status_code=400)
    data = _dump(b)
    if len(data.encode("utf-8")) > MAX_BODY:
        raise HTTPException(413, "item too large")
    base = body.get("base_rev") or 0
    if isinstance(base, bool) or not isinstance(base, int):
        raise HTTPException(400, "base_rev must be a number")
    changed_at = _time(body.get("changed_at"))
    with db.write_lock:
        c = db.conn()
        dev = _device_name(c, aid)
        r = c.execute("SELECT * FROM shared_items WHERE kind=? AND operation=? AND op_version=? AND name=?", (kind, op, ver, name)).fetchone()
        if r is None:
            cur = c.execute("INSERT INTO shared_items(kind,operation,op_version,name,rev,seq,hash,changed_at,device_id,device_name,deleted,size,body) "
                            "VALUES(?,?,?,?,1,?,?,?,?,?,0,?,?)",
                            (kind, op, ver, name, _next_seq(c), hsh, changed_at, aid, dev, len(data.encode("utf-8")), data))
            r = _item(c, cur.lastrowid)
            _set_state(c, aid, r["id"], r["rev"])
            c.commit()
            return {"id": r["id"], "rev": r["rev"], "seq": r["seq"], "decided": None}
        if not r["deleted"] and hsh == r["hash"]:  # the same content: nothing changes, whatever base_rev
            c.execute("DELETE FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, r["id"]))
            _set_state(c, aid, r["id"], r["rev"])
            c.commit()
            return {"id": r["id"], "rev": r["rev"], "seq": r["seq"], "decided": None}
        if base == r["rev"]:  # the device had the current revision: its change is the next one (a deletion comes back)
            _new_revision(c, r, body=data, hsh=hsh, deleted=False, changed_at=changed_at, aid=aid, dev=dev)
            c.execute("DELETE FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, r["id"]))
            r = _item(c, r["id"])
            _set_state(c, aid, r["id"], r["rev"])
            c.commit()
            return {"id": r["id"], "rev": r["rev"], "seq": r["seq"], "decided": None}
        return _conflict(c, aid, r, body=data, hsh=hsh, deleted=False, changed_at=changed_at, base_rev=base)


@router.delete("/api/shared/{iid:int}")
def shared_delete(iid: int, agent: str = "", base_rev: int = -1):
    aid = _agent_id(agent)
    with db.write_lock:
        c = db.conn()
        r = _item(c, iid)
        if not r:
            raise HTTPException(404, "no such item")
        if r["deleted"]:  # deleted here and elsewhere: nothing to decide
            c.execute("DELETE FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, iid))
            _set_state(c, aid, iid, r["rev"])
            c.commit()
            return {"id": iid, "rev": r["rev"], "seq": r["seq"], "decided": None}
        now = time.time()
        if base_rev == r["rev"]:
            _new_revision(c, r, body=None, hsh=r["hash"], deleted=True, changed_at=now, aid=aid, dev=_device_name(c, aid))
            c.execute("DELETE FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, iid))
            r = _item(c, iid)
            _set_state(c, aid, iid, r["rev"])
            c.commit()
            return {"id": iid, "rev": r["rev"], "seq": r["seq"], "decided": None}
        return _conflict(c, aid, r, body=None, hsh=None, deleted=True, changed_at=now, base_rev=base_rev)


def _renamed(body: dict, name: str) -> dict:
    b = json.loads(json.dumps(body))
    if b["kind"] == "style":
        b["style"]["name"] = name
    else:
        b["row"]["name"] = name
    return b


@router.post("/api/shared/{iid:int}/resolve")
def shared_resolve(iid: int, body: Dict[str, Any] = Body(...)):
    aid = _agent_id(body.get("agent") or "")
    choice = body.get("choice")
    if choice not in ("mine", "theirs", "both"):
        raise HTTPException(400, "choice must be mine, theirs or both")
    with db.write_lock:
        c = db.conn()
        r = _item(c, iid)
        k = c.execute("SELECT * FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, iid)).fetchone()
        if not r or not k:
            raise HTTPException(404, "no open conflict for this agent and item")
        changed = []
        if choice == "both" and (k["deleted"] or r["deleted"]):
            raise HTTPException(400, "keep both needs content on both sides (one of them is a deletion)")
        if choice in ("mine", "both"):
            prev = r
            dev = _device_name(c, aid)
            _new_revision(c, r, body=k["body"], hsh=k["hash"] or r["hash"], deleted=bool(k["deleted"]), changed_at=k["changed_at"] or time.time(),
                          aid=aid, dev=dev)
            r = _item(c, iid)
            _set_state(c, aid, iid, r["rev"])
            changed.append(r)
            if choice == "both":  # the previous content stays as "<name> (<device of the previous revision>)"
                changed.append(_store_copy(c, prev))
        c.execute("DELETE FROM shared_candidates WHERE agent_id=? AND item_id=?", (aid, iid))
        c.commit()
        return {"items": [meta(x) for x in changed]}


def _store_copy(c, prev):
    pbody = json.loads(prev["body"])
    base = "%s (%s)" % (prev["name"], prev["device_name"] or prev["device_id"] or "other device")
    n = 1
    while True:
        name = base if n == 1 else "%s %d" % (base, n)
        b = _renamed(pbody, name)
        hsh, data = item_hash(b), _dump(b)
        ex = c.execute("SELECT * FROM shared_items WHERE kind=? AND operation=? AND op_version=? AND name=?",
                       (prev["kind"], prev["operation"], prev["op_version"], name)).fetchone()
        if ex is None:
            cur = c.execute("INSERT INTO shared_items(kind,operation,op_version,name,rev,seq,hash,changed_at,device_id,device_name,deleted,size,body) "
                            "VALUES(?,?,?,?,1,?,?,?,?,?,0,?,?)",
                            (prev["kind"], prev["operation"], prev["op_version"], name, _next_seq(c), hsh, prev["changed_at"],
                             prev["device_id"], prev["device_name"], len(data.encode("utf-8")), data))
            return _item(c, cur.lastrowid)
        if ex["deleted"]:
            _new_revision(c, ex, body=data, hsh=hsh, deleted=False, changed_at=prev["changed_at"] or time.time(),
                          aid=prev["device_id"], dev=prev["device_name"])
            return _item(c, ex["id"])
        if ex["hash"] == hsh:
            return ex
        n += 1


@router.post("/api/shared/update")
def shared_update(body: Dict[str, Any] = Body(...)):
    """Web UI: "Update <device>" for a PC. Makes a shared-update job with the payload (a queued one not yet taken is replaced)."""
    aid = _agent_id(body.get("agent") or "")

    def ids(key):
        v = body.get(key) or []
        if not isinstance(v, list) or any(isinstance(x, bool) or not isinstance(x, int) for x in v):
            raise HTTPException(400, "%s must be a list of item ids" % key)
        return list(dict.fromkeys(v))
    payload = {"ids": ids("ids"), "delete": ids("delete"), "keep": ids("keep")}
    with db.write_lock:
        c = db.conn()
        a = _agent(c, aid)
        if not a:
            raise HTTPException(404, "unknown device")
        if _is_phone(a):
            raise HTTPException(400, "a phone takes shared items in from its own review sheet")
        if "shared" not in _caps(c, aid):
            raise HTTPException(400, "this device does not share styles and presets (agent too old?)")
        c.execute("DELETE FROM jobs WHERE agent_id=? AND kind='shared-update' AND status='queued'", (aid,))
        cur = c.execute("INSERT INTO jobs(agent_id,kind,payload,created) VALUES(?,?,?,?)",
                        (aid, "shared-update", json.dumps(payload), time.time()))
        c.commit()
    return {"id": cur.lastrowid, "agent_id": aid}
