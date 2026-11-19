# SPDX-License-Identifier: GPL-3.0-or-later
"""Devices are listed in a stable order (by name, case-insensitive, then id) that does not change with
heartbeats. See docs/devices-stable-order.md."""

PC = {"id": "pc-test", "name": "Desktop", "os": "windows"}
PHONE = {"id": "android-0000-test", "name": "Phone", "os": "android"}
LAPTOP = {"id": "pc-laptop", "name": "laptop", "os": "linux"}
TWIN = {"id": "pc-0-twin", "name": "Desktop", "os": "linux"}


def beat(c, agent):
    assert c.post("/api/agents/heartbeat", json={**agent, "state": {"working_set": []}}).status_code == 200


def order(c, path):
    return [a["id"] for a in c.get(path).json()["agents"]]


def test_order_does_not_follow_heartbeats(clean):
    c = clean
    for who in (PHONE, PC, LAPTOP):
        beat(c, who)
    seen = set()
    for who in [PHONE, PC, PHONE, LAPTOP, PC, PHONE, LAPTOP, PHONE]:
        beat(c, who)
        seen.add((tuple(order(c, "/api/agents")), tuple(order(c, "/api/status"))))
    assert seen == {(("pc-test", "pc-laptop", "android-0000-test"),) * 2}  # Desktop, laptop, Phone: by name, any case


def test_same_name_ordered_by_id(clean):
    c = clean
    beat(c, PC)
    beat(c, TWIN)
    beat(c, PC)
    assert order(c, "/api/agents") == ["pc-0-twin", "pc-test"]


def test_routing_still_ignores_order(clean):
    """job_create picks a device only when exactly one PC is online; the order of the list plays no part."""
    c = clean
    beat(c, PHONE)
    beat(c, PC)
    j = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": [1]}}).json()
    assert j["agent_id"] == PC["id"]
    beat(c, LAPTOP)
    j = c.post("/api/jobs", json={"kind": "edit", "payload": {"ids": [1]}}).json()
    assert j["agent_id"] is None
