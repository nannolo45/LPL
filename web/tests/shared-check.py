# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendered check of shared styles, presets and module layouts (R11) on the Devices page in headless Chrome, desktop and
phone: per device line, the waiting table (list on the phone), a conflict with keep / take / keep both, a deletion with
delete here / keep, "Update <device> · n" making the shared-update job, the "Shared through LPL" panel, the setting.
Optional, not part of `npm test`; run `npm run build` first.
python web/tests/shared-check.py <python with server/requirements.txt> <screenshot folder>"""
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VEC = json.loads((ROOT / "server" / "tests" / "fixtures" / "r11-hash-vectors.json").read_text(encoding="utf-8"))["vectors"]
PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36")
DESK = {"id": "pc-desk", "name": "Desktop", "os": "windows", "caps": ["shared"], "state": {"darktable_running": True, "working_set": []}}
LAP = {"id": "pc-lap", "name": "Laptop", "os": "linux", "caps": ["shared"], "state": {"darktable_running": False, "working_set": []}}
PHONE = {"id": "android-ph", "name": "Phone", "os": "android", "caps": ["shared"], "state": {"working_set": []}}


def open_devices(page):
    """The Devices page opens as a tab from the top bar's indicator."""
    page.locator(".dev-ind").first.click()
    page.locator(".dev-open").first.click()


