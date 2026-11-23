# SPDX-License-Identifier: GPL-3.0-or-later
"""The PC agent with versions (R17): it announces the "versions" cap, copies version sidecars beside the RAW, posts new and
changed ones with version=n, renames a local one to the number LPL names on 409 version_exists, removes a version deleted in
LPL only when it is unchanged here (else posts it back), and behaves as before against a server that does not list versions.
darktable is not started."""
import shutil
import time
from pathlib import Path

import pytest
from PIL import Image

from lpl import db, scanner, transfer, xmp
from test_pc_agent import agent_mod, base_url  # noqa: F401  (fixtures)

CFG = {"id": "pc-versions-test", "name": "Desktop", "password": "", "auto_sync_seconds": 0}


def side(rating=0, name=""):
    d = xmp.apply_meta(None, rating=rating, labels=[], tags=[], category_tags=[])
    return xmp.set_version_name(d, name) if name else d


@pytest.fixture()
def lib(clean):
    from conftest import PHOTOS
    d = PHOTOS / "agent-versions-lib"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    Image.new("RGB", (40, 30), (1, 2, 3)).save(d / "P.jpg")
    (d / "P.jpg.xmp").write_bytes(side(3))
    for n, r in ((1, 5), (2, 1), (100, 2)):
        (d / ("P_%02d.jpg.xmp" % n)).write_bytes(side(r, "v%d" % n))
    scanner.scan()
    pid = db.conn().execute("SELECT id FROM photos WHERE filename='P.jpg' AND folder='agent-versions-lib'").fetchone()["id"]
    return d, pid


def make_agent(agent_mod, base_url, monkeypatch):
    a = agent_mod.Agent(dict(CFG, server=base_url))
    a.srv.json("POST", "/api/agents/heartbeat", a.heartbeat_body())  # the server learns the caps from the heartbeat
    monkeypatch.setattr(a, "open_paths", lambda paths: None)
    return a


def server_ns(c, pid):
    return [v["n"] for v in c.get("/api/photos/%d/versions" % pid).json()["versions"]]


def local(a, pid):
    return Path(a.items[str(pid)]["local_path"])


def test_agent_announces_versions(agent_mod):
    assert "versions" in agent_mod.CAPS


def test_copy_of_versions_beside_the_raw(lib, base_url, agent_mod, monkeypatch):
    d, pid = lib
    a = make_agent(agent_mod, base_url, monkeypatch)
    a.edit([str(pid)])
    raw = local(a, pid)
    assert sorted(agent_mod.local_versions(raw)) == [1, 2, 100]
    assert agent_mod.version_file(raw, 2).name == raw.name.replace(".jpg", "_02.jpg") + ".xmp"
    assert xmp.parse(agent_mod.version_file(raw, 1).read_bytes())["version_name"] == "v1"
    assert xmp.parse(Path(str(raw) + ".xmp").read_bytes())["rating"] == 3
    assert sorted(map(int, a.items[str(pid)]["versions"])) == [1, 2, 100]


def test_post_changed_and_new_versions(lib, base_url, agent_mod, monkeypatch, clean):
    d, pid = lib
    a = make_agent(agent_mod, base_url, monkeypatch)
    a.edit([str(pid)])
    raw = local(a, pid)
    time.sleep(0.05)
    agent_mod.version_file(raw, 2).write_bytes(side(4, "edited here"))   # changed in darktable
    agent_mod.version_file(raw, 101).write_bytes(side(2, "duplicate"))   # a duplicate made in darktable
    a.sync(str(pid))
    assert a.items[str(pid)]["status"] == "synced", a.items[str(pid)]
    assert xmp.parse((d / "P_02.jpg.xmp").read_bytes())["rating"] == 4
    assert (d / "P_101.jpg.xmp").is_file() and 101 in server_ns(clean, pid)
    evs = clean.get("/api/photos/%d/edits" % pid).json()["edits"]
    assert {e["version"] for e in evs} >= {2, 101}
    # nothing changed: nothing is sent again
    n = len(evs)
    a.sync(str(pid))
    assert len(clean.get("/api/photos/%d/edits" % pid).json()["edits"]) == n


