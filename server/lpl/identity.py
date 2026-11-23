# SPDX-License-Identifier: GPL-3.0-or-later
"""Which device is which: names, renames from LPL, and one computer that came back under a new id.

A PC agent keeps its id in its config file. Installing it again after that file was lost gives the same
computer a new id, and LPL used to show both: the old entry (offline, with its old name) next to the new one.
Agents now also send `machine`, a hash of the computer's own id (/etc/machine-id, Windows' MachineGuid), so LPL
can tell that two entries are one computer and merge them: the new id keeps its name; jobs, batches, the edit
log and version checkouts of the old one move over; the old entry goes.

Names: the agent reports the name set on the device. A rename in LPL (PUT /api/agents/{id}/name) is stored and
said to the device in every heartbeat answer (`rename`) until it reports that name itself; until then LPL keeps
its own. So the last change wins, wherever it was made. Devices that do not know `rename` (the phone app, older
agents) simply keep the name given in LPL.

Nothing here imports api.py: api.heartbeat calls on_heartbeat, api.list_agents calls decorate."""
import json
import re
import time
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Body, HTTPException

from . import db

router = APIRouter()


def migrate(c) -> None:
    cols = {r[1] for r in c.execute("PRAGMA table_info(agents)").fetchall()}
    for col, decl in (("machine", "TEXT"), ("name_pending", "TEXT"), ("merged_from", "TEXT")):
        if col not in cols:  # merged_from: JSON list of the names it was merged from, for the Settings note
            c.execute("ALTER TABLE agents ADD COLUMN %s %s" % (col, decl))


def clean_name(v) -> str:
    return re.sub(r"\s+", " ", v).strip()[:60] if isinstance(v, str) else ""


def clean_machine(v) -> str:
    return re.sub(r"[^0-9a-zA-Z_-]", "", v)[:64] if isinstance(v, str) else ""


def auto_merge() -> bool:
    """Settings > Devices & library > merge a reinstalled agent into the old device without asking (on unless off)."""
    try:
        s = json.loads(db.get_setting("pref.settings") or "null")
    except ValueError:
        s = None
    return not (isinstance(s, dict) and s.get("devicesAutoMerge") is False)


def _online_seconds() -> float:
    from .api import AGENT_ONLINE_SECONDS
    return AGENT_ONLINE_SECONDS


