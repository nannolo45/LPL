# SPDX-License-Identifier: GPL-3.0-or-later
"""Who edited last and what changed (R15): the darktable history read from real darktable XMPs, the edit
events recorded for XMP posts (with and without agent=), changes found by the scanner, and module names.
See docs/android-edit-record-progress.md."""
import json
import os
import re
import time
from pathlib import Path

import pytest

from lpl import db, edits, scanner, xmp

DATA = Path(__file__).resolve().parent / "fixtures" / "xmp"
PC = {"id": "pc-test", "name": "Desktop", "os": "windows"}
PHONE = {"id": "android-0000-test", "name": "Phone", "os": "android"}


def real(name: str) -> bytes:
    return (DATA / name).read_bytes()


def with_history_end(data: bytes, end: int) -> bytes:
    """darktable writes this when an earlier step of the history stack is selected: the items after it stay in the file."""
    out, n = re.subn(rb'darktable:history_end="\d+"', b'darktable:history_end="%d"' % end, data)
    assert n == 1
    return out


def without_history(data: bytes) -> bytes:
    """A sidecar of an image that was never opened in the darkroom: no darktable:history, history_end 0."""
    out, n = re.subn(rb"<darktable:history>.*?</darktable:history>", b"", data, flags=re.S)
    assert n == 1
    return with_history_end(out, 0)


def mods(data: bytes):
    return xmp.parse(data)["history"]


def ops(hist):
    return [(m["operation"], m["priority"], m["enabled"]) for m in hist]


# ---------------------------------------------------------------- reading darktable's history
def test_history_of_default_pipeline():
    h = mods(real("nop.xmp"))
    assert sorted(m["operation"] for m in h) == sorted(
        ["rawprepare", "temperature", "highlights", "demosaic", "colorin", "colorout", "gamma", "flip"])
    assert all(m["enabled"] and m["priority"] == 0 and m["instance"] == "" for m in h)
    assert h[0]["operation"] == "flip"  # most recently changed first


def test_history_one_module_added():
    d = edits.diff(mods(real("nop.xmp")), mods(real("exposure.xmp")))
    assert [m["operation"] for m in d["added"]] == ["exposure"]
    assert d["removed"] == d["changed"] == d["switched_on"] == d["switched_off"] == []


def test_history_end_before_the_last_item():
    data = real("exposure.xmp")
    assert "exposure" in [m["operation"] for m in mods(data)]
    cut = mods(with_history_end(data, 8))  # exposure is item 8: undone in the history stack
    assert "exposure" not in [m["operation"] for m in cut]
    assert edits.signature(cut) == edits.signature(mods(real("nop.xmp")))
    d = edits.diff(mods(data), cut)
    assert [m["operation"] for m in d["removed"]] == ["exposure"]
    assert edits.summary(d, cut) == "exposure"


def test_same_instance_twice_last_item_counts():
    h = mods(real("orientation-minus-90.xmp"))  # flip: item 7 (auto), item 8 (rotated -90)
    flips = [m for m in h if m["operation"] == "flip"]
    assert len(flips) == 1 and flips[0]["enabled"] is True and flips[0]["num"] == 8
    mm = next(m for m in h if m["operation"] == "mask_manager")  # item 0: darktable's mask manager, always off
    assert mm["enabled"] is False
    # history_end 8: the rotation is undone, flip has the settings of item 7
    before = mods(with_history_end(real("orientation-minus-90.xmp"), 8))
    assert next(m for m in before if m["operation"] == "flip")["num"] == 7
    d = edits.diff(before, h)
    assert [m["operation"] for m in d["changed"]] == ["flip"]
    assert d["added"] == d["removed"] == d["switched_on"] == d["switched_off"] == []
    # switching a module off
    off = [dict(m, enabled=False) if m["operation"] == "flip" else m for m in h]
    assert [m["operation"] for m in edits.diff(h, off)["switched_off"]] == ["flip"]
    assert [m["operation"] for m in edits.diff(off, h)["switched_on"]] == ["flip"]


