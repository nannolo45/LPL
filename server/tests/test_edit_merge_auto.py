# SPDX-License-Identifier: GPL-3.0-or-later
"""Modules darktable applies by itself (left out of the short summary) and saves of one device on one day as
one entry. See docs/android-edit-record-progress.md."""
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lpl import db, edits, xmp
from lpl.config import config
from test_edit_record import DATA, PC, PHONE, beat, edits_of, fresh, post, real, sidecar, touch_later, with_history_end  # noqa: F401

FLAGS = DATA.parent / "module-flags.json"  # generated from darktable-android schema/ui-schema.json, see SOURCE.md


@pytest.fixture()
def flags(monkeypatch):
    monkeypatch.setattr(edits, "MODULE_FLAGS", FLAGS)


@pytest.fixture()
def no_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(edits, "MODULE_FLAGS", tmp_path / "missing.json")


def hist(data):
    return xmp.parse(data)["history"]


def auto_ops(h):
    return sorted(op for op, _ in edits.automatic(h))


def user_ops(h):
    a = edits.automatic(h)
    return sorted(m["operation"] for m in h if (m["operation"], m["priority"]) not in a)


def set_item(data: bytes, op: bytes, attr: bytes, value: bytes, which: int = -1) -> bytes:
    """Change one attribute of a history item of operation op (the last one by default)."""
    items = list(re.finditer(rb"<rdf:li\s[^>]*darktable:operation=\"" + op + rb"\"[^>]*/>", data))
    m = items[which]
    new = re.sub(rb'darktable:' + attr + rb'="[^"]*"', b'darktable:' + attr + b'="' + value + b'"', m.group(0))
    return data[:m.start()] + new + data[m.end():]


# ---------------------------------------------------------------- what darktable applies by itself
def test_only_opened_photo_is_darktables_default(flags):
    h = hist(real("nop.xmp"))  # darktable's default pipeline only
    assert user_ops(h) == []
    assert edits.summary(None, h, edits.automatic(h)) == "darktable's default edit"


def test_only_opened_darktable_5(flags):
    data = real("capture-sharpen.xmp")
    opened = hist(with_history_end(data, 11))  # without the last item: what darktable 5 wrote on opening
    assert user_ops(opened) == []  # incl. exposure, color calibration, sigmoid, flip from darktable's own presets
    assert "exposure" in auto_ops(opened) and "sigmoid" in auto_ops(opened)
    full = hist(data)  # the test then changed demosaic (a second demosaic item)
    assert user_ops(full) == ["demosaic"]
    assert edits.summary(None, full, edits.automatic(full)) == "demosaic"


def test_user_modules_after_the_defaults(flags):
    assert user_ops(hist(real("exposure.xmp"))) == ["exposure"]
    assert user_ops(hist(real("orientation-minus-90.xmp"))) == ["flip"]  # rotated: flip's second item
    h = hist(real("blending-modes-uniform.xmp"))
    assert set(user_ops(h)) == {"exposure", "colorbalance"}
    assert len(user_ops(h)) == 7
    assert "mask_manager" in auto_ops(hist(real("orientation-minus-90.xmp")))  # internal, always automatic


def test_without_the_flags_file(no_flags):
    """Only darktable's own presets (instance names "_builtin_…") are recognised."""
    assert user_ops(hist(real("nop.xmp"))) == sorted(m["operation"] for m in hist(real("nop.xmp")))
    opened = hist(with_history_end(real("capture-sharpen.xmp"), 11))
    assert set(auto_ops(opened)) == {"channelmixerrgb", "exposure", "flip", "sigmoid"}


def test_known_limit_compressed_history(flags):
    """What the method gets wrong: after darktable's "compress history", a default module the user changed has
    one item again; if nothing that is not darktable's own comes before it, it still counts as automatic."""
    data = set_item(real("nop.xmp"), b"temperature", b"params", b"00000000")  # changed white balance, compressed
    assert "temperature" in auto_ops(hist(data))


def test_auto_flag_in_the_api(fresh, flags):
    c = fresh
    beat(c, PC)
    post(c, real("nop.xmp"), agent=PC["id"])
    e = edits_of(c)[0]
    assert e["summary"] == "darktable's default edit"
    assert len(e["modules"]) == 8 and all(m["auto"] for m in e["modules"])  # every module stays in the list


def test_events_stored_before_auto_flags():
    """Modules stored without where their items were: nothing is marked automatic."""
    old = [{"operation": "rawprepare", "instance": "", "priority": 0, "enabled": True}]
    assert edits.automatic(old) == set()


