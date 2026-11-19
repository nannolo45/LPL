# SPDX-License-Identifier: GPL-3.0-or-later
"""The PC agent (agent/lpl_agent.py) against this server over real HTTP: it names itself in XMP posts
(agent=, R15) and reports step and progress while it copies and syncs a photo (R16). darktable is not
started (open_paths is replaced)."""
import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from lpl import db, scanner, xmp

AGENT_PY = Path(__file__).resolve().parents[2] / "agent" / "lpl_agent.py"
DATA = Path(__file__).resolve().parent / "fixtures" / "xmp"


@pytest.fixture(scope="module")
def base_url(client):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    from lpl import api
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield "http://127.0.0.1:%d" % port
    server.should_exit = True
    t.join(5)


@pytest.fixture()
def agent_mod(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("lpl_agent_under_test", AGENT_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "WORK", tmp_path / "work")
    monkeypatch.setattr(m, "BRIDGE", tmp_path / "bridge")
    monkeypatch.setattr(m, "STATE_FILE", tmp_path / "state.json")
    return m


def test_agent_names_itself_and_reports_progress(clean, base_url, agent_mod, monkeypatch):
    c = clean
    with db.write_lock:
        db.conn().execute("DELETE FROM edit_events")
        db.conn().execute("DELETE FROM edit_baseline")
        db.conn().commit()
    r = db.conn().execute("SELECT relpath FROM photos WHERE id=2").fetchone()
    sc = xmp.sidecar_path(scanner.abs_of(r["relpath"]))
    if sc.exists():
        sc.unlink()

    cfg = {"id": "pc-agent-test", "name": "Desktop", "server": base_url, "password": "", "auto_sync_seconds": 0}
    a = agent_mod.Agent(cfg)
    monkeypatch.setattr(a, "open_paths", lambda paths: None)
    seen = []  # the working-set item as each heartbeat would report it, at every step change
    real_step = a.step

    def spy(it, step=None, progress=None):
        real_step(it, step, progress)
        seen.append(next(w for w in a.state()["working_set"] if w["id"] == it["id"]).copy())
    monkeypatch.setattr(a, "step", spy)

    def heartbeat():
        assert c.post("/api/agents/heartbeat", json={"id": cfg["id"], "name": cfg["name"], "os": "linux",
                                                      "state": a.state()}).status_code == 200

    a.edit(["2"])
    downloading = [w for w in seen if w.get("step") == "downloading"]
    assert downloading and downloading[0]["progress"] == 0.0 and downloading[-1]["progress"] == 1.0
    assert "step" not in a.state()["working_set"][0]  # done: no step any more
    heartbeat()

    # darktable wrote the edit: the agent sends it, naming itself
    it = a.items["2"]
    Path(it["local_path"] + ".xmp").write_bytes((DATA / "exposure.xmp").read_bytes())
    seen.clear()
    jpg = Path(it["local_path"] + ".jpg")
    from PIL import Image
    Image.new("RGB", (32, 24), (90, 120, 150)).save(jpg)
    a.sync("2", force=True, preview=jpg)
    assert [w.get("step") for w in seen] == ["sending preview", None, "sending edit", None]
    assert a.items["2"]["status"] == "synced"
    ev = c.get("/api/photos/2/edits").json()["edits"]
    assert len(ev) == 1 and ev[0]["agent_id"] == "pc-agent-test" and ev[0]["device"] == "Desktop"
    assert c.get("/api/photos/2").json()["last_edit"]["device"] == "Desktop"

    # a step left in the state file by an agent that was stopped is not reported after a restart
    a.step(a.items["2"], "downloading", 0.3)
    a.save()
    b = agent_mod.Agent(cfg)
    assert "step" not in b.state()["working_set"][0]
    sc.unlink()
