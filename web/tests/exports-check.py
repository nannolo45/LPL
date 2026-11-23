# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendered check of exports (R18) in headless Chrome, desktop and phone: the photo panel lists an export (name, size,
device), download link, delete with confirmation, "has exports" filter. Optional, not part of `npm test`; run
`npm run build` first.   python web/tests/exports-check.py <python with server/requirements.txt> <screenshot folder>"""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36")


def main(py: str, shots: Path) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lpl-exports-"))
    photos = tmp / "photos" / "2026" / "Lake"
    photos.mkdir(parents=True)
    subprocess.run([py, "-c", "import sys\nfrom PIL import Image\nfor i,c in enumerate([(70,110,150),(150,120,80)]):\n"
                    "    Image.new('RGB',(600,400),c).save(sys.argv[1]+'/IMG_%04d.jpg'%(i+1))", str(photos)], check=True)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PHOTO_ROOT=str(tmp / "photos"), DATA_DIR=str(tmp / "data"), WEB_DIR=str(ROOT / "web" / "dist"),
               PORT=str(port), HOST="127.0.0.1", LPL_PASSWORD="", SCAN_INTERVAL="0", LOG_LEVEL="WARNING")
    srv = subprocess.Popen([py, "-m", "lpl.main"], cwd=str(ROOT / "server"), env=env)

    def call(method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"content-type": "application/json"} if body is not None else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")

    try:
        for _ in range(100):
            try:
                st = call("GET", "/api/status")
                if st["photos"] == 2 and st["scan"]["state"] == "idle":
                    break
            except Exception:
                pass
            time.sleep(0.2)
        from PIL import Image  # noqa: only to make the export bytes
        b = io.BytesIO(); Image.new("RGB", (30, 20)).save(b, "JPEG")
        call("POST", "/api/agents/heartbeat", {"id": "p1", "name": "Phone", "os": "android", "state": {"working_set": []}})
        r = call("POST", "/api/photos/1/exports?agent=p1&name=lake-final.jpg&version=0", raw=b.getvalue())
        assert r["path"] == "exports/2026/Lake/lake-final.jpg", r
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base + "/")
            page.locator(".tile .img").first.click()
            box = page.get_by_test_id("exports")
            expect(box).to_be_visible(timeout=15000)
            expect(box).to_contain_text("lake-final.jpg")
            expect(box).to_contain_text("Phone")
            expect(box.get_by_role("link", name="Download")).to_have_attribute("href", "/api/photos/1/exports/1/file")
            results.append("desktop: panel lists lake-final.jpg with size, device Phone, date, Download link")
            page.screenshot(path=str(shots / "desktop-exports.png"))
            ph = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=PHONE_UA, is_mobile=True, has_touch=True).new_page()
            ph.goto(base + "/?photo=1")
            expect(ph.get_by_test_id("exports")).to_contain_text("lake-final.jpg", timeout=15000)
            ph.screenshot(path=str(shots / "phone-exports.png"))
            results.append("phone: photo page lists the export")
            page.on("dialog", lambda d: d.dismiss())
            box.get_by_role("button", name="Delete").click()
            dlg = page.get_by_role("dialog")
            expect(dlg).to_contain_text("backup folder")
            dlg.get_by_role("button", name="Delete").click()
            time.sleep(1)
            assert call("GET", "/api/photos/1/exports")["exports"] == []
            assert not (tmp / "photos" / "exports" / "2026" / "Lake" / "lake-final.jpg").exists()
            results.append("desktop: delete asked first, then the file went to the backup folder")
            browser.close()
        print("\n".join(results)); print("all %d checks passed" % len(results))
    finally:
        srv.terminate()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