def test_second_instances_of_a_module():
    h = mods(real("blending-modes-uniform.xmp"))
    exp = sorted((m["priority"], m["instance"]) for m in h if m["operation"] == "exposure")
    assert [p for p, _ in exp] == [0, 1, 2, 3, 4]
    assert dict(exp)[1] == "1"
    assert len([m for m in h if m["operation"] == "colorbalance"]) == 2
    labels = [edits.public(m)["label"] for m in h if m["operation"] == "exposure"]
    assert "exposure 1" in labels and "exposure" in labels
    # one instance dropped: only that one is "removed"
    fewer = [m for m in h if not (m["operation"] == "exposure" and m["priority"] == 3)]
    d = edits.diff(h, fewer)
    assert [(m["operation"], m["priority"]) for m in d["removed"]] == [("exposure", 3)]
    assert d["changed"] == []


def test_no_history():
    data = without_history(real("nop.xmp"))
    x = xmp.parse(data)
    assert x["history"] == [] and x["edited"] == 0
    assert edits.summary(None, []) == "no darktable edit"
    d = edits.diff(mods(real("nop.xmp")), [])
    assert len(d["removed"]) == 8


def test_changed_settings_and_preset_instance_names():
    h = mods(real("capture-sharpen.xmp"))  # darktable 5 sidecar, xmp_version 5
    exp = next(m for m in h if m["operation"] == "exposure")
    assert exp["instance"] == "_builtin_scene-referred default"
    assert edits.public(exp)["label"] == "exposure scene-referred default"
    tweaked = [dict(m, params="x" + m["params"]) if m["operation"] == "exposure" else m for m in h]
    d = edits.diff(h, tweaked)
    assert [m["operation"] for m in d["changed"]] == ["exposure"]
    assert edits.diff(h, h) == {"added": [], "removed": [], "switched_on": [], "switched_off": [], "changed": []}
    assert edits.summary(edits.diff(h, h), h) == "no module changed"


def test_summary_is_short():
    h = mods(real("blending-modes-uniform.xmp"))
    s = edits.summary(None, h)
    assert s.endswith(" more") and s.count(",") == edits.SUMMARY_NAMES - 1


# ---------------------------------------------------------------- module names
def test_module_names_file(monkeypatch, tmp_path):
    f = tmp_path / "module-names.json"
    monkeypatch.setattr(edits, "MODULE_NAMES", f)
    edits._names.update(mtime=None, map={})
    assert edits.module_name("colorbalancergb") == "colorbalancergb"  # no file: the operation
    f.write_text(json.dumps({"colorbalancergb": "color balance rgb", "exposure": "exposure"}))
    assert edits.module_name("colorbalancergb") == "color balance rgb"
    assert edits.module_name("sigmoid") == "sigmoid"  # not in the file: the operation
    f.write_text("{ not json")
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert edits.module_name("colorbalancergb") == "colorbalancergb"  # unreadable: the operation
    edits._names.update(mtime=None, map={})


def test_module_names_place():
    assert edits.MODULE_NAMES == Path(edits.__file__).resolve().parent / "resources" / "module-names.json"


# ---------------------------------------------------------------- events from XMP posts and the scanner
@pytest.fixture()
def fresh(clean):
    """Photo 3 without a sidecar and without any edit record."""
    with db.write_lock:
        db.conn().execute("DELETE FROM edit_events")
        db.conn().execute("DELETE FROM edit_baseline")
        db.conn().commit()
    sc = sidecar(3)
    if sc.exists():
        sc.unlink()
    return clean


def sidecar(pid: int) -> Path:
    r = db.conn().execute("SELECT relpath FROM photos WHERE id=?", (pid,)).fetchone()
    return xmp.sidecar_path(scanner.abs_of(r["relpath"]))


def beat(c, agent, ws=()):
    r = c.post("/api/agents/heartbeat", json={**agent, "state": {"working_set": list(ws)}})
    assert r.status_code == 200


