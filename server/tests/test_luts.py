# SPDX-License-Identifier: GPL-3.0-or-later
"""The LUT folder (R19, docs/luts-r19.md; contract PLAN.md 4.6 of darktable-android): caps, path rules, upload with hash
check, newest wins without a base, 409 with a stale base, history kept, Range download, removal from LPL only, device
reports and the web UI overview."""
import hashlib
import shutil

import pytest

from lpl import db, luts

PC = {"id": "pc-luts-a", "name": "Desktop", "os": "windows", "caps": ["luts"]}
PC2 = {"id": "pc-luts-b", "name": "Laptop", "os": "linux", "caps": ["luts"]}
PHONE = {"id": "android-luts", "name": "Phone", "os": "android", "caps": ["luts"]}

CUBE_A = b"TITLE \"a\"\nLUT_3D_SIZE 2\n" + b"0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"
CUBE_B = CUBE_A.replace(b"\"a\"", b"\"b\"")
CUBE_C = CUBE_A.replace(b"\"a\"", b"\"c\"")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def c(clean):
    with db.write_lock:
        for t in ("lut_files", "lut_history", "lut_devices"):
            db.conn().execute("DELETE FROM %s" % t)
        db.conn().commit()
    shutil.rmtree(luts.store_dir(), ignore_errors=True)
    for a in (PC, PC2, PHONE):
        assert clean.post("/api/agents/heartbeat", json=a).status_code == 200
    return clean


def put(c, agent, path, data, base=None, digest=None):
    h = {"x-sha256": digest or sha(data)}
    if base is not None:
        h["x-base-sha256"] = base
    return c.put("/api/luts/file", params={"path": path, "agent": agent["id"]}, content=data, headers=h)


def listing(c, since=0):
    return c.get("/api/luts", params={"since": since}).json()


def test_server_caps_lists_luts(c):
    r = c.post("/api/agents/heartbeat", json=PHONE).json()
    assert "luts" in r["server_caps"]
    old = c.post("/api/agents/heartbeat", json={"id": "pc-no-caps-luts", "name": "Old"}).json()
    assert "server_caps" not in old


@pytest.mark.parametrize("p", ["", "/abs.cube", "C:/x.cube", "a\\b.cube", "../x.cube", "a/../x.cube", "a//x.cube",
                               "./x.cube", "a/", "x.txt", "x.cube.txt", "a\x01.cube", "x" * 600 + ".cube"])
def test_bad_paths_refused(c, p):
    assert put(c, PC, p, CUBE_A).status_code == 400
    with pytest.raises(ValueError):
        luts.clean_path(p)


@pytest.mark.parametrize("p", ["x.cube", "X.CUBE", "film/Kodak 2383.3dl", "hald/identity.PNG", "g/pack.gmz", "ä/ß.cube"])
def test_good_paths(p):
    assert luts.clean_path(p) == p


def test_upload_list_download(c):
    r = put(c, PC, "film/a.cube", CUBE_A)
    assert r.status_code == 200 and r.json()["stored"] is True
    d = listing(c)
    assert d["seq"] == 1
    f = d["files"][0]
    assert (f["path"], f["size"], f["sha256"], f["device_id"], f["device_name"], f["deleted"]) == \
        ("film/a.cube", len(CUBE_A), sha(CUBE_A), PC["id"], "Desktop", False)
    g = c.get("/api/luts/file", params={"path": "film/a.cube"})
    assert g.status_code == 200 and g.content == CUBE_A and g.headers["x-sha256"] == sha(CUBE_A)
    # Range, as /original: a cut-off download resumes
    g = c.get("/api/luts/file", params={"path": "film/a.cube"}, headers={"Range": "bytes=10-"})
    assert g.status_code == 206 and g.content == CUBE_A[10:]
    assert c.get("/api/luts/file", params={"path": "film/none.cube"}).status_code == 404
    # since: only what changed after
    assert listing(c, since=1)["files"] == []


def test_same_content_changes_nothing(c):
    put(c, PC, "a.cube", CUBE_A)
    r = put(c, PC2, "a.cube", CUBE_A, base="")
    assert r.status_code == 200 and r.json()["stored"] is False
    assert listing(c)["seq"] == 1
    assert listing(c)["files"][0]["device_name"] == "Desktop"


def test_hash_mismatch_and_bad_header(c):
    r = put(c, PC, "a.cube", CUBE_A, digest=sha(CUBE_B))
    assert r.status_code == 400 and r.json()["error"] == "hash_mismatch"
    assert listing(c)["files"] == []
    assert c.put("/api/luts/file", params={"path": "a.cube", "agent": PC["id"]}, content=CUBE_A).status_code == 400
    assert put(c, PC, "a.cube", b"").status_code == 400
    assert c.put("/api/luts/file", params={"path": "a.cube"}, content=CUBE_A,
                 headers={"x-sha256": sha(CUBE_A)}).status_code == 400  # agent required


def test_png_signature(c):
    assert put(c, PC, "h.png", b"not a png at all").status_code == 400
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
    assert put(c, PC, "h.png", png).status_code == 200


def test_too_large(c, monkeypatch):
    monkeypatch.setattr(luts, "MAX_BYTES", 100)
    r = put(c, PC, "big.cube", b"0" * 101)
    assert r.status_code == 413
    assert listing(c)["files"] == []


