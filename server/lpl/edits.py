"""Who edited a photo last, and what changed (R15 of the darktable Android plan).

Every XMP that an agent posts is recorded as an edit event: the device, the time, the darktable modules the
edit uses, and what changed against the XMP LPL had before. A changed XMP that the scanner finds and no agent
posted is recorded as edited outside LPL. Only modules are compared (operation and instance, on or off, a
fingerprint of the settings); settings are never decoded.

Display names of modules come from resources/module-names.json next to this file (operation -> English name,
generated in the darktable-android repository as schema/module-names.json). Without the file, or for an
operation it does not list, the operation itself is shown.

Modules darktable applies by itself when a photo is first opened (raw black/white point, demosaic, input and
output color profile, the workflow's default exposure and tone mapper, …) are marked "auto" and left out of the
short summary when there is nothing to compare with; see automatic(). That uses resources/module-flags.json
(operation -> {"default_enabled": bool, "hidden": bool}, generated from the darktable-android repository's
schema/ui-schema.json); without it only darktable's own presets (instance names starting "_builtin_") are found.

Saves of one device on one photo on one day are one entry (see record()); the day is the server's local day,
in the time zone of the TZ setting (config.tz).
"""
import datetime
import json
import logging
import time
from pathlib import Path
from typing import Optional

from . import db
from .config import config

log = logging.getLogger("lpl.edits")

RESOURCES = Path(__file__).resolve().parent / "resources"
MODULE_NAMES = RESOURCES / "module-names.json"
MODULE_FLAGS = RESOURCES / "module-flags.json"
SUMMARY_NAMES = 4  # names shown in the one-line summary before "and N more"
DEFAULT_EDIT = "darktable's default edit"

_names = {"mtime": None, "map": {}}
_flags = {"mtime": None, "map": {}}


