# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendered check of the Devices (Working set) page in headless Chrome, desktop (1400 x 900) and phone (390 x 844,
Android Chrome user agent): the device cards keep their order while the devices heartbeat in turn, the phone's
card describes the phone (app open, darkroom) and not a PC's darktable and bridge, the R16 progress bar shows in
a row next to its status pill, the device picker lists the devices in the same order. Screenshots.

Optional, not part of `npm test`: needs the Python package `playwright` and Google Chrome. Run `npm run build` first.

    python web/tests/devices-check.py <python with server/requirements.txt> <folder for screenshots>
"""
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
PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36")


def main(py: str, shots: Path) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lpl-devices-"))
    photos = tmp / "photos" / "2026" / "2026-11-18 Lake"
    photos.mkdir(parents=True)
    subprocess.run([py, "-c", "import sys\nfrom PIL import Image\nfor i,c in enumerate([(70,110,150),(150,120,80),(90,140,90)]):\n"
                    "    Image.new('RGB',(600,400),c).save(sys.argv[1]+'/IMG_%04d.jpg'%(i+1))", str(photos)], check=True)
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

    stop = threading.Event()
    try:
        for _ in range(100):
            try:
                st = call("GET", "/api/status")
                if st["photos"] == 3 and st["scan"]["state"] == "idle":
                    break
            except Exception:
                pass
            time.sleep(0.2)
        # what the two agents send: the PC agent's state, and the phone app's (darktable_running = a photo open in
        # its darkroom, bridge_alive always true); the phone downloads photo 1 with step and progress (R16)
        pc = {"id": "pc-desktop", "name": "Desktop", "os": "windows"}
        phone = {"id": "android-phone", "name": "Phone", "os": "android"}

        def beats():
            k = 0
            while not stop.is_set():
                # alternate who heartbeats last, as two real devices do
                first, second = (pc, phone) if k % 2 else (phone, pc)
                for a in (first, second):
                    if a is pc:
                        call("POST", "/api/agents/heartbeat", {**pc, "state": {"darktable_running": True, "bridge_alive": True, "version": "0.1.0",
                             "working_set": [{"id": 2, "filename": "IMG_0002.jpg", "status": "synced", "checkout_ts": time.time() - 600,
                                              "synced_ts": time.time() - 60, "error": ""}]}})
                    else:
                        call("POST", "/api/agents/heartbeat", {**phone, "state": {"darktable_running": True, "bridge_alive": True,
                             "version": "0.2.0 / darktable 5.2.1", "message": "", "working_set": [
                                 {"id": 1, "filename": "IMG_0001.jpg", "status": "downloading", "checkout_ts": time.time(), "synced_ts": 0,
                                  "error": "", "step": "downloading", "progress": min(0.95, 0.30 + 0.02 * k)}]}})
                k += 1
                stop.wait(1)
        t = threading.Thread(target=beats, daemon=True)
        t.start()
        time.sleep(1.5)

        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base + "/")
            page.get_by_role("button", name="Working set").first.click()
            cards = page.locator(".card .display").filter(has_text="")
            names = lambda: [x for x in page.locator(".card > .row > .display").all_inner_texts()]
            expect(page.locator(".card > .row > .display")).to_have_count(2, timeout=10000)
            orders = set()
            for _ in range(12):  # the page polls every 4 s; the devices alternate every second
                orders.add(tuple(names()))
                time.sleep(1)
            assert orders == {("Desktop", "Phone")}, orders
            results.append("desktop: card order over 12 s of alternating heartbeats: %s" % sorted(orders))
            phone_card = page.locator(".card").filter(has=page.locator(".display", has_text="Phone"))
            expect(phone_card.locator(".pill-ok", has_text="online · app open")).to_be_visible()
            expect(phone_card.locator(".pill-ok", has_text="photo open in the darkroom")).to_be_visible()
            assert phone_card.get_by_text("bridge script").count() == 0 and phone_card.get_by_text("darktable running").count() == 0
            pc_card = page.locator(".card").filter(has=page.locator(".display", has_text="Desktop"))
            expect(pc_card.locator(".pill-ok", has_text="bridge script active")).to_be_visible()
            results.append("desktop: phone card 'online · app open', 'photo open in the darkroom'; PC card unchanged")
            row = phone_card.locator("tbody tr").first
            pill = row.locator(".status")
            bar = row.get_by_role("progressbar", name="downloading")
            expect(pill).to_have_text("copying from the NAS")
            expect(bar).to_be_visible()
            pb, bb = pill.bounding_box(), bar.bounding_box()
            assert pb["y"] + pb["height"] <= bb["y"] + 0.5, (pb, bb)  # the bar sits under the pill, not over it
            size = bar.locator(".small").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
            assert size >= 12, size
            results.append("desktop: row has the pill 'copying from the NAS' and under it '%s' (%.0f px text)" % (bar.inner_text().replace("\n", " "), size))
            page.screenshot(path=str(shots / "desktop-devices.png"), full_page=True)
            # the picker: the same order, phone in its own words
            page.goto(base + "/?photo=1")
            page.get_by_role("button", name="Edit in darktable").first.click()
            dlg = page.get_by_role("dialog", name="Edit in darktable on which device")
            expect(dlg).to_be_visible()
            labels = dlg.locator("label").all_inner_texts()
            assert [l.split("\n")[0].strip() for l in labels] == ["Desktop", "Phone"], labels
            expect(dlg.locator("label", has_text="Phone").locator(".pill-ok")).to_have_text("app open")
            dlg.screenshot(path=str(shots / "desktop-device-picker.png"))
            results.append("desktop: picker lists Desktop, Phone; phone 'app open'")
            # phone layout: the Devices tab
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=PHONE_UA, is_mobile=True, has_touch=True, device_scale_factor=2)
            ph = ctx.new_page()
            ph.goto(base + "/")
            ph.locator(".mtabs").get_by_role("button", name="Devices").click()
            expect(ph.locator(".card > .row > .display")).to_have_count(2, timeout=10000)
            assert ph.locator(".card > .row > .display").all_inner_texts() == ["Desktop", "Phone"]
            pcard = ph.locator(".card").filter(has=ph.locator(".display", has_text="Phone"))
            expect(pcard.get_by_role("progressbar", name="downloading")).to_be_visible()
            pcard.scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-devices.png"))
            results.append("phone: Devices tab in the same order, bar in the phone's row")
            # the phone app leaves the screen: no more heartbeats from it
            stop.set()
            t.join(5)
            def pc_only():
                while not pc_stop.is_set():
                    call("POST", "/api/agents/heartbeat", {**pc, "state": {"darktable_running": True, "bridge_alive": True, "working_set": []}})
                    pc_stop.wait(1)
            pc_stop = threading.Event()
            t2 = threading.Thread(target=pc_only, daemon=True)
            t2.start()
            expect(pcard.locator(".pill-off", has_text="app not open")).to_be_visible(timeout=30000)
            expect(pcard.get_by_text("what you start here waits until it is opened there")).to_be_visible()
            expect(pcard.get_by_role("progressbar")).to_have_count(0)
            pcard.scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-devices-app-closed.png"))
            results.append("phone: after the app stopped reporting: 'app not open · seen …', the waiting hint, no bar")
            pc_stop.set()
            browser.close()
        print("\n".join(["OK"] + results))
    finally:
        stop.set()
        srv.terminate()
        srv.wait(10)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