def main(py: str, shots: Path) -> None:
    sys.path.insert(0, str(ROOT / "server"))
    shots.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lpl-shared-"))
    (tmp / "photos").mkdir()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PHOTO_ROOT=str(tmp / "photos"), DATA_DIR=str(tmp / "data"), WEB_DIR=str(ROOT / "web" / "dist"),
               PORT=str(port), HOST="127.0.0.1", LPL_PASSWORD="", SCAN_INTERVAL="0", LOG_LEVEL="WARNING")
    srv = subprocess.Popen([py, "-m", "lpl.main"], cwd=str(ROOT / "server"), env=env)

    def call(method, path, body=None, ok=(200,)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return json.loads(e.read() or b"{}")
            raise

    def hsh(body):
        out = subprocess.run([py, "-c", "import json,sys; sys.path.insert(0,'.'); from lpl import shared; print(shared.item_hash(json.load(sys.stdin)))"],
                             cwd=str(ROOT / "server"), input=json.dumps(body), capture_output=True, text=True, env=env, check=True)
        return out.stdout.strip()

    def put(agent, body, base_rev=0, changed_at=None, ok=(200,)):
        row = body.get("row") or {}
        name = row.get("name") or body["style"]["name"]
        return call("PUT", "/api/shared?agent=" + agent["id"], {"kind": body["kind"], "operation": row.get("operation", ""),
                    "op_version": row.get("op_version", 0), "name": name, "hash": hsh(body), "changed_at": changed_at or time.time(),
                    "base_rev": base_rev, "body": body}, ok=ok)

    def preset(name, params):
        b = copy.deepcopy(VEC[0]["body"]); b["row"].update(name=name, op_params=params); return b

    try:
        for _ in range(100):
            try:
                call("GET", "/api/status"); break
            except Exception:
                time.sleep(0.2)
        for a in (DESK, LAP, PHONE):
            call("POST", "/api/agents/heartbeat", a)
        now = time.time()
        style = VEC[2]["body"]
        put(LAP, style, changed_at=now - 86400)                                     # new for Desktop
        mono = put(DESK, preset("mono red filter", "00"), changed_at=now - 7200)
        put(LAP, preset("mono red filter", "00"))
        put(LAP, preset("mono red filter", "11"), base_rev=1, changed_at=now - 3600)  # Laptop changed it ...
        put(DESK, preset("mono red filter", "22"), base_rev=1, changed_at=now - 600, ok=(409,))  # ... and Desktop too: conflict
        old = put(LAP, preset("old | cross process", "33"), changed_at=now - 3 * 86400)
        put(DESK, preset("old | cross process", "33"))
        call("DELETE", "/api/shared/%d?agent=%s&base_rev=1" % (old["id"], LAP["id"]))  # deleted on Laptop: offered to Desktop
        put(DESK, VEC[1]["body"], changed_at=now - 300)                              # a layout from Desktop
        put(LAP, preset("punchy", "44"), changed_at=now - 200)                         # another new one for Desktop
        call("PUT", "/api/shared/state?agent=" + PHONE["id"], {"items": {}, "policy": "ask"})

        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(base + "/")
            open_devices(page)
            sec = page.locator(".sharedsection")
            expect(sec).to_be_visible(timeout=15000)
            desk = sec.locator(".sharedcard").filter(has=page.locator(".display", has_text="Desktop"))
            expect(desk).to_contain_text("4 styles, presets, layouts waiting")
            expect(sec.locator(".sharedcard").filter(has=page.locator(".display", has_text="Phone"))).to_contain_text("waiting · updated from the app")
            results.append("desktop: device lines (Desktop: 4 waiting; Phone: '… waiting · updated from the app')")
            desk.locator(".devfold").click()
            rows = desk.locator("tbody tr")
            expect(rows).to_have_count(3)
            expect(desk).to_contain_text("deleted on Laptop")
            expect(desk).to_contain_text("changed here too")
            expect(desk).to_contain_text("darktable is open on Desktop")
            expect(desk.get_by_role("button", name="Update Desktop · 3")).to_be_visible()
            expect(page.locator(".sharedpanel")).to_contain_text("Shared through LPL")
            expect(page.locator(".sharedpanel .shareddot.conflict")).to_have_count(1)
            page.screenshot(path=str(shots / "desktop-shared.png"), full_page=True)
            results.append("desktop: table with style / deletion / preset rows, conflict box, darktable note, Update Desktop · 3, overview panel")
            del_row = rows.filter(has_text="old | cross process")
            del_row.get_by_role("button", name="keep").click()
            rows.filter(has_text="punchy").get_by_role("checkbox").uncheck()
            desk.get_by_role("button", name="Update Desktop · 2").click()
            time.sleep(1)
            jobs = [j for j in call("GET", "/api/jobs")["jobs"] if j["kind"] == "shared-update"]
            assert len(jobs) == 1 and jobs[0]["agent_id"] == DESK["id"], jobs
            pl = jobs[0]["payload"]
            assert pl["keep"] == [old["id"]] and pl["delete"] == [] and len(pl["ids"]) == 1, pl
            results.append("desktop: 'keep' and an unticked row give the job payload %s" % json.dumps(pl))
            desk.get_by_role("button", name="Keep both").click()
            time.sleep(1)
            names = sorted(i["name"] for i in call("GET", "/api/shared")["items"] if not i["deleted"])
            assert "mono red filter (Laptop)" in names, names
            results.append("desktop: Keep both stored Laptop's copy as 'mono red filter (Laptop)'")
            page.goto(base + "/")
            open_devices(page)
            page.get_by_role("button", name="Device settings").click()
            seg = page.get_by_role("group", name="Same item changed on two devices")
            seg.get_by_role("button", name="Newest wins").click()
            time.sleep(0.5)
            assert call("GET", "/api/shared/devices")["policy"] == "newest"
            page.screenshot(path=str(shots / "desktop-shared-setting.png"))
            results.append("desktop: Settings 'Same item changed on two devices: Newest wins' reaches the server")
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, user_agent=PHONE_UA, is_mobile=True, has_touch=True, device_scale_factor=2)
            ph = ctx.new_page()
            ph.goto(base + "/")
            ph.locator(".dev-ind.round").first.click()  # the phone opens the Devices page from the header's indicator
            msec = ph.locator(".sharedsection")
            expect(msec).to_be_visible(timeout=15000)
            lap = msec.locator(".sharedcard").filter(has=ph.locator(".display", has_text="Laptop"))
            lap.locator(".devfold").click()
            expect(lap.locator(".sharedrow").first).to_be_visible()
            assert lap.locator("table").count() == 0
            msec.scroll_into_view_if_needed()
            ph.screenshot(path=str(shots / "phone-shared.png"), full_page=True)
            results.append("phone: the waiting table is a list; overview below")
            browser.close()
        print("\n".join(results)); print("all %d checks passed" % len(results))
    finally:
        srv.terminate()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
