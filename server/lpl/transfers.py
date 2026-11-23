# SPDX-License-Identifier: GPL-3.0-or-later
"""Batches of photos on their way to a device: what is known up front, how far it is, Stop, Resume, "Don't send".

Every "edit" job is a batch: the photos it names, each with its size, so a device's card can say
"Sending 38 of 120 photos to Laptop · 1.4 GB of 4.6 GB · about 6 min left" before the first file is there.

A device takes part only as far as it says it can, in its heartbeat (`caps`):
  batch   it reports how far each edit job is (`transfers` in the heartbeat)
  cancel  it stops a job, or leaves out single photos, when the heartbeat answer says so (`cancel`)
  open    it honours `open: false` in an edit job's payload (copy, but do not open darktable)
A device without `caps` (the darktable phone app today, PC agents from before) gets and sends exactly what it
always did; for it LPL only knows which photos wait in a job it has not taken yet, and can take those back.

The contract for devices is written down in docs/devices-stop-estimate.md. Nothing here imports api.py: api.py
calls register / on_heartbeat / on_done / decorate at four places and mounts `router`."""
import json
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    job_id INTEGER PRIMARY KEY,        -- the edit job this batch is
    agent_id TEXT,                     -- the device; NULL = any PC, until one takes the job
    run INTEGER NOT NULL,              -- batches that add up to one overall figure share a run (its first job's id)
    state TEXT NOT NULL,               -- queued / running / stopping / stopped / done / cancelled / resumed / legacy
    created REAL,
    started REAL,
    ended REAL,
    cancel INTEGER DEFAULT 0,          -- Stop was asked for: told to the device in its heartbeat answers until it confirms
    reason TEXT DEFAULT '',            -- why it stopped: user / interrupted (the device lost the job, e.g. it restarted)
    dismissed INTEGER DEFAULT 0,       -- the "Stopped" note was closed
    cur_photo INTEGER,                 -- the photo being copied now, and how many of its bytes are there
    cur_bytes INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_transfers_agent ON transfers(agent_id, state);
CREATE TABLE IF NOT EXISTS transfer_items (
    job_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    pos INTEGER NOT NULL,              -- place in the batch
    filename TEXT,
    size INTEGER DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'waiting',
    PRIMARY KEY (job_id, photo_id)
);
"""
# transfer_items.state:
#   waiting   not copied yet
#   skipping  "Don't send" was pressed while its job runs; the device has not confirmed yet
#   arrived   copied to the device by this batch
#   present   was on the device already; nothing was copied
#   failed    the device could not copy it
#   skipped   taken out with "Don't send"
#   unsent    not copied because the batch was stopped (Resume sends these)
#   dup       waits in an earlier batch for the same device already (counted there)
#   retried   was failed; Retry sent it again in a new batch
#   cleared   was failed; "Clear from queue" took it off the Devices page

LIVE = ("queued", "running", "stopping")
SAMPLE_SECONDS = 90          # how far back the progress samples for the estimate go
CAPS_KNOWN = ("batch", "cancel", "open")

router = APIRouter()
_samples: Dict[str, Dict[str, Any]] = {}   # agent id -> {"run": id, "points": deque of [time, bytes done, photos done]}
_samples_lock = threading.Lock()


def migrate(c) -> None:
    """New tables and agents.caps; called by db.migrate at every start, changes nothing the second time."""
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(agents)").fetchall()}
    if "caps" not in cols:  # what the device said it can do, as a JSON list; NULL = nothing (devices from before)
        c.execute("ALTER TABLE agents ADD COLUMN caps TEXT")


# ---------------------------------------------------------------- small helpers
def _ints(v) -> List[int]:
    out, seen = [], set()
    for x in v if isinstance(v, list) else []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if isinstance(x, bool) or i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def clean_caps(v) -> List[str]:
    if not isinstance(v, list):
        return []
    return [x for x in dict.fromkeys(str(s).strip().lower()[:24] for s in v if isinstance(s, str)) if x][:16]


def caps_of(c, aid: str) -> List[str]:
    r = c.execute("SELECT caps FROM agents WHERE id=?", (aid,)).fetchone()
    try:
        return clean_caps(json.loads((r["caps"] if r else None) or "[]"))
    except ValueError:
        return []


def _auto_open() -> bool:
    """Settings > Devices & library > "Open photos in darktable when they have arrived" (on unless switched off)."""
    try:
        s = json.loads(db.get_setting("pref.settings") or "null")
    except ValueError:
        s = None
    return not (isinstance(s, dict) and s.get("devicesAutoOpen") is False)


def _live(c, aid: str) -> list:
    return c.execute("SELECT * FROM transfers WHERE agent_id=? AND state IN ('queued','running','stopping') ORDER BY job_id",
                     (aid,)).fetchall()


def _set_items(c, job_id: int, ids, frm: tuple, to: str) -> None:
    for pid in ids:
        c.execute("UPDATE transfer_items SET state=? WHERE job_id=? AND photo_id=? AND state IN (%s)" % ",".join("?" * len(frm)),
                  (to, job_id, pid) + frm)


# ---------------------------------------------------------------- a job is made
def register(c, job_id: int, agent_id: Optional[str], payload: Dict[str, Any], run: Optional[int] = None) -> None:
    """Remember an edit job's photos as a batch. Caller holds db.write_lock and commits."""
    ids = _ints((payload or {}).get("ids"))
    if not ids:
        return
    now = time.time()
    live = _live(c, agent_id) if agent_id else []
    if run is None:
        run = live[0]["run"] if live else job_id
    waiting = set()
    if live:
        waiting = {r["photo_id"] for r in c.execute(
            "SELECT photo_id FROM transfer_items WHERE state IN ('waiting','skipping') AND job_id IN (%s)"
            % ",".join("?" * len(live)), [t["job_id"] for t in live]).fetchall()}
    c.execute("INSERT OR REPLACE INTO transfers(job_id, agent_id, run, state, created) VALUES(?,?,?,'queued',?)",
              (job_id, agent_id, run, now))
    known = {r["id"]: r for r in c.execute("SELECT id, filename, size FROM photos WHERE id IN (%s)" % ",".join("?" * len(ids)), ids).fetchall()}
    for pos, pid in enumerate(ids):
        p = known.get(pid)
        c.execute("INSERT OR REPLACE INTO transfer_items(job_id, photo_id, pos, filename, size, state) VALUES(?,?,?,?,?,?)",
                  (job_id, pid, pos, p["filename"] if p else "", int((p["size"] if p else 0) or 0),
                   "dup" if pid in waiting else "waiting"))


# ---------------------------------------------------------------- what a device reports
def _apply_report(c, t, rep: Dict[str, Any]) -> None:
    """Item states from a device's report of one job (lists of photo ids; all optional, all cumulative)."""
    j = t["job_id"]
    _set_items(c, j, _ints(rep.get("arrived")), ("waiting", "skipping", "unsent", "failed"), "arrived")
    _set_items(c, j, _ints(rep.get("present")), ("waiting", "skipping", "unsent", "failed"), "present")
    _set_items(c, j, _ints(rep.get("failed")), ("waiting", "skipping"), "failed")
    _set_items(c, j, _ints(rep.get("skipped")), ("waiting", "skipping"), "skipped")


def _finish(c, t, how: str, reason: str = "") -> str:
    """End a batch: how = stopped (what was not copied can be resumed) or done. Returns the state it got."""
    j, now = t["job_id"], time.time()
    c.execute("UPDATE transfer_items SET state='skipped' WHERE job_id=? AND state='skipping'", (j,))
    if how == "stopped":
        c.execute("UPDATE transfer_items SET state='unsent' WHERE job_id=? AND state='waiting'", (j,))
        left = c.execute("SELECT COUNT(*) n FROM transfer_items WHERE job_id=? AND state='unsent'", (j,)).fetchone()["n"]
        state = "stopped" if left else "done"   # nothing left to send: it simply finished
    else:
        c.execute("UPDATE transfer_items SET state='failed' WHERE job_id=? AND state='waiting'", (j,))
        left = c.execute("SELECT COUNT(*) n FROM transfer_items WHERE job_id=? AND state='unsent'", (j,)).fetchone()["n"]
        state = "stopped" if left else "done"   # (a batch stopped earlier whose device reports late keeps its rest)
    c.execute("UPDATE transfers SET state=?, ended=?, reason=CASE WHEN ?='stopped' THEN ? ELSE reason END, cur_photo=NULL, cur_bytes=0 WHERE job_id=?",
              (state, now, state, reason or t["reason"] or "user", j))
    return state


def _reconcile(c, t, working_set) -> None:
    """A device lost a job (it restarted): photos of the batch that are in its working set did arrive."""
    have = {w.get("id") for w in working_set or [] if isinstance(w, dict) and w.get("status") not in ("downloading", "error")}
    ids = [r["photo_id"] for r in c.execute("SELECT photo_id FROM transfer_items WHERE job_id=? AND state IN ('waiting','skipping')",
                                             (t["job_id"],)).fetchall() if r["photo_id"] in have]
    _set_items(c, t["job_id"], ids, ("waiting", "skipping"), "arrived")


def _run_progress(c, aid: str):
    """(run, bytes done, photos done) of the device's live run, or None."""
    live = _live(c, aid)
    if not live:
        return None
    run = live[0]["run"]
    r = c.execute("SELECT COALESCE(SUM(CASE WHEN i.state='arrived' THEN i.size ELSE 0 END),0) b,"
                  " COALESCE(SUM(CASE WHEN i.state IN ('arrived','present') THEN 1 ELSE 0 END),0) n"
                  " FROM transfer_items i JOIN transfers t ON t.job_id=i.job_id WHERE t.agent_id=? AND t.run=?"
                  " AND t.state NOT IN ('cancelled','legacy')", (aid, run)).fetchone()
    cur = sum(t["cur_bytes"] or 0 for t in live if t["state"] in ("running", "stopping"))
    return run, int(r["b"]) + int(cur), int(r["n"])


def on_heartbeat(c, aid: str, body: Dict[str, Any], out: List[dict], now: float) -> Dict[str, Any]:
    """Called by api.heartbeat inside its transaction, after it chose the jobs to hand out (`out`, which this
    may change). Returns what to add to the answer: nothing for a device without caps."""
    caps = clean_caps(body.get("caps"))
    c.execute("UPDATE agents SET caps=? WHERE id=?", (json.dumps(caps) if caps else None, aid))
    batch = "batch" in caps
    reports = [r for r in (body.get("transfers") or []) if isinstance(r, dict)] if batch and isinstance(body.get("transfers"), list) else []
    listed = {}
    for rep in reports:
        try:
            listed[int(rep.get("job"))] = rep
        except (TypeError, ValueError):
            pass

    # jobs handed out now
    for j in list(out):
        if j.get("kind") != "edit":
            continue
        t = c.execute("SELECT * FROM transfers WHERE job_id=?", (j["id"],)).fetchone()
        if not t:
            continue
        if not batch:   # a device from before: from here on only it knows how far the job is
            if t["state"] == "queued":
                c.execute("UPDATE transfers SET state='legacy', agent_id=?, started=?, ended=? WHERE job_id=?", (aid, now, now, j["id"]))
            continue
        if j["id"] in listed:   # handed out again (never reported done for a long time) while the device still has it
            out.remove(j)
            continue
        if t["state"] == "queued":
            live = [x for x in _live(c, aid) if x["job_id"] != j["id"]]
            c.execute("UPDATE transfers SET state='running', agent_id=?, started=?, run=? WHERE job_id=?",
                      (aid, now, live[0]["run"] if live else t["run"], j["id"]))
        j["payload"] = dict(j.get("payload") or {}, ids=_ints((j.get("payload") or {}).get("ids")))  # each photo once
        if "open" in caps:
            j["payload"] = dict(j["payload"], open=_auto_open())
    if not batch:
        return {}

    # how far the device says its jobs are
    handed = {j["id"] for j in out}
    for jid, rep in listed.items():
        t = c.execute("SELECT * FROM transfers WHERE job_id=? AND agent_id=?", (jid, aid)).fetchone()
        if not t or t["state"] in ("legacy", "cancelled"):
            continue
        _apply_report(c, t, rep)
        state = str(rep.get("state") or "")
        if state in ("stopped", "done", "failed"):
            if t["state"] in LIVE or t["state"] == "stopped":
                _finish(c, t, "stopped" if state == "stopped" else "done")
            c.execute("UPDATE jobs SET status=? WHERE id=? AND status='taken'", (state, jid))
            continue
        if t["state"] in ("running", "stopping"):
            ph, by = rep.get("photo"), rep.get("bytes")
            ph = ph if isinstance(ph, int) and not isinstance(ph, bool) else None
            by = int(by) if isinstance(by, (int, float)) and not isinstance(by, bool) and by == by and by > 0 else 0
            if ph is not None:
                size = c.execute("SELECT size FROM transfer_items WHERE job_id=? AND photo_id=?", (jid, ph)).fetchone()
                by = min(by, int(size["size"] or 0)) if size and size["size"] else by
            c.execute("UPDATE transfers SET cur_photo=?, cur_bytes=? WHERE job_id=?", (ph, by if ph is not None else 0, jid))
        # a sign of life: the job is not handed out again while the device works on it (JOB_RETRY_SECONDS)
        c.execute("UPDATE jobs SET taken=? WHERE id=? AND status='taken'", (now, jid))

    # jobs the device should have but does not list: it lost them (restarted, or the answer never reached it)
    for t in _live(c, aid):
        if t["state"] == "queued" or t["job_id"] in listed or t["job_id"] in handed:
            continue
        _reconcile(c, t, (body.get("state") or {}).get("working_set") if isinstance(body.get("state"), dict) else None)
        _finish(c, t, "stopped", "user" if t["cancel"] else "interrupted")
        c.execute("UPDATE jobs SET status='stopped' WHERE id=? AND status='taken'", (t["job_id"],))

    # samples for the time-left estimate
    p = _run_progress(c, aid)
    with _samples_lock:
        if p is None:
            _samples.pop(aid, None)
        else:
            s = _samples.get(aid)
            if not s or s["run"] != p[0]:
                s = _samples[aid] = {"run": p[0], "points": deque()}
            s["points"].append([round(now, 2), p[1], p[2]])
            while s["points"] and s["points"][0][0] < now - SAMPLE_SECONDS:
                s["points"].popleft()

    if "cancel" not in caps:
        return {}
    # what the device must stop: said again in every answer until it confirms (so a lost answer loses nothing)
    jobs = [t["job_id"] for t in c.execute(
        "SELECT job_id, state FROM transfers WHERE agent_id=? AND cancel=1 AND state IN ('running','stopping','stopped')", (aid,)).fetchall()
        if t["state"] != "stopped" or (t["job_id"] in listed and str(listed[t["job_id"]].get("state") or "") not in ("stopped", "done", "failed"))]
    photos = [r["photo_id"] for r in c.execute(
        "SELECT DISTINCT i.photo_id FROM transfer_items i JOIN transfers t ON t.job_id=i.job_id"
        " WHERE t.agent_id=? AND t.state IN ('running','stopping') AND i.state='skipping' ORDER BY i.photo_id", (aid,)).fetchall()]
    return {"cancel": {"jobs": jobs, "photos": photos}}


def on_done(c, jid: int, body: Dict[str, Any]) -> None:
    """POST /api/jobs/{id}/done. A device with caps may send the same lists as in a heartbeat report."""
    t = c.execute("SELECT * FROM transfers WHERE job_id=?", (jid,)).fetchone()
    if not t or t["state"] in ("legacy", "cancelled", "resumed", "done"):
        return
    _apply_report(c, t, body or {})
    _finish(c, t, "stopped" if (body or {}).get("status") == "stopped" else "done")


# ---------------------------------------------------------------- what the web app shows
def _items(c, jobs: List[int]) -> list:
    if not jobs:
        return []
    return c.execute("SELECT * FROM transfer_items WHERE job_id IN (%s) ORDER BY job_id, pos" % ",".join("?" * len(jobs)), jobs).fetchall()


def _summary(c, a: dict, caps: List[str]) -> Dict[str, Any]:
    aid = a["id"]
    out: Dict[str, Any] = {"transfer": None, "stopped": None}
    live = _live(c, aid)
    live_run = live[0]["run"] if live else None
    if live:
        members = c.execute("SELECT * FROM transfers WHERE agent_id=? AND run=? AND state NOT IN ('cancelled','legacy') ORDER BY job_id",
                            (aid, live_run)).fetchall()
        items = _items(c, [t["job_id"] for t in members])
        livejobs = {t["job_id"] for t in live}
        running = [t for t in live if t["state"] in ("running", "stopping")]
        cur = next((t for t in running if t["cur_photo"] is not None), None) if a["online"] else None
        cur_item = next((i for i in items if cur and i["job_id"] == cur["job_id"] and i["photo_id"] == cur["cur_photo"]
                         and i["state"] == "waiting"), None)
        done = [i for i in items if i["state"] in ("arrived", "present")]
        waiting = [i for i in items if i["state"] == "waiting" and i["job_id"] in livejobs]
        failed = [i for i in items if i["state"] == "failed"]
        cur_bytes = int(cur["cur_bytes"] or 0) if cur_item else 0
        state = "stopping" if any(t["state"] == "stopping" for t in live) else "running" if running else "queued"
        with _samples_lock:
            s = _samples.get(aid)
            points = [list(p) for p in s["points"]] if s and s["run"] == live_run and a["online"] else []
        out["transfer"] = {
            "run": live_run, "state": state,
            "photos_total": len(done) + len(waiting) + len(failed), "photos_done": len(done), "failed": len(failed),
            "bytes_total": sum(i["size"] or 0 for i in items if i["state"] == "arrived") + sum(i["size"] or 0 for i in waiting),
            "bytes_done": sum(i["size"] or 0 for i in items if i["state"] == "arrived") + cur_bytes,
            "current": {"id": cur_item["photo_id"], "filename": cur_item["filename"], "bytes": cur_bytes, "size": cur_item["size"] or 0} if cur_item else None,
            "waiting": [{"id": i["photo_id"], "filename": i["filename"], "job": i["job_id"]} for i in waiting if i is not cur_item],
            "arrived": [i["photo_id"] for i in items if i["state"] == "arrived"],
            "started": min([t["started"] for t in running if t["started"]] or [min(t["created"] or 0 for t in live)]),
            "samples": points,
            # Stop for a running copy needs the device's word that it can; jobs it has not taken are stopped here
            "can_stop": state == "queued" or (state == "running" and "cancel" in caps),
            "can_skip": state == "queued" or "cancel" in caps,
            "known": state == "queued" or "batch" in caps,
        }
    st = c.execute("SELECT * FROM transfers WHERE agent_id=? AND state='stopped' AND dismissed=0 ORDER BY ended DESC, job_id DESC", (aid,)).fetchall()
    st = [t for t in st if t["run"] != live_run]
    if st:
        run = st[0]["run"]
        members = c.execute("SELECT * FROM transfers WHERE agent_id=? AND run=? AND state NOT IN ('cancelled','legacy') ORDER BY job_id",
                            (aid, run)).fetchall()
        items = _items(c, [t["job_id"] for t in members])
        stopped_jobs = {t["job_id"] for t in members if t["state"] == "stopped" and not t["dismissed"]}
        done = [i for i in items if i["state"] in ("arrived", "present")]
        unsent = [i for i in items if i["state"] == "unsent" and i["job_id"] in stopped_jobs]
        failed = [i for i in items if i["state"] == "failed"]
        out["stopped"] = {
            "run": run, "reason": st[0]["reason"] or "user", "time": st[0]["ended"],
            "photos_total": len(done) + len(unsent) + len(failed), "photos_done": len(done), "failed": len(failed),
            "bytes_done": sum(i["size"] or 0 for i in items if i["state"] == "arrived"),
            "bytes_total": sum(i["size"] or 0 for i in items if i["state"] == "arrived") + sum(i["size"] or 0 for i in unsent),
            "unsent": [{"id": i["photo_id"], "filename": i["filename"], "job": i["job_id"]} for i in unsent],
            "arrived": [i["photo_id"] for i in items if i["state"] == "arrived"],
        }
    return out


def folds() -> Dict[str, bool]:
    try:
        v = json.loads(db.get_setting("pref.devices.fold") or "{}")
    except ValueError:
        v = {}
    return {str(k): bool(x) for k, x in v.items()} if isinstance(v, dict) else {}


def decorate(agents: List[dict]) -> None:
    """Called by api.list_agents: adds caps, transfer, stopped and folded to each device."""
    c = db.conn()
    fold = folds()
    for a in agents:
        try:
            caps = clean_caps(json.loads(a.pop("caps", None) or "[]"))
        except ValueError:
            caps = []
        a["caps"] = caps
        a["folded"] = fold.get(a["id"])   # true / false as the user left it; null = never chosen
        a["folded_phone"] = fold.get("phone:" + a["id"])   # the same for the phone layout, which folds by itself
        a.update(_summary(c, a, caps))
        a["failed"] = failed_items(c, a["id"])


def failed_items(c, aid: str) -> list:
    """Photos this device could not copy, in any batch that is not over and done with (newest first, each once),
    until Retry or "Clear from queue". Before, a failed photo was only a number in a batch that had ended."""
    rows = c.execute("SELECT i.photo_id, i.filename, i.job_id, COALESCE(t.ended, t.started, t.created) ts FROM transfer_items i"
                     " JOIN transfers t ON t.job_id=i.job_id WHERE t.agent_id=? AND i.state='failed'"
                     " AND t.state NOT IN ('cancelled','legacy') ORDER BY i.job_id DESC LIMIT 500", (aid,)).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["photo_id"] not in seen:
            seen.add(r["photo_id"])
            out.append({"id": r["photo_id"], "filename": r["filename"], "job": r["job_id"], "time": r["ts"]})
    return out


# ---------------------------------------------------------------- what the web app can ask for
def _agent(c, aid: str):
    r = c.execute("SELECT id, last_seen FROM agents WHERE id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404, "device not found")
    return r


@router.post("/api/agents/{aid}/stop")
def stop(aid: str):
    """Stop sending photos to this device. What arrived stays; the rest can be resumed. Nothing running: nothing happens."""
    from .api import AGENT_ONLINE_SECONDS
    n = 0
    with db.write_lock:
        c = db.conn()
        a = _agent(c, aid)
        online = time.time() - (a["last_seen"] or 0) < AGENT_ONLINE_SECONDS
        caps = caps_of(c, aid)
        live = _live(c, aid)
        if any(t["state"] == "running" for t in live) and online and "cancel" not in caps:
            raise HTTPException(409, "this device cannot stop a copy that is under way")
        for t in live:
            n += 1
            if t["state"] == "queued":   # not taken yet: it never starts
                c.execute("UPDATE jobs SET status='stopped' WHERE id=? AND status='queued'", (t["job_id"],))
                _finish(c, t, "stopped", "user")
            elif online:                 # the device is told in its next heartbeat answer and confirms
                c.execute("UPDATE transfers SET cancel=1, state='stopping' WHERE job_id=?", (t["job_id"],))
            else:                        # not reachable: stopped as far as LPL goes; it is told when it is back
                c.execute("UPDATE transfers SET cancel=1 WHERE job_id=?", (t["job_id"],))
                _finish(c, c.execute("SELECT * FROM transfers WHERE job_id=?", (t["job_id"],)).fetchone(), "stopped", "user")
                c.execute("UPDATE jobs SET status='stopped' WHERE id=? AND status='taken'", (t["job_id"],))
        c.commit()
    return {"ok": True, "stopped": n}


@router.post("/api/agents/{aid}/resume")
def resume(aid: str):
    """Send what a stopped batch did not send: a new edit job for this device with those photos."""
    with db.write_lock:
        c = db.conn()
        _agent(c, aid)
        st = c.execute("SELECT * FROM transfers WHERE agent_id=? AND state='stopped' AND dismissed=0 ORDER BY job_id", (aid,)).fetchall()
        if not st:
            return {"ok": True, "id": None, "photos": 0}
        run = max(st, key=lambda t: (t["ended"] or 0, t["job_id"]))["run"]
        st = [t for t in st if t["run"] == run]
        ids = [i["photo_id"] for i in _items(c, [t["job_id"] for t in st]) if i["state"] == "unsent"]
        ids = list(dict.fromkeys(ids))
        for t in st:
            c.execute("UPDATE transfers SET state='resumed' WHERE job_id=?", (t["job_id"],))
        jid = None
        if ids:
            payload = {"ids": ids}
            cur = c.execute("INSERT INTO jobs(agent_id,kind,payload,created) VALUES(?,?,?,?)", (aid, "edit", json.dumps(payload), time.time()))
            jid = cur.lastrowid
            live = _live(c, aid)
            register(c, jid, aid, payload, run=live[0]["run"] if live else run)
            if live:  # one overall figure: what arrived before the stop counts in the run that is under way
                c.execute("UPDATE transfers SET run=? WHERE agent_id=? AND run=?", (live[0]["run"], aid, run))
        c.commit()
    return {"ok": True, "id": jid, "photos": len(ids)}


@router.post("/api/agents/{aid}/dont-send")
def dont_send(aid: str, body: Dict[str, Any] = Body(...)):
    """Take single photos out of what is still to be sent to this device (waiting, or left over after a stop)."""
    ids = set(_ints(body.get("ids")))
    n = 0
    with db.write_lock:
        c = db.conn()
        _agent(c, aid)
        caps = caps_of(c, aid)
        ts = c.execute("SELECT * FROM transfers WHERE agent_id=? AND (state IN ('queued','running','stopping') OR (state='stopped' AND dismissed=0))"
                       " ORDER BY job_id", (aid,)).fetchall()
        for t in ts:
            j = t["job_id"]
            mine = [i["photo_id"] for i in _items(c, [j]) if i["photo_id"] in ids and i["state"] in ("waiting", "unsent")]
            if not mine:
                continue
            if t["state"] == "queued":
                _set_items(c, j, mine, ("waiting",), "skipped")
                left = [i["photo_id"] for i in _items(c, [j]) if i["state"] in ("waiting", "dup")]
                row = c.execute("SELECT payload FROM jobs WHERE id=? AND status='queued'", (j,)).fetchone()
                if row:
                    payload = json.loads(row["payload"] or "{}")
                    payload["ids"] = [p for p in _ints(payload.get("ids")) if p in set(left)]
                    c.execute("UPDATE jobs SET payload=?, status=? WHERE id=?", (json.dumps(payload), "queued" if payload["ids"] else "cancelled", j))
                    if not payload["ids"]:
                        c.execute("UPDATE transfers SET state='cancelled', ended=? WHERE job_id=?", (time.time(), j))
            elif t["state"] == "stopped":
                _set_items(c, j, mine, ("unsent",), "skipped")
                if not any(i["state"] == "unsent" for i in _items(c, [j])):
                    c.execute("UPDATE transfers SET state='done' WHERE job_id=?", (j,))
            else:
                if "cancel" not in caps:
                    raise HTTPException(409, "this device cannot leave out photos of a copy that is under way")
                _set_items(c, j, mine, ("waiting",), "skipping")   # the device is told in its heartbeat answer
            n += len(mine)
        c.commit()
    return {"ok": True, "photos": n}


def _working_errors(c, aid: str, ids) -> List[int]:
    """Of these photos, the ones the device lists as a copy that failed (status error, no edits of its own)."""
    r = c.execute("SELECT state FROM agents WHERE id=?", (aid,)).fetchone()
    try:
        ws = (json.loads((r["state"] if r else None) or "{}") or {}).get("working_set") or []
    except ValueError:
        ws = []
    want = set(ids)
    return [w["id"] for w in ws if isinstance(w, dict) and w.get("id") in want and w.get("status") == "error"
            and (w.get("copy_failed") or "sync" not in str(w.get("error") or "").lower())]


@router.post("/api/agents/{aid}/retry")
def retry(aid: str, body: Dict[str, Any] = Body(...)):
    """Send photos that could not be copied again: one new batch for this device. A device from before this does
    not copy again a photo it lists with an error; the agent from 23 Nov 2026 on does."""
    ids = _ints(body.get("ids"))
    if not ids:
        raise HTTPException(400, "ids required")
    with db.write_lock:
        c = db.conn()
        _agent(c, aid)
        for pid in ids:
            c.execute("UPDATE transfer_items SET state='retried' WHERE photo_id=? AND state='failed' AND job_id IN"
                      " (SELECT job_id FROM transfers WHERE agent_id=?)", (pid, aid))
        payload = {"ids": ids}
        jid = c.execute("INSERT INTO jobs(agent_id,kind,payload,created) VALUES(?,?,?,?)", (aid, "edit", json.dumps(payload), time.time())).lastrowid
        live = _live(c, aid)
        register(c, jid, aid, payload, run=live[0]["run"] if live else None)
        c.commit()
    return {"ok": True, "id": jid, "photos": len(ids)}


@router.post("/api/agents/{aid}/clear")
def clear(aid: str, body: Dict[str, Any] = Body(...)):
    """"Clear from queue": photos that could not be copied leave the Devices page. Nothing of them is on the device
    (what a failed copy left there, the device deletes: a "forget" job). Photos with edits are never touched here."""
    ids = _ints(body.get("ids"))
    if not ids:
        raise HTTPException(400, "ids required")
    with db.write_lock:
        c = db.conn()
        _agent(c, aid)
        n = 0
        for pid in ids:
            n += c.execute("UPDATE transfer_items SET state='cleared' WHERE photo_id=? AND state='failed' AND job_id IN"
                           " (SELECT job_id FROM transfers WHERE agent_id=?)", (pid, aid)).rowcount
        there = _working_errors(c, aid, ids)
        jid = None
        if there:
            jid = c.execute("INSERT INTO jobs(agent_id,kind,payload,created) VALUES(?,?,?,?)",
                            (aid, "forget", json.dumps({"ids": there, "mode": "failed"}), time.time())).lastrowid
        c.commit()
    return {"ok": True, "cleared": n, "on_device": len(there), "id": jid}


@router.post("/api/agents/{aid}/dismiss")
def dismiss(aid: str):
    """Close the "Stopped" note of this device (what arrived stays where it is)."""
    with db.write_lock:
        c = db.conn()
        c.execute("UPDATE transfers SET dismissed=1 WHERE agent_id=? AND state='stopped'", (aid,))
        c.commit()
    return {"ok": True}


@router.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: int):
    """Take back a job no device has taken yet (any kind, any device, also one waiting for "a PC")."""
    with db.write_lock:
        c = db.conn()
        r = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            raise HTTPException(404, "job not found")
        if r["status"] != "queued":
            raise HTTPException(409, "a device has taken this job already")
        c.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (jid,))
        c.execute("UPDATE transfers SET state='cancelled', ended=? WHERE job_id=? AND state='queued'", (time.time(), jid))
        c.commit()
    return {"ok": True}


@router.put("/api/devices/fold")
def fold_set(body: Dict[str, Any] = Body(...)):
    """Remember which devices are folded on the Devices page: {"folds": {"<device id>": true | false | null}},
    merged into what is stored (null forgets a choice), so two browsers do not undo each other. The phone
    layout keeps its own choices under "phone:<device id>" (a small screen starts with everything folded)."""
    v = body.get("folds")
    if not isinstance(v, dict) or len(v) > 500:
        raise HTTPException(400, "folds must be an object")
    with db.write_lock:
        cur = folds()
        for k, x in v.items():
            if x is None:
                cur.pop(str(k)[:90], None)
            else:
                cur[str(k)[:90]] = bool(x)
        db.set_setting("pref.devices.fold", json.dumps(cur))
    return {"folds": cur}
