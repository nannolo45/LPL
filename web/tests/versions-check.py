# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendered check of versions (R17) in headless Chrome, desktop (1400 x 900) and phone (390 x 844, Android Chrome user
agent), with screenshots: the count badge on a tile, the versions strip, rating of the viewed version (written to that
version's XMP only), set as cover, delete with confirmation, and the phone page.

Optional, not part of `npm test`: needs the Python package `playwright` and Google Chrome. Run `npm run build` first.

    python web/tests/versions-check.py <python with server/requirements.txt> <folder for screenshots>

Starts LPL on a throwaway folder of three generated JPEGs; IMG_0001 gets versions 1 and 2 (synthetic XMPs)."""
import json
import os
import shutil
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
    tmp = Path(tempfile.mkdtemp(prefix="lpl-versions-"))
    photos = tmp / "photos" / "2026" / "2026-11-18 Lake"
    photos.mkdir(parents=True)
    subprocess.run([py, "-c", "import sys\nfrom PIL import Image\nfor i,c in enumerate([(70,110,150),(150,120,80),(90,140,90)]):\n"
                    "    Image.new('RGB',(600,400),c).save(sys.argv[1]+'/IMG_%04d.jpg'%(i+1))", str(photos)], check=True)
    mk = ("import sys\nfrom lpl import xmp\nd=sys.argv[1]\n"
          "open(d+'/IMG_0001.jpg.xmp','wb').write(xmp.apply_meta(None, rating=3, labels=[], tags=[], category_tags=[]))\n"
          "open(d+'/IMG_0001_01.jpg.xmp','wb').write(xmp.set_version_name(xmp.apply_meta(None, rating=5, labels=[1], tags=[], category_tags=[]), 'black and white'))\n"
          "open(d+'/IMG_0001_02.jpg.xmp','wb').write(xmp.apply_meta(None, rating=1, labels=[], tags=[], category_tags=[]))\n")
    subprocess.run([py, "-c", mk, str(photos)], check=True, cwd=str(ROOT / "server"))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PHOTO_ROOT=str(tmp / "photos"), DATA_DIR=str(tmp / "data"), WEB_DIR=str(ROOT / "web" / "dist"),
               PORT=str(port), HOST="127.0.0.1", LPL_PASSWORD="", SCAN_INTERVAL="0", LOG_LEVEL="WARNING")
    srv = subprocess.Popen([py, "-m", "lpl.main"], cwd=str(ROOT / "server"), env=env)

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")

    def rating_in(name):
        import re
        m = re.search(r'xmp:Rating="(-?\d)"', (photos / name).read_text())
        return int(m.group(1)) if m else None

    try:
        for _ in range(100):
            try:
                st = call("GET", "/api/status")
                if st["photos"] == 3 and st["scan"]["state"] == "idle":
                    break
            except Exception:
                pass
            time.sleep(0.2)
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
            # ---- desktop
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base + "/")
            badge = page.get_by_label("3 versions")
            expect(badge).to_have_count(1, timeout=15000)   # only the photo that has versions
            results.append("desktop: one tile has the badge '3'; the other two have none")
            tile = page.locator(".tile").filter(has=badge)
            tile.locator(".img").click()
            strip = page.get_by_label("Versions", exact=True)
            expect(strip).to_be_visible(timeout=10000)
            expect(strip.locator(".vth")).to_have_count(3)
            results.append("desktop: versions strip with 3 thumbnails: " + " | ".join(strip.locator(".vth .vn").all_inner_texts()))
            strip.get_by_role("button", name="v2, no edits").click()
            page.get_by_role("button", name="Rate 4 stars").click()
            time.sleep(1)
            assert rating_in("IMG_0001_02.jpg.xmp") == 4 and rating_in("IMG_0001.jpg.xmp") == 3, "rating must go to version 2 only"
            results.append("desktop: rating 4 on v2 is in IMG_0001_02.jpg.xmp; the original's XMP still says 3")
            page.screenshot(path=str(shots / "desktop-versions-strip.png"))
            strip.get_by_role("button", name="Set as cover").click()
            time.sleep(1)
            assert call("POST", "/api/photos/get", {"ids": [1]})["items"][0]["cover"] == 2
            results.append("desktop: set as cover -> the photo's cover is 2")
            page.on("dialog", lambda d: d.dismiss())
            strip.get_by_role("button", name="v1 · black and white, no edits").click()
            strip.get_by_role("button", name="Delete…").click()
            dlg = page.get_by_role("dialog")
            expect(dlg).to_contain_text("backup folder")
            page.screenshot(path=str(shots / "desktop-delete-confirm.png"))
            dlg.get_by_role("button", name="Delete").click()
            time.sleep(1)
            assert not (photos / "IMG_0001_01.jpg.xmp").exists()
            results.append("desktop: delete asked first, then moved v1's file away (the original stays)")
            # ---- phone
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=PHONE_UA, is_mobile=True, has_touch=True, device_scale_factor=2)
            ph = ctx.new_page()
            ph.goto(base + "/?photo=1")
            pstrip = ph.get_by_label("Versions", exact=True)
            expect(pstrip).to_be_visible(timeout=15000)
            pstrip.scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-versions-strip.png"))
            results.append("phone: photo page shows the versions strip (%d versions)" % pstrip.locator(".vth").count())
            ph.goto(base + "/")
            expect(ph.get_by_label("2 versions")).to_have_count(1, timeout=10000)
            ph.screenshot(path=str(shots / "phone-grid-badge.png"))
            results.append("phone: grid tile shows the badge '2'")
            browser.close()
        print("\n".join(results))
    finally:
        srv.terminate()
        srv.wait(10)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