def test_newest_wins_without_base_and_history_kept(c):
    put(c, PC, "a.cube", CUBE_A)
    r = put(c, PC2, "a.cube", CUBE_B)  # a PC that changed its file: no base, newest wins
    assert r.status_code == 200 and r.json()["stored"] is True
    f = listing(c)["files"][0]
    assert f["sha256"] == sha(CUBE_B) and f["device_name"] == "Laptop" and f["seq"] == 2
    ov = c.get("/api/luts/overview").json()
    assert ov["files"][0]["history"] == 1
    # the earlier content is still stored
    assert luts.blob_path(sha(CUBE_A)).is_file()


def test_stale_base_is_409_and_stores_nothing(c):
    put(c, PC, "a.cube", CUBE_A)
    put(c, PC, "a.cube", CUBE_B)
    r = put(c, PHONE, "a.cube", CUBE_C, base=sha(CUBE_A))  # the phone changed it, and so did the PC
    assert r.status_code == 409
    assert r.json()["error"] == "changed_elsewhere" and r.json()["file"]["sha256"] == sha(CUBE_B)
    assert listing(c)["files"][0]["sha256"] == sha(CUBE_B)
    # "mine": the phone sends again with the server's current hash as base
    r = put(c, PHONE, "a.cube", CUBE_C, base=sha(CUBE_B))
    assert r.status_code == 200 and r.json()["stored"] is True
    assert listing(c)["files"][0]["device_name"] == "Phone"


def test_empty_base_is_a_first_upload(c):
    put(c, PC, "a.cube", CUBE_A)
    # a PC that joins later with another file at the same path: not replaced
    r = put(c, PC2, "a.cube", CUBE_B, base="")
    assert r.status_code == 409
    assert listing(c)["files"][0]["sha256"] == sha(CUBE_A)
    # a new path with an empty base: stored
    assert put(c, PC2, "b.cube", CUBE_B, base="").json()["stored"] is True


def test_matching_base_is_stored(c):
    put(c, PC, "a.cube", CUBE_A)
    r = put(c, PHONE, "a.cube", CUBE_B, base=sha(CUBE_A))
    assert r.status_code == 200 and r.json()["stored"] is True


def test_remove_from_lpl_only_and_back(c):
    put(c, PC, "a.cube", CUBE_A)
    d = c.delete("/api/luts/file", params={"path": "a.cube"})
    assert d.status_code == 200 and d.json()["deleted"] is True
    f = listing(c, since=1)["files"][0]
    assert f["deleted"] is True and f["sha256"] == sha(CUBE_A) and f["seq"] == 2
    assert c.get("/api/luts/file", params={"path": "a.cube"}).status_code == 404
    assert luts.blob_path(sha(CUBE_A)).is_file()  # kept as history
    assert c.delete("/api/luts/file", params={"path": "zzz.cube"}).status_code == 404
    # a later upload brings it back, whatever base it carries
    r = put(c, PHONE, "a.cube", CUBE_B, base="")
    assert r.status_code == 200 and r.json()["stored"] is True
    f = listing(c)["files"][0]
    assert f["deleted"] is False and f["sha256"] == sha(CUBE_B)
    assert c.get("/api/luts/overview").json()["files"][0]["history"] == 1


def test_one_content_stored_once(c):
    put(c, PC, "a.cube", CUBE_A)
    put(c, PC, "copy/a.cube", CUBE_A)
    ov = c.get("/api/luts/overview").json()
    assert ov["bytes_stored"] == len(CUBE_A)
    assert ov["bytes_current"] == 2 * len(CUBE_A)


def test_state_reports_and_overview(c):
    put(c, PC, "a.cube", CUBE_A)
    put(c, PC, "b.cube", CUBE_B)
    s = {"folder": "C:/Users/x/luts", "reason": None, "files": {
        "a.cube": {"sha256": sha(CUBE_A), "state": "ok"},
        "b.cube": {"sha256": sha(CUBE_C), "state": "different"},
        "../bad.cube": {"sha256": "x", "state": "ok"}}}
    r = c.put("/api/luts/state", params={"agent": PC["id"]}, json=s)
    assert r.status_code == 200 and r.json()["files"] == 2
    assert c.put("/api/luts/state", params={"agent": PC2["id"]},
                 json={"folder": None, "reason": "darktable has no LUT folder set", "files": {}}).status_code == 200
    c.put("/api/luts/state", params={"agent": PHONE["id"]},
          json={"folder": "on this phone", "files": {"a.cube": {"sha256": sha(CUBE_A), "state": "ok"}}})
    ov = c.get("/api/luts/overview").json()
    a = next(f for f in ov["files"] if f["path"] == "a.cube")
    assert [(d["name"], d["state"]) for d in a["devices"]] == [("Desktop", "ok"), ("Phone", "ok")]
    b = next(f for f in ov["files"] if f["path"] == "b.cube")
    assert [(d["name"], d["state"]) for d in b["devices"]] == [("Desktop", "different")]
    lap = next(d for d in ov["devices"] if d["name"] == "Laptop")
    assert lap["folder"] is None and lap["reason"] == "darktable has no LUT folder set"
    # a report replaces the last one
    c.put("/api/luts/state", params={"agent": PC["id"]}, json={"folder": "x", "files": {}})
    ov = c.get("/api/luts/overview").json()
    assert [d["name"] for d in next(f for f in ov["files"] if f["path"] == "a.cube")["devices"]] == ["Phone"]
    # a device without the cap is not listed
    c.post("/api/agents/heartbeat", json=dict(PHONE, caps=[]))
    assert "Phone" not in [d["name"] for d in c.get("/api/luts/overview").json()["devices"]]
    assert c.put("/api/luts/state", params={"agent": PC["id"]}, json={"files": []}).status_code == 400