def name_for(c, aid: str, body: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The name to store for this heartbeat, and what to add to the answer. Caller holds db.write_lock."""
    reported = clean_name(body.get("name"))
    r = c.execute("SELECT name, name_pending FROM agents WHERE id=?", (aid,)).fetchone()
    if r and r["name_pending"]:
        if reported == r["name_pending"]:
            c.execute("UPDATE agents SET name_pending=NULL WHERE id=?", (aid,))
            return reported, {}
        return r["name"] or r["name_pending"], {"rename": r["name_pending"]}
    return reported or (r["name"] if r else "") or aid[:8], {}


def after_heartbeat(c, aid: str, body: Dict[str, Any], was_new: bool, now: float) -> None:
    """Store the machine id; a new id on a computer LPL knows under another one is merged into it (if allowed)."""
    m = clean_machine(body.get("machine"))
    if not m:
        return
    c.execute("UPDATE agents SET machine=? WHERE id=?", (m, aid))
    if not was_new or not auto_merge():
        return
    for o in c.execute("SELECT id, last_seen FROM agents WHERE machine=? AND id<>?", (m, aid)).fetchall():
        if now - (o["last_seen"] or 0) >= _online_seconds():  # one still reporting is a second agent there: leave it
            merge(c, o["id"], aid)


def merge(c, old: str, into: str) -> None:
    """Everything of device `old` becomes `into`'s; `old` is removed. Caller holds db.write_lock and commits."""
    r = c.execute("SELECT name, merged_from FROM agents WHERE id=?", (old,)).fetchone()
    t = c.execute("SELECT name, merged_from FROM agents WHERE id=?", (into,)).fetchone()
    if not r or not t:
        raise HTTPException(404, "device not found")
    for table in ("jobs", "transfers"):
        c.execute("UPDATE %s SET agent_id=? WHERE agent_id=?" % table, (into, old))
    c.execute("UPDATE edit_events SET agent_id=?, device=? WHERE agent_id=?", (into, t["name"], old))
    if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='version_checkouts'").fetchone():
        c.execute("INSERT OR IGNORE INTO version_checkouts(agent_id,photo_id,ts,ns) SELECT ?,photo_id,ts,ns FROM version_checkouts WHERE agent_id=?",
                  (into, old))
        c.execute("DELETE FROM version_checkouts WHERE agent_id=?", (old,))
    names = []
    for x in (t["merged_from"], r["merged_from"]):
        try:
            names += [n for n in json.loads(x or "[]") if isinstance(n, str)]
        except ValueError:
            pass
    if r["name"] and r["name"] != t["name"]:
        names.append(r["name"])
    c.execute("UPDATE agents SET merged_from=? WHERE id=?", (json.dumps(list(dict.fromkeys(names))[-5:]) if names else None, into))
    c.execute("DELETE FROM agents WHERE id=?", (old,))
    try:
        folds = json.loads(db.get_setting("pref.devices.fold") or "{}")
    except ValueError:
        folds = {}
    if isinstance(folds, dict) and any(k in folds for k in (old, "phone:" + old)):
        for k in (old, "phone:" + old):
            if k in folds:
                folds.setdefault(k.replace(old, into), folds[k])
                folds.pop(k)
        c.execute("INSERT INTO settings(key,value) VALUES('pref.devices.fold',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (json.dumps(folds),))
    from . import transfers
    with transfers._samples_lock:
        transfers._samples.pop(old, None)


def decorate(agents: List[dict]) -> None:
    """Adds same_machine (other entries of the same computer), renaming (a name given in LPL the device has not
    taken yet) and merged_from (names it was merged from); drops machine and name_pending."""
    by: Dict[str, List[str]] = {}
    for a in agents:
        if a.get("machine"):
            by.setdefault(a["machine"], []).append(a["id"])
    for a in agents:
        m = a.pop("machine", None)
        a["same_machine"] = [x for x in by.get(m, []) if x != a["id"]] if m else []
        a["renaming"] = a.pop("name_pending", None) or None
        try:
            a["merged_from"] = json.loads(a.get("merged_from") or "[]")
        except ValueError:
            a["merged_from"] = []


# ---------------------------------------------------------------- what the web app can ask for
@router.put("/api/agents/{aid}/name")
def rename(aid: str, body: Dict[str, Any] = Body(...)):
    name = clean_name(body.get("name"))
    if not name:
        raise HTTPException(400, "name required")
    with db.write_lock:
        c = db.conn()
        r = c.execute("SELECT name FROM agents WHERE id=?", (aid,)).fetchone()
        if not r:
            raise HTTPException(404, "device not found")
        c.execute("UPDATE agents SET name=?, name_pending=? WHERE id=?", (name, name, aid))
        c.execute("UPDATE edit_events SET device=? WHERE agent_id=?", (name, aid))
        c.commit()
    return {"ok": True, "name": name}


@router.post("/api/agents/{aid}/merge")
def merge_into(aid: str, body: Dict[str, Any] = Body(...)):
    """Merge device `aid` (the old entry) into body.into; refused while `aid` is still reporting."""
    into = str(body.get("into") or "")
    if not into or into == aid:
        raise HTTPException(400, "into: another device's id")
    with db.write_lock:
        c = db.conn()
        r = c.execute("SELECT last_seen FROM agents WHERE id=?", (aid,)).fetchone()
        if not r:
            raise HTTPException(404, "device not found")
        if time.time() - (r["last_seen"] or 0) < _online_seconds():
            raise HTTPException(409, "this device is online: only an entry that stopped reporting can be merged away")
        merge(c, aid, into)
        c.commit()
    return {"ok": True}