def test_409_renames_the_local_sidecar(lib, base_url, agent_mod, monkeypatch, clean):
    d, pid = lib
    a = make_agent(agent_mod, base_url, monkeypatch)
    a.edit([str(pid)])
    raw = local(a, pid)
    agent_mod.version_file(raw, 101).write_bytes(side(2, "mine"))       # made here, offline ...
    (d / "P_101.jpg.xmp").write_bytes(side(5, "theirs"))                # ... and 101 made on the phone meanwhile
    scanner.scan()
    a.sync(str(pid))
    assert a.items[str(pid)]["status"] == "synced", a.items[str(pid)]
    assert not agent_mod.version_file(raw, 101).exists()
    assert xmp.parse(agent_mod.version_file(raw, 102).read_bytes())["version_name"] == "mine"
    assert xmp.parse((d / "P_101.jpg.xmp").read_bytes())["version_name"] == "theirs"   # not overwritten
    assert xmp.parse((d / "P_102.jpg.xmp").read_bytes())["version_name"] == "mine"
    assert "102" in a.items[str(pid)]["versions"]


def test_version_deleted_in_lpl(lib, base_url, agent_mod, monkeypatch, clean):
    d, pid = lib
    a = make_agent(agent_mod, base_url, monkeypatch)
    a.edit([str(pid)])
    raw = local(a, pid)
    time.sleep(0.05)
    agent_mod.version_file(raw, 2).write_bytes(side(4, "changed here"))
    assert clean.delete("/api/photos/%d/xmp?version=1" % pid).status_code == 200
    assert clean.delete("/api/photos/%d/xmp?version=2" % pid).status_code == 200
    a.edit([str(pid)])                      # asked for again: the photo is here, versions are refreshed
    assert not agent_mod.version_file(raw, 1).exists()        # unchanged here: removed
    assert agent_mod.version_file(raw, 2).exists()            # changed here: kept ...
    a.sync(str(pid))
    assert xmp.parse((d / "P_02.jpg.xmp").read_bytes())["version_name"] == "changed here"   # ... and posted back: the edit wins
    assert server_ns(clean, pid) == [0, 2, 100]


def test_old_server_without_versions(lib, base_url, agent_mod, monkeypatch, clean):
    d, pid = lib
    a = make_agent(agent_mod, base_url, monkeypatch)
    real = a.srv.json

    def old(method, path, body=None, timeout=30):
        r = real(method, path, body, timeout)
        if "/checkout" in path:
            for k in ("versions", "cover", "next"):
                r.pop(k, None)
        return r
    monkeypatch.setattr(a.srv, "json", old)
    a.edit([str(pid)])
    raw = local(a, pid)
    assert agent_mod.local_versions(raw) == {} and a.items[str(pid)].get("versions") is None
    agent_mod.version_file(raw, 7).write_bytes(side(2))
    Path(str(raw) + ".xmp").write_bytes(side(5))
    a.sync(str(pid), force=True)
    assert a.items[str(pid)]["status"] == "synced"
    assert 7 not in server_ns(clean, pid) and not (d / "P_07.jpg.xmp").exists()   # as before: only version 0 goes


def test_release_removes_version_files(lib, base_url, agent_mod, monkeypatch):
    d, pid = lib
    a = make_agent(agent_mod, base_url, monkeypatch)
    a.edit([str(pid)])
    raw = local(a, pid)
    a.release(str(pid))
    assert not raw.exists() and agent_mod.local_versions(raw) == {}


def test_local_library_send_keeps_all_versions(lib):
    """transfer.py: the files a send carries and a delete removes are the same set, including three-digit numbers."""
    d, pid = lib
    found = transfer.local_version_files(d / "P.jpg")
    assert sorted(found) == [1, 2, 100]
    assert transfer.local_version_files(d / "Q.jpg") == {}