def post(c, data: bytes, pid=3, **params):
    q = "&".join("%s=%s" % kv for kv in {"checkout_ts": 0, **params}.items())
    r = c.post("/api/photos/%d/xmp?%s" % (pid, q), content=data, headers={"content-type": "application/rdf+xml"})
    assert r.status_code == 200, r.text
    return r


def edits_of(c, pid=3):
    r = c.get("/api/photos/%d/edits" % pid)
    assert r.status_code == 200
    return r.json()["edits"]


def test_post_records_device_and_changes(fresh):
    c = fresh
    beat(c, PC)
    beat(c, PHONE)
    post(c, real("nop.xmp"), agent=PHONE["id"])
    beat(c, {**PC, "name": "Desktop renamed"})
    post(c, real("exposure.xmp"), agent=PC["id"])
    ev = edits_of(c)
    assert len(ev) == 2
    newest, first = ev
    assert newest["source"] == "agent" and newest["agent_id"] == PC["id"] and newest["device"] == "Desktop renamed"
    assert first["device"] == "Phone" and first["device_os"] == "android"
    assert first["changes"] is None  # nothing to compare the first XMP with
    assert [m["operation"] for m in newest["changes"]["added"]] == ["exposure"]
    assert newest["summary"] == "exposure"
    assert {m["operation"] for m in newest["modules"]} >= {"exposure", "flip"}
    assert set(newest["modules"][0]) == {"operation", "name", "instance", "priority", "enabled", "label", "auto"}
    assert newest["time"] >= first["time"] and newest["saves"] == 1 and newest["first_time"] == newest["time"]
    d = c.get("/api/photos/3").json()
    assert d["last_edit"] == newest

def test_post_from_the_phone_after_a_pc(fresh):
    c = fresh
    beat(c, PC)
    beat(c, PHONE)
    post(c, real("nop.xmp"), agent=PC["id"])
    post(c, with_history_end(real("exposure.xmp"), 8), agent=PHONE["id"])  # same modules: nothing changed
    ev = edits_of(c)
    assert [e["device"] for e in ev] == ["Phone", "Desktop"]
    assert ev[0]["summary"] == "no module changed"
    post(c, real("exposure.xmp"), agent=PHONE["id"])  # same device, same day: the same entry
    ev = edits_of(c)
    assert [e["device"] for e in ev] == ["Phone", "Desktop"]
    assert ev[0]["summary"] == "exposure" and ev[0]["saves"] == 2

def test_old_agent_without_agent_parameter(fresh):
    c = fresh
    beat(c, PC, [{"id": 3, "filename": "IMG_0003.jpg", "status": "dirty", "checkout_ts": 1, "synced_ts": 0, "error": ""}])
    beat(c, PHONE)
    r = post(c, real("nop.xmp"), checkout_ts=time.time())  # what agents before this change send
    assert r.headers["X-LPL-Merged"] in ("0", "1")
    ev = edits_of(c)
    assert ev[0]["agent_id"] == PC["id"] and ev[0]["device"] == "Desktop"  # the only device holding the photo
    # nobody holds it: recorded without a device
    beat(c, PC)
    post(c, real("exposure.xmp"))
    ev = edits_of(c)
    assert ev[0]["source"] == "agent" and ev[0]["agent_id"] is None and ev[0]["device"] is None


def test_unknown_agent_id_is_kept(fresh):
    post(fresh, real("nop.xmp"), agent="android-never-seen")
    e = edits_of(fresh)[0]
    assert e["agent_id"] == "android-never-seen" and e["device"] is None


def test_xmp_without_history_is_recorded(fresh):
    c = fresh
    beat(c, PC)
    beat(c, PHONE)
    post(c, real("exposure.xmp"), agent=PHONE["id"])
    post(c, without_history(real("exposure.xmp")), agent=PC["id"])  # history discarded in darktable
    e = edits_of(c)[0]
    assert e["modules"] == [] and len(e["changes"]["removed"]) == 9
    assert "exposure" in e["summary"]

