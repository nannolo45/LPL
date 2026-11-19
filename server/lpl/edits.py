"""Who edited a photo last, and what changed (R15 of the darktable Android plan).

Every XMP that an agent posts is recorded as an edit event: the device, the time, the darktable modules the
edit uses, and what changed against the XMP LPL had before. A changed XMP that the scanner finds and no agent
posted is recorded as edited outside LPL. Only modules are compared (operation and instance, on or off, a
fingerprint of the settings); settings are never decoded.

Display names of modules come from resources/module-names.json next to this file (operation -> English name,
generated in the darktable-android repository as schema/module-names.json). Without the file, or for an
operation it does not list, the operation itself is shown.
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional

from . import db
from .config import config

log = logging.getLogger("lpl.edits")

MODULE_NAMES = Path(__file__).resolve().parent / "resources" / "module-names.json"
SUMMARY_NAMES = 4  # names shown in the one-line summary before "and N more"

_names = {"mtime": None, "map": {}}


def module_names() -> dict:
    """operation -> English name, re-read when the file changes; {} when it is missing or unreadable."""
    try:
        m = MODULE_NAMES.stat().st_mtime
    except OSError:
        _names.update(mtime=None, map={})
        return {}
    if m != _names["mtime"]:
        try:
            data = json.loads(MODULE_NAMES.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            _names["map"] = {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}
        except (OSError, ValueError) as e:
            log.warning("cannot read %s (%s): showing operation names", MODULE_NAMES, e)
            _names["map"] = {}
        _names["mtime"] = m
    return _names["map"]


def module_name(op: str) -> str:
    return module_names().get(op) or op


def _instance_label(inst: str) -> str:
    # darktable names an instance after the preset that made it, "_builtin_" marking its own presets
    return inst[len("_builtin_"):] if inst.startswith("_builtin_") else inst


def public(m: dict) -> dict:
    name = module_name(m["operation"])
    inst = _instance_label(m.get("instance") or "")
    return {"operation": m["operation"], "name": name, "instance": m.get("instance") or "", "priority": m.get("priority", 0),
            "enabled": bool(m.get("enabled")), "label": "%s %s" % (name, inst) if inst else name}


def signature(hist: list) -> str:
    return json.dumps(sorted([m["operation"], m["priority"], m["instance"], bool(m["enabled"]), m["params"]] for m in hist))


def diff(prev: Optional[list], new: list) -> Optional[dict]:
    """What changed from prev to new, per module instance; None when there is nothing to compare with."""
    if prev is None:
        return None
    key = lambda m: (m["operation"], m["priority"])
    old = {key(m): m for m in prev}
    out = {"added": [], "removed": [], "switched_on": [], "switched_off": [], "changed": []}
    for m in new:  # new is most recently changed first, so each list is too
        o = old.pop(key(m), None)
        if o is None:
            out["added"].append(m)
            continue
        if bool(o["enabled"]) != bool(m["enabled"]):
            out["switched_on" if m["enabled"] else "switched_off"].append(m)
        elif o["params"] != m["params"] or (o.get("instance") or "") != (m.get("instance") or ""):
            out["changed"].append(m)
    out["removed"] = list(old.values())
    return out


def _store_module(m: dict) -> dict:
    return {"operation": m["operation"], "instance": m.get("instance") or "", "priority": m.get("priority", 0),
            "enabled": bool(m.get("enabled"))}


def touched(changes: Optional[dict], modules: list) -> list:
    """The modules an edit is about: the ones it changed, else (nothing to compare with) the ones that are on."""
    if changes is None:
        return [m for m in modules if m["enabled"]]
    return changes["changed"] + changes["added"] + changes["switched_on"] + changes["switched_off"] + changes["removed"]


def summary(changes: Optional[dict], modules: list) -> str:
    if not modules and (changes is None or not touched(changes, modules)):
        return "no darktable edit"
    names = []
    for m in touched(changes, modules):
        lab = public(m)["label"]
        if lab not in names:
            names.append(lab)
    if not names:
        return "no module changed"
    more = len(names) - SUMMARY_NAMES
    return ", ".join(names[:SUMMARY_NAMES]) + (" and %d more" % more if more > 0 else "")


# ---------------------------------------------------------------- storage
def baseline(pid: int) -> Optional[dict]:
    """The last XMP state LPL recorded for this photo: {"history": [...], "mtime": float} or None."""
    r = db.conn().execute("SELECT history, xmp_mtime FROM edit_baseline WHERE photo_id=?", (pid,)).fetchone()
    if not r:
        return None
    try:
        return {"history": json.loads(r["history"]), "mtime": r["xmp_mtime"] or 0}
    except ValueError:
        return None


def set_baseline(pid: int, hist: list, mtime: Optional[float]) -> None:
    """Caller holds db.write_lock and commits."""
    db.conn().execute(
        "INSERT INTO edit_baseline(photo_id, history, xmp_mtime) VALUES(?,?,?) ON CONFLICT(photo_id) DO UPDATE SET "
        "history=excluded.history, xmp_mtime=excluded.xmp_mtime", (pid, json.dumps(hist), mtime or 0))


def record(pid: int, hist: list, prev: Optional[list], source: str, agent_id: Optional[str] = None,
           ts: Optional[float] = None, mtime: Optional[float] = None) -> int:
    """Store an edit event and make hist the photo's baseline. source: "agent", "outside", "import" (sent from a
    PC's local library) or "local" (darktable on the PC of a local library).
    Caller holds db.write_lock and commits."""
    c = db.conn()
    device = device_os = None
    if agent_id:
        a = c.execute("SELECT name, os FROM agents WHERE id=?", (agent_id,)).fetchone()
        if a:
            device, device_os = a["name"], a["os"]
    changes = diff(prev, hist)
    stored_changes = None if changes is None else {k: [_store_module(m) for m in v] for k, v in changes.items()}
    t = time.time()
    ts = min(ts, t) if ts else t  # a file time from a PC with its clock ahead must not sort after later edits
    cur = c.execute(
        "INSERT INTO edit_events(photo_id, ts, source, agent_id, device, device_os, modules, changes) VALUES(?,?,?,?,?,?,?,?)",
        (pid, ts, source, agent_id, device, device_os, json.dumps([_store_module(m) for m in hist]),
         None if stored_changes is None else json.dumps(stored_changes)))
    set_baseline(pid, hist, mtime)
    return cur.lastrowid


def observe_scan(pid: int, x: dict) -> Optional[int]:
    """The scanner read this photo's XMP (x from xmp.read_for_image). Records "edited outside LPL" when the
    modules differ from the last recorded state and the file is newer than it. Without an earlier record the
    state is only remembered: what changed cannot be known, and the change may be a rating only.
    Caller holds db.write_lock and commits."""
    hist = x.get("history")
    if hist is None:
        return None
    mtime = x.get("mtime") or 0
    base = baseline(pid)
    if base is None:
        set_baseline(pid, hist, mtime)
        return None
    if mtime <= base["mtime"] + 0.001:
        return None  # read before a newer XMP was recorded (an agent posted meanwhile)
    if signature(hist) == signature(base["history"]):
        set_baseline(pid, hist, mtime)  # rating, labels, tags or LPL's own write: not an edit
        return None
    # a local library (LPL on a PC) has no agents: darktable on that PC edits the photos where they are
    return record(pid, hist, base["history"], "local" if config.local else "outside", ts=mtime, mtime=mtime)


def event_dict(r) -> dict:
    modules = json.loads(r["modules"] or "[]")
    changes = json.loads(r["changes"]) if r["changes"] else None
    return {
        "id": r["id"], "photo_id": r["photo_id"], "time": r["ts"], "source": r["source"], "agent_id": r["agent_id"],
        "device": r["device"], "device_os": r["device_os"],
        "modules": [public(m) for m in modules],
        "changes": None if changes is None else {k: [public(m) for m in v] for k, v in changes.items()},
        "summary": summary(changes, modules),
    }


def events(pid: int, limit: int = 200) -> list:
    rows = db.conn().execute("SELECT * FROM edit_events WHERE photo_id=? ORDER BY ts DESC, id DESC LIMIT ?", (pid, limit)).fetchall()
    return [event_dict(r) for r in rows]


def last(pid: int) -> Optional[dict]:
    e = events(pid, 1)
    return e[0] if e else None
