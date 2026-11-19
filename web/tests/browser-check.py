# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendered check of the last-edit line, the list of edits and the progress bar (R15, R16) in headless Chrome,
desktop (1400 x 900) and phone (390 x 844, Android Chrome user agent), with screenshots.

Optional, not part of `npm test`: needs the Python package `playwright` and Google Chrome (no Playwright
browser download). Builds nothing: run `npm run build` in web/ first.

    python web/tests/browser-check.py <python with server/requirements.txt> <folder for screenshots>

Starts LPL on a throwaway photo folder (three generated JPEGs), plays a PC agent "Desktop" and a phone "Phone"
over the API (real darktable XMPs from server/tests/fixtures/xmp), and checks what the page shows. The progress
bar must go away when the phone stops sending heartbeats (LPL counts a device offline after 15 s)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
XMPS = ROOT / "server" / "tests" / "fixtures" / "xmp"
PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36")


def main(py: str, shots: Path) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lpl-browser-"))
    photos = tmp / "photos" / "2026" / "2026-11-18 Lake"
    photos.mkdir(parents=True)
    subprocess.run([py, "-c", "import sys\nfrom PIL import Image\nfor i,c in enumerate([(70,110,150),(150,120,80),(90,140,90)]):\n"
                    "    Image.new('RGB',(600,400),c).save(sys.argv[1]+'/IMG_%04d.jpg'%(i+1))", str(photos)], check=True)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PHOTO_ROOT=str(tmp / "photos"), DATA_DIR=str(tmp / "data"), WEB_DIR=str(ROOT / "web" / "dist"),
               PORT=str(port), HOST="127.0.0.1", LPL_PASSWORD="", SCAN_INTERVAL="0", LOG_LEVEL="WARNING")
    srv = subprocess.Popen([py, "-m", "lpl.main"], cwd=str(ROOT / "server"), env=env)

    def call(method, path, body=None, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": ctype} if data else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}") if "json" in (r.headers.get("content-type") or "") else r.read()

    stop = threading.Event()
    try:
        for _ in range(100):
            try:
                if call("GET", "/api/status")["photos"] == 3 and call("GET", "/api/status")["scan"]["state"] == "idle":
                    break
            except Exception:
                pass
            time.sleep(0.2)
        pc = {"id": "pc-desktop", "name": "Desktop", "os": "windows"}
        phone = {"id": "android-phone", "name": "Phone", "os": "android"}
        call("POST", "/api/agents/heartbeat", {**pc, "state": {"working_set": []}})
        call("POST", "/api/agents/heartbeat", {**phone, "state": {"working_set": []}})
        xmp = lambda n: (XMPS / n).read_bytes()
        call("POST", "/api/photos/1/xmp?checkout_ts=0&agent=pc-desktop", xmp("nop.xmp"), "application/rdf+xml")
        call("POST", "/api/photos/1/xmp?checkout_ts=0&agent=android-phone", xmp("exposure.xmp"), "application/rdf+xml")
        call("POST", "/api/photos/1/xmp?checkout_ts=0&agent=pc-desktop", xmp("blending-modes-uniform.xmp"), "application/rdf+xml")

        def beats():  # the phone downloads photo 1, the PC sends the edit of photo 2
            k = 0
            while not stop.is_set():
                call("POST", "/api/agents/heartbeat", {**phone, "state": {"working_set": [
                    {"id": 1, "filename": "IMG_0001.jpg", "status": "downloading", "checkout_ts": time.time(), "synced_ts": 0,
                     "error": "", "step": "downloading", "progress": min(0.95, 0.42 + 0.01 * k)}]}})
                call("POST", "/api/agents/heartbeat", {**pc, "state": {"working_set": [
                    {"id": 2, "filename": "IMG_0002.jpg", "status": "syncing", "checkout_ts": time.time(), "synced_ts": 0,
                     "error": "", "step": "sending edit"}]}})
                k += 1
                stop.wait(2)
        t = threading.Thread(target=beats, daemon=True)
        t.start()
        time.sleep(1)

        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
            # ---- desktop
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base + "/?photo=1")
            line = page.get_by_role("button", name="last edited on Desktop")
            expect(line).to_be_visible(timeout=15000)
            assert "last edited on Desktop · today" in line.inner_text(), line.inner_text()
            bar = page.get_by_role("progressbar", name="on Phone: downloading")
            expect(bar).to_be_visible(timeout=10000)
            results.append("desktop: last-edit line: " + line.inner_text())
            line.click()
            lst = page.get_by_role("list", name="Edits of this photo, newest first")
            expect(lst).to_be_visible()
            items = lst.locator("li")
            expect(items).to_have_count(3)
            assert "edited on Phone" in items.nth(1).inner_text() and "added: exposure" in items.nth(1).inner_text()
            results.append("desktop: list of edits: %d entries" % items.count())
            page.locator(".right").screenshot(path=str(shots / "desktop-last-edit-and-progress.png"))
            page.get_by_role("button", name="Working set").first.click()
            expect(page.get_by_role("progressbar", name="sending the edit")).to_be_visible()
            expect(page.get_by_role("progressbar", name="downloading")).to_be_visible()
            page.screenshot(path=str(shots / "desktop-devices-progress.png"))
            results.append("desktop: Devices: bars for 'downloading' and 'sending the edit'")
            # ---- phone
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=PHONE_UA, is_mobile=True, has_touch=True,
                                      device_scale_factor=2)
            ph = ctx.new_page()
            ph.goto(base + "/?photo=1")
            pline = ph.get_by_role("button", name="last edited on Desktop")
            expect(pline).to_be_visible(timeout=15000)
            expect(ph.get_by_role("progressbar", name="on Phone: downloading")).to_be_visible()
            pline.scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-last-edit-and-progress.png"))
            pline.click()
            expect(ph.get_by_role("list", name="Edits of this photo, newest first").locator("li")).to_have_count(3)
            ph.get_by_role("list", name="Edits of this photo, newest first").scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-list-of-edits.png"))
            results.append("phone: last-edit line and list of edits")
            # ---- the phone stops reporting: the bar goes away (desktop and phone pages keep polling)
            stop.set()
            t.join(5)
            page.goto(base + "/?photo=1")
            expect(page.get_by_role("progressbar", name="on Phone: downloading")).to_be_visible(timeout=10000)
            t0 = time.time()
            expect(page.get_by_role("progressbar", name="on Phone: downloading")).to_have_count(0, timeout=30000)
            gone = time.time() - t0
            expect(page.get_by_text("on Phone: copying from the NAS")).to_be_visible()
            expect(ph.get_by_role("progressbar", name="on Phone: downloading")).to_have_count(0, timeout=10000)
            ph.get_by_text("on Phone: copying from the NAS").scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-after-device-stopped.png"))
            page.locator(".right").screenshot(path=str(shots / "desktop-after-device-stopped.png"))
            results.append("bar gone %.0f s after the check started waiting (heartbeats stopped before); plain state shown" % gone)
            browser.close()
        print("\n".join(["OK"] + results))
    finally:
        stop.set()
        srv.terminate()
        srv.wait(10)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
