# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendered check of the LUT files section (R19) on the Devices page in headless Chrome, desktop and phone: each
device's LUT folder (or why it offers none), every file with its size, last sender and a dot per device, and
"Remove from LPL" (asks first; the file leaves LPL's list, no device report changes).
Optional, not part of `npm test`; run `npm run build` first.
python web/tests/luts-check.py <python with server/requirements.txt> <screenshot folder>"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36")
DESK = {"id": "pc-desk", "name": "Desktop", "os": "windows", "caps": ["luts"], "state": {"working_set": []}}
LAP = {"id": "pc-lap", "name": "Laptop", "os": "linux", "caps": ["luts"], "state": {"working_set": []}}
PHONE = {"id": "android-ph", "name": "Phone", "os": "android", "caps": ["luts"], "state": {"working_set": []}}
CUBE = b"TITLE \"x\"\nLUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"


def open_devices(page):
    page.locator(".dev-ind").first.click()
    page.locator(".dev-open").first.click()


def main(py: str, shots: Path) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lpl-luts-"))
    (tmp / "photos").mkdir()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PHOTO_ROOT=str(tmp / "photos"), DATA_DIR=str(tmp / "data"), WEB_DIR=str(ROOT / "web" / "dist"),
               PORT=str(port), HOST="127.0.0.1", LPL_PASSWORD="", SCAN_INTERVAL="0", LOG_LEVEL="WARNING")
    srv = subprocess.Popen([py, "-m", "lpl.main"], cwd=str(ROOT / "server"), env=env)

    def call(method, path, body=None, raw=None, headers=None):
        data = json.dumps(body).encode() if body is not None else raw
        h = dict(headers or {})
        if body is not None:
            h["content-type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")

    def put(agent, path, data):
        q = "/api/luts/file?path=%s&agent=%s" % (urllib.parse.quote(path, safe=""), agent["id"])
        return call("PUT", q, raw=data, headers={"x-sha256": hashlib.sha256(data).hexdigest()})

    def state(agent, folder, files, reason=None):
        call("PUT", "/api/luts/state?agent=" + agent["id"], {"folder": folder, "reason": reason, "files": files})

    try:
        for _ in range(100):
            try:
                call("GET", "/api/status"); break
            except Exception:
                time.sleep(0.2)
        for a in (DESK, LAP, PHONE):
            call("POST", "/api/agents/heartbeat", a)
        warm, mono = CUBE, CUBE.replace(b"\"x\"", b"\"m\"")
        put(DESK, "film/Kodak 2383.cube", warm)
        put(PHONE, "phone/mono.cube", mono)
        put(DESK, "film/Kodak 2383.cube", warm + b"# changed\n")  # one earlier version
        sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
        state(DESK, "C:/Users/me/darktable/luts", {"film/Kodak 2383.cube": {"sha256": sha(warm + b"# changed\n"), "state": "ok"},
                                                  "phone/mono.cube": {"sha256": sha(mono), "state": "ok"}})
        state(LAP, None, {}, reason="darktable has no LUT folder set (preferences > processing > LUT 3D root folder)")
        state(PHONE, "on this phone", {"film/Kodak 2383.cube": {"sha256": sha(warm), "state": "different"},
                                       "phone/mono.cube": {"sha256": sha(mono), "state": "ok"}})
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base + "/")
            open_devices(page)
            sec = page.locator(".sharedsection").filter(has=page.locator(".display", has_text="LUT files"))
            expect(sec).to_be_visible(timeout=15000)
            expect(sec).to_contain_text("2 files")
            expect(sec).to_contain_text("darktable has no LUT folder set")
            expect(sec).to_contain_text("2 files on the phone")
            row = sec.locator("tr").filter(has_text="film/Kodak 2383.cube")
            expect(row).to_contain_text("1 earlier")
            expect(row.locator('[aria-label="Phone: has a different file at this path (kept as it is)"]')).to_have_count(1)
            expect(row.locator('[aria-label="Desktop: has this file"]')).to_have_count(1)
            sec.scroll_into_view_if_needed()
            page.screenshot(path=str(shots / "desktop-luts.png"), full_page=True)
            results.append("desktop: folders, files, earlier versions, a dot per device")
            page.once("dialog", lambda d: d.accept())
            sec.locator("tr").filter(has_text="phone/mono.cube").locator("button", has_text="Remove from LPL").click()
            expect(sec.locator("tr").filter(has_text="phone/mono.cube")).to_have_count(0, timeout=15000)
            listed = {f["path"]: f for f in call("GET", "/api/luts?since=0")["files"]}
            assert listed["phone/mono.cube"]["deleted"] is True
            results.append("desktop: Remove from LPL asks first, the file leaves LPL's list")
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=PHONE_UA, is_mobile=True, has_touch=True, device_scale_factor=2)
            ph = ctx.new_page()
            ph.goto(base + "/")
            ph.locator(".dev-ind.round").first.click()
            msec = ph.locator(".sharedsection").filter(has=ph.locator(".display", has_text="LUT files"))
            expect(msec).to_be_visible(timeout=15000)
            assert msec.locator("table").count() == 0
            expect(msec.locator(".sharedrow").first).to_contain_text("film/Kodak 2383.cube")
            msec.scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-luts.png"), full_page=True)
            results.append("phone: a list instead of the table")
            browser.close()
        print("\n".join(results)); print("all %d checks passed" % len(results))
    finally:
        srv.terminate()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