def touch_later(p: Path, data: bytes, dt: float = 5):
    p.write_bytes(data)
    t = time.time() + dt
    os.utime(p, (t, t))


def test_scanner_records_edits_outside_lpl(fresh):
    c = fresh
    beat(c, PC)
    post(c, real("nop.xmp"), agent=PC["id"])
    sc = sidecar(3)
    # darktable on a PC wrote to the NAS directly: a second exposure instance
    touch_later(sc, real("exposure.xmp"))
    scanner.scan()
    ev = edits_of(c)
    assert len(ev) == 2
    assert ev[0]["source"] == "outside" and ev[0]["device"] is None and ev[0]["agent_id"] is None
    assert [m["operation"] for m in ev[0]["changes"]["added"]] == ["exposure"]
    scanner.scan()  # nothing new: nothing recorded
    assert len(edits_of(c)) == 2
    # only the rating changed outside: not an edit
    touch_later(sc, sc.read_bytes().replace(b'xmp:Rating="1"', b'xmp:Rating="4"'), dt=10)
    scanner.scan()
    assert len(edits_of(c)) == 2
    assert c.get("/api/photos/3").json()["rating"] == 4


def test_outside_change_found_when_an_agent_posts(fresh):
    c = fresh
    beat(c, PC)
    post(c, real("nop.xmp"), agent=PC["id"])
    touch_later(sidecar(3), real("exposure.xmp"))  # not scanned yet
    post(c, real("orientation-minus-90.xmp"), agent=PC["id"])
    ev = edits_of(c)
    assert [e["source"] for e in ev] == ["agent", "outside", "agent"]
    assert [m["operation"] for m in ev[1]["changes"]["added"]] == ["exposure"]
    # against the file it replaced (that file has exposure and highlights, the posted one neither)
    assert sorted(m["operation"] for m in ev[0]["changes"]["removed"]) == ["exposure", "highlights"]


def test_first_scan_only_remembers(fresh):
    """An XMP LPL has no record of: remembered, not reported (what changed is unknown, maybe only a rating)."""
    touch_later(sidecar(3), real("exposure.xmp"))
    scanner.scan()
    assert edits_of(fresh) == []
    assert edits.baseline(3) is not None
    assert fresh.get("/api/photos/3").json()["last_edit"] is None


def test_edits_of_unknown_photo(fresh):
    assert fresh.get("/api/photos/99999/edits").status_code == 404


def test_files_this_needs_are_not_ignored():
    """The module-names file and these tests' fixtures must reach git and the Docker image (a folder named
    "data" is ignored by .gitignore, because a library's data folder must never be committed)."""
    import fnmatch
    import shutil
    import subprocess
    repo = Path(__file__).resolve().parents[2]
    paths = ["server/lpl/resources/module-names.json", "server/lpl/resources/module-flags.json",
             "server/tests/fixtures/module-flags.json", "server/tests/fixtures/xmp/nop.xmp",
             "server/tests/fixtures/schema-main-0f1139c.sql"]
    if shutil.which("git") and (repo / ".git").exists():
        r = subprocess.run(["git", "-C", str(repo), "check-ignore", "--no-index", *paths], capture_output=True, text=True)
        assert r.stdout.strip() == "", "ignored by git: " + r.stdout
    # .dockerignore: patterns match from the build context's root, a match on a folder excludes what is in it
    pats = [l.strip() for l in (repo / ".dockerignore").read_text().splitlines() if l.strip() and not l.startswith("#")]
    for path in paths[:2]:
        p = path.split("/")
        for i in range(1, len(p) + 1):
            part = "/".join(p[:i])
            hit = [x for x in pats if not x.startswith("!") and fnmatch.fnmatchcase(part, x.replace("**/", "*/"))]
            assert not hit, "%s excluded from the Docker image by %s" % (part, hit)