# ---------------------------------------------------------------- saves of one device on one day
def test_saves_merged_and_compared_with_the_state_before(fresh):
    c = fresh
    beat(c, PC)
    beat(c, PHONE)
    post(c, real("nop.xmp"), agent=PHONE["id"])
    base = real("exposure.xmp")
    for v in (b"0000803f", b"0000003f", b"00000040"):  # three exposure steps
        post(c, set_item(base, b"exposure", b"params", v * 7), agent=PC["id"])
    ev = edits_of(c)
    assert len(ev) == 2
    e = ev[0]
    assert e["saves"] == 3 and e["first_time"] <= e["time"] and e["device"] == "Desktop"
    assert [m["operation"] for m in e["changes"]["added"]] == ["exposure"]  # against nop, once
    assert e["changes"]["changed"] == [] and e["summary"] == "exposure"


def test_switched_on_and_off_within_a_run_is_no_change(fresh):
    c = fresh
    beat(c, PC)
    beat(c, PHONE)
    post(c, real("exposure.xmp"), agent=PHONE["id"])
    post(c, set_item(real("exposure.xmp"), b"exposure", b"enabled", b"0"), agent=PC["id"])
    assert [m["operation"] for m in edits_of(c)[0]["changes"]["switched_off"]] == ["exposure"]
    post(c, real("exposure.xmp"), agent=PC["id"])
    e = edits_of(c)[0]
    assert e["saves"] == 2 and e["summary"] == "no module changed"


def test_another_device_in_between_starts_a_new_entry(fresh):
    c = fresh
    beat(c, PC)
    beat(c, PHONE)
    post(c, real("nop.xmp"), agent=PC["id"])
    post(c, real("exposure.xmp"), agent=PHONE["id"])
    post(c, real("orientation-minus-90.xmp"), agent=PC["id"])
    assert [(e["device"], e["saves"]) for e in edits_of(c)] == [("Desktop", 1), ("Phone", 1), ("Desktop", 1)]


def test_outside_in_between_starts_a_new_entry(fresh):
    c = fresh
    beat(c, PC)
    post(c, real("nop.xmp"), agent=PC["id"])
    touch_later(sidecar(3), real("exposure.xmp"))
    from lpl import scanner
    scanner.scan()
    post(c, real("orientation-minus-90.xmp"), agent=PC["id"])
    assert [e["source"] for e in edits_of(c)] == ["agent", "outside", "agent"]


def at(zone: str, *ymdhm) -> float:
    return datetime(*ymdhm, tzinfo=ZoneInfo(zone)).timestamp()


def two_saves(t1, t2):
    nop, exp = hist(real("nop.xmp")), hist(real("exposure.xmp"))
    with db.write_lock:
        a = edits.record(3, nop, None, "agent", PC["id"], ts=t1)
        b = edits.record(3, exp, nop, "agent", PC["id"], ts=t2)
        db.conn().commit()
    return a, b


def test_a_new_day_starts_a_new_entry_in_the_servers_zone(fresh, monkeypatch):
    beat(fresh, PC)
    # 23:50 and 00:10 in Auckland (UTC+13 in November) are 10:50 and 11:10 UTC of the same UTC day
    t1, t2 = at("Pacific/Auckland", 2025, 11, 18, 23, 50), at("Pacific/Auckland", 2025, 11, 19, 0, 10)
    monkeypatch.setattr(config, "tz", "Pacific/Auckland")
    a, b = two_saves(t1, t2)
    assert a != b and len(edits_of(fresh)) == 2
    assert edits_of(fresh)[0]["changes"]["added"][0]["operation"] == "exposure"


def test_same_day_in_utc_is_one_entry(fresh, monkeypatch):
    beat(fresh, PC)
    t1, t2 = at("Pacific/Auckland", 2025, 11, 18, 23, 50), at("Pacific/Auckland", 2025, 11, 19, 0, 10)
    monkeypatch.setattr(config, "tz", "UTC")
    a, b = two_saves(t1, t2)
    e = edits_of(fresh)
    assert a == b and len(e) == 1 and e[0]["saves"] == 2
    assert e[0]["first_time"] == pytest.approx(t1) and e[0]["time"] == pytest.approx(t2)


def test_unknown_zone_falls_back(monkeypatch):
    monkeypatch.setattr(config, "tz", "Nowhere/Never")
    assert edits.day_of(time.time()) == datetime.now().date()