def _load(path: Path, cache: dict, clean, what: str) -> dict:
    """A JSON object from resources/, re-read when the file changes; {} when it is missing or unreadable."""
    try:
        m = path.stat().st_mtime
    except OSError:
        cache.update(mtime=None, map={})
        return {}
    if (m, path) != (cache["mtime"], cache.get("path")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            cache["map"] = clean(data)
        except (OSError, ValueError) as e:
            log.warning("cannot read %s (%s): %s", path, e, what)
            cache["map"] = {}
        cache["mtime"], cache["path"] = m, path
    return cache["map"]


def module_names() -> dict:
    """operation -> English name."""
    return _load(MODULE_NAMES, _names, lambda d: {str(k): str(v) for k, v in d.items() if isinstance(v, str) and v.strip()},
                 "showing operation names")


def module_flags() -> dict:
    """operation -> {"default_enabled": bool, "hidden": bool} from module-flags.json."""
    return _load(MODULE_FLAGS, _flags, lambda d: {str(k): {"default_enabled": v.get("default_enabled") is True, "hidden": v.get("hidden") is True}
                                                  for k, v in d.items() if isinstance(v, dict)},
                 "only darktable's own presets are recognised as automatic")


def automatic(hist: list) -> set:
    """The module instances (operation, multi_priority) of hist that darktable applied by itself and nobody changed.

    When darktable first opens a photo it writes, at the start of the history and before anything the user does,
    one item for each module that is on by default (module-flags.json: default_enabled, e.g. raw black/white point,
    demosaic, white balance, input/output color profile, orientation), its internal modules (hidden: gamma, the
    mask manager) and one for each preset it applies automatically, which it names after the preset with darktable's
    "_builtin_" prefix (the workflow's exposure, color calibration, filmic/sigmoid, "auto" orientation).
    So: the leading run of history items that are each one of those, where no instance appears twice, is
    darktable's own; an instance whose last item lies in that run was not changed by the user. The first other
    item ends the run, and so does an instance's second item (darktable applies each one once).
    Hidden modules are always automatic. Events stored before items carried "first" give an empty set."""
    if not hist or any("first" not in m for m in hist):
        return set()
    flags = module_flags()
    if not flags:  # without the file the run cannot be told: only darktable's own presets, applied once
        return {(m["operation"], m["priority"]) for m in hist
                if (m.get("instance") or "").startswith("_builtin_") and m.get("second") is None}

    def own(m):
        f = flags.get(m["operation"], {})
        return f.get("default_enabled") or f.get("hidden") or (m.get("instance") or "").startswith("_builtin_")
    # where darktable's run ends: the first item of a module it would not apply, or any instance's second item
    ends = [m["first"] for m in hist if not own(m)] + [m["second"] for m in hist if m.get("second") is not None]
    end = min(ends) if ends else float("inf")
    auto = {(m["operation"], m["priority"]) for m in hist if own(m) and m.get("second") is None and m["first"] < end}
    return auto | {(m["operation"], m["priority"]) for m in hist if flags.get(m["operation"], {}).get("hidden")}


def module_name(op: str) -> str:
    return module_names().get(op) or op


def _instance_label(inst: str) -> str:
    # darktable names an instance after the preset that made it, "_builtin_" marking its own presets
    return inst[len("_builtin_"):] if inst.startswith("_builtin_") else inst


def public(m: dict, auto: Optional[set] = None) -> dict:
    """A module as the API shows it; auto (given for the modules of an edit): darktable applied it by itself."""
    name = module_name(m["operation"])
    inst = _instance_label(m.get("instance") or "")
    d = {"operation": m["operation"], "name": name, "instance": m.get("instance") or "", "priority": m.get("priority", 0),
         "enabled": bool(m.get("enabled")), "label": "%s %s" % (name, inst) if inst else name}
    if auto is not None:
        d["auto"] = (m["operation"], m.get("priority", 0)) in auto
    return d


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


def _store_full(m: dict) -> dict:
    """A module of an edit as stored: with where its items are in the history, for automatic() when it is read."""
    d = _store_module(m)
    for k in ("num", "first", "second"):
        if k in m:
            d[k] = m[k]
    return d


def touched(changes: Optional[dict], modules: list, auto: set = frozenset()) -> list:
    """The modules an edit is about: the ones it changed, else (nothing to compare with) the ones that are on,
    leaving out what darktable applied by itself."""
    if changes is None:
        return [m for m in modules if m["enabled"] and (m["operation"], m.get("priority", 0)) not in auto]
    return changes["changed"] + changes["added"] + changes["switched_on"] + changes["switched_off"] + changes["removed"]


def summary(changes: Optional[dict], modules: list, auto: set = frozenset()) -> str:
    if not modules and (changes is None or not touched(changes, modules)):
        return "no darktable edit"
    if changes is None and not touched(changes, modules, auto):
        return DEFAULT_EDIT
    names = []
    for m in touched(changes, modules, auto):
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


def day_of(ts: float) -> datetime.date:
    """The server's local calendar day of a time: in the zone of the TZ setting when it is set (and known),
    else the system's local time."""
    if config.tz:
        try:
            from zoneinfo import ZoneInfo
            return datetime.datetime.fromtimestamp(ts, ZoneInfo(config.tz)).date()
        except Exception as e:  # unknown zone, or no time-zone data on this system
            if not _tz_warned:
                log.warning("time zone %r not usable (%s): days follow the system's local time", config.tz, e)
                _tz_warned.append(1)
    return datetime.date.fromtimestamp(ts)


_tz_warned: list = []


def record(pid: int, hist: list, prev: Optional[list], source: str, agent_id: Optional[str] = None,
           ts: Optional[float] = None, mtime: Optional[float] = None) -> int:
    """Store an edit event and make hist the photo's baseline. source: "agent", "outside", "import" (sent from a
    PC's local library) or "local" (darktable on the PC of a local library).

    Saves of the same source and device (agent id) on the same day follow each other as one entry: when the
    photo's newest entry has the same source and agent id and is from the same day, it is updated: its time
    becomes this save's, saves goes up by one and changes are against the state before its first save (kept in
    "before"). Another device or source in between, or a new day, starts a new entry.
    Caller holds db.write_lock and commits. Returns the entry's id."""
    c = db.conn()
    device = device_os = None
    if agent_id:
        a = c.execute("SELECT name, os FROM agents WHERE id=?", (agent_id,)).fetchone()
        if a:
            device, device_os = a["name"], a["os"]
    t = time.time()
    ts = min(ts, t) if ts else t  # a file time from a PC with its clock ahead must not sort after later edits
    last = c.execute("SELECT * FROM edit_events WHERE photo_id=? ORDER BY ts DESC, id DESC LIMIT 1", (pid,)).fetchone()
    same = (last is not None and last["source"] == source and (last["agent_id"] or "") == (agent_id or "")
            and day_of(last["ts"]) == day_of(ts) and ts >= last["ts"])
    if same:
        try:
            before = json.loads(last["before"]) if last["before"] else None
        except ValueError:
            before = None
    else:
        before = prev
    changes = diff(before, hist)
    stored_changes = None if changes is None else json.dumps({k: [_store_module(m) for m in v] for k, v in changes.items()})
    modules = json.dumps([_store_full(m) for m in hist])
    if same:
        c.execute("UPDATE edit_events SET ts=?, device=COALESCE(?, device), device_os=COALESCE(?, device_os), modules=?, changes=?, "
                  "saves=COALESCE(saves, 1)+1 WHERE id=?", (ts, device, device_os, modules, stored_changes, last["id"]))
        rid = last["id"]
    else:
        rid = c.execute(
            "INSERT INTO edit_events(photo_id, ts, source, agent_id, device, device_os, modules, changes, first_ts, saves, before) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,?)",
            (pid, ts, source, agent_id, device, device_os, modules, stored_changes, ts,
             None if before is None else json.dumps(before))).lastrowid
    set_baseline(pid, hist, mtime)
    return rid


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
    auto = automatic(modules)
    keys = r.keys()
    return {
        "id": r["id"], "photo_id": r["photo_id"], "time": r["ts"], "source": r["source"], "agent_id": r["agent_id"],
        "device": r["device"], "device_os": r["device_os"],
        "modules": [public(m, auto) for m in modules],
        "changes": None if changes is None else {k: [public(m) for m in v] for k, v in changes.items()},
        "summary": summary(changes, modules, auto),
        # one entry can hold several saves of one device on one day: time is the last, first_time the first
        "first_time": (r["first_ts"] if "first_ts" in keys else None) or r["ts"],
        "saves": (r["saves"] if "saves" in keys else None) or 1,
    }


def events(pid: int, limit: int = 200) -> list:
    rows = db.conn().execute("SELECT * FROM edit_events WHERE photo_id=? ORDER BY ts DESC, id DESC LIMIT ?", (pid, limit)).fetchall()
    return [event_dict(r) for r in rows]


def last(pid: int) -> Optional[dict]:
    e = events(pid, 1)
    return e[0] if e else None
