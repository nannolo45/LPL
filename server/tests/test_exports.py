# SPDX-License-Identifier: GPL-3.0-or-later
"""Exports received from the Android app (R18): streamed upload, name clash, version, delete to backup, caps, step
"exporting", the "has exports" filter, scanner ignores the exports folder. See docs/exports-r18.md."""
import io
import shutil
from pathlib import Path

import pytest
from PIL import Image

from lpl import db, scanner
from lpl.config import config

PHONE = {"id": "android-exp-test", "name": "Phone", "os": "android"}


def jpeg(size=(40, 30)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture()
def lib(client):
    from conftest import PHOTOS
    d = PHOTOS / "exp-lib" / "2026" / "trip"
    shutil.rmtree(PHOTOS / "exp-lib", ignore_errors=True)
    shutil.rmtree(PHOTOS / "exports", ignore_errors=True)
    d.mkdir(parents=True)
    Image.new("RGB", (40, 30), (9, 9, 9)).save(d / "X.jpg")
    with db.write_lock:
        db.conn().execute("DELETE FROM photo_exports")
        db.conn().execute("DELETE FROM settings WHERE key='pref.settings'")
        db.conn().commit()
    scanner.scan()
    yield db.conn().execute("SELECT id FROM photos WHERE relpath='exp-lib/2026/trip/X.jpg'").fetchone()["id"]


def post(client, pid, name="out.jpg", body=None, **q):
    q = dict(agent=PHONE["id"], name=name, **q)
    return client.post("/api/photos/%d/exports" % pid, params=q, content=body if body is not None else jpeg())


def test_upload_and_path(client, lib):
    r = post(client, lib, version=2)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["path"] == "exports/exp-lib/2026/trip/out.jpg" and j["export_id"]
    assert (config.photo_root / j["path"]).is_file()
    e = client.get("/api/photos/%d/exports" % lib).json()["exports"][0]
    assert (e["version"], e["name"], e["format"], e["agent"]) == (2, "out.jpg", "jpeg", PHONE["id"])
    assert e["size"] == len(jpeg())
    assert client.get("/api/photos/%d/exports/%d/file" % (lib, e["id"])).content == jpeg()


def test_large_streamed(client, lib):
    big = b"\xff\xd8\xff" + b"\0" * (40 * 1024 * 1024)
    def gen():
        for i in range(0, len(big), 1 << 20):
            yield big[i:i + (1 << 20)]
    r = client.post("/api/photos/%d/exports" % lib, params={"name": "big.jpg", "size": len(big)}, content=gen())
    assert r.status_code == 200, r.text
    assert (config.photo_root / r.json()["path"]).stat().st_size == len(big)
    assert client.post("/api/photos/%d/exports" % lib, params={"name": "cut.jpg", "size": len(big) + 1}, content=jpeg()).status_code == 400
    left = [p for p in (config.photo_root / "exports").rglob("*") if p.name.startswith(".lpl-export")]
    assert not left


def test_name_clash_never_overwrites(client, lib):
    a, b, c = (post(client, lib).json()["path"] for _ in range(3))
    assert [Path(x).name for x in (a, b, c)] == ["out.jpg", "out (2).jpg", "out (3).jpg"]


def test_bad_uploads(client, lib):
    assert post(client, lib, name="../../evil.jpg").json()["path"].endswith("trip/evil.jpg")
    assert post(client, lib, name="x.exe").status_code == 400
    assert post(client, lib, name="x.png").status_code == 400  # JPEG bytes named .png
    assert post(client, lib, body=b"").status_code == 400
    assert post(client, 999999).status_code == 404
    assert post(client, lib, version=-1).status_code == 400


def test_formats(client, lib):
    for ext, fmt in (("png", "PNG"), ("tif", "TIFF"), ("webp", "WEBP")):
        b = io.BytesIO()
        Image.new("RGB", (8, 8)).save(b, fmt)
        assert post(client, lib, name="a." + ext, body=b.getvalue()).status_code == 200


def test_delete_moves_to_backup(client, lib):
    j = post(client, lib).json()
    r = client.delete("/api/photos/%d/exports/%d" % (lib, j["export_id"]))
    assert r.status_code == 200 and r.json()["exports"] == []
    assert not (config.photo_root / j["path"]).exists()
    assert Path(r.json()["moved_to"]).is_file() and config.backup_dir in Path(r.json()["moved_to"]).parents
    assert client.delete("/api/photos/%d/exports/%d" % (lib, j["export_id"])).status_code == 404


def test_setting_exports_folder(client, lib):
    client.put("/api/prefs/settings", json={"value": {"exportsFolder": "out/sub"}})
    assert post(client, lib).json()["path"] == "out/sub/exp-lib/2026/trip/out.jpg"
    client.put("/api/prefs/settings", json={"value": {"exportsFolder": "../../etc"}})
    assert post(client, lib).json()["path"].startswith("exports/")
    shutil.rmtree(config.photo_root / "out", ignore_errors=True)


def test_filter_and_scanner(client, lib):
    post(client, lib)
    q = lambda f: client.post("/api/photos/query", json={"filters": f, "limit": 2000}).json()["items"]
    found = q({"exports": "yes"})
    assert [p["id"] for p in found] == [lib] and found[0]["exports"] == 1
    assert len(q({})) > 1
    scanner.scan()
    assert db.conn().execute("SELECT COUNT(*) FROM photos WHERE relpath LIKE 'exports/%'").fetchone()[0] == 0


def test_caps_and_step(client, lib):
    body = dict(PHONE, state={"working_set": [{"id": lib, "status": "dirty", "step": "exporting", "progress": 0.5}]})
    old = client.post("/api/agents/heartbeat", json=body).json()
    assert "server_caps" not in old   # no caps announced: the answer is as before
    new = client.post("/api/agents/heartbeat", json=dict(body, caps=["exports"])).json()
    assert "exports" in new["server_caps"]
    ag = [a for a in client.get("/api/agents").json()["agents"] if a["id"] == PHONE["id"]][0]
    it = ag["state"]["working_set"][0]
    assert it["step"] == "exporting" and "exports" in ag["caps"]
