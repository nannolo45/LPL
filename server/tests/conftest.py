# SPDX-License-Identifier: GPL-3.0-or-later
"""Test setup: a throwaway photo folder with a few generated images and a fresh data folder.

LPL reads its configuration from the environment when lpl.config is first imported, so the
folders are created and the variables set here, before anything from lpl is imported."""
import os
import random
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="lpl-test-"))
PHOTOS = _TMP / "photos"
os.environ.update(PHOTO_ROOT=str(PHOTOS), DATA_DIR=str(_TMP / "data"), WEB_DIR=str(_TMP / "no-web"),
                  LPL_NO_SCAN="1", LPL_MODE="nas", LPL_PASSWORD="", SCAN_INTERVAL="0", PREVIEW_WORKERS="1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_photos() -> None:
    from PIL import Image
    rnd = random.Random(1)
    for i, folder in enumerate(["2024/2024-05-01 Walk", "2024/2024-05-01 Walk", "2024/2024-06-10 Lake"]):
        d = PHOTOS / folder
        d.mkdir(parents=True, exist_ok=True)
        # noisy pixels so the JPEG is a few kB: enough bytes for range tests to mean something
        img = Image.new("RGB", (64, 48))
        img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(64 * 48)])
        img.save(d / ("IMG_%04d.jpg" % (i + 1)), quality=95)


@pytest.fixture(scope="session")
def client():
    _make_photos()
    from fastapi.testclient import TestClient
    from lpl import main, scanner
    app = main.create_app()
    scanner.scan()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean(client):
    """No agents and no jobs at the start of a test."""
    from lpl import db
    with db.write_lock:
        db.conn().execute("DELETE FROM jobs")
        db.conn().execute("DELETE FROM agents")
        db.conn().commit()
    return client
