# SPDX-License-Identifier: GPL-3.0-or-later
"""Strip on a phone, by touch (branch strip-touch-fixes): reorder lanes and folder headings by their handle,
rename by tap, and the selection bar fits. Headless browser with mobile emulation and touch, viewport 412 x 915.

Optional, not part of `npm test`: needs the Python package `playwright`, Google Chrome at /usr/bin/google-chrome
(or Playwright's own chromium: PW_CHROMIUM=1) and `npm run build` in web/ first.

    python web/tests/strip-touch-check.py <python with server/requirements.txt> <folder for screenshots> [chromium|firefox]

Chromium: real touch events through the DevTools protocol (a finger that presses, holds, moves, lifts).
Firefox has no such protocol; there only the taps (rename, selection bar) are checked, with Playwright's touchscreen."""
import json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
UA = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
FF_UA = "Mozilla/5.0 (Android 14; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0"


def main(py: str, shots: Path, browser_name: str) -> None:
    from PIL import Image
    shots.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lpl-striptouch-"))
    photos = tmp / "photos" / "2026" / "2026-11-18 Lake"
    photos.mkdir(parents=True)
    for i in range(24):
        Image.new("RGB", (600, 400), (40 + i * 10, 100, 160 - i * 6)).save(photos / f"IMG_{i + 1:04d}.jpg")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ, PHOTO_ROOT=str(tmp / "photos"), DATA_DIR=str(tmp / "data"), WEB_DIR=str(ROOT / "web" / "dist"),
               PORT=str(port), HOST="127.0.0.1", LPL_PASSWORD="", SCAN_INTERVAL="0", LOG_LEVEL="WARNING")
    shutil.copytree(ROOT / "server" / "lpl", tmp / "server" / "lpl", ignore=shutil.ignore_patterns("__pycache__"))
    srv = subprocess.Popen([py, "-m", "lpl.main"], cwd=str(tmp / "server"), env=env)

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")

    ok = []

    def check(cond, msg):
        assert cond, "FAILED: " + msg
        ok.append(msg); print("ok  ", msg)

    try:
        for _ in range(100):
            try:
                if call("GET", "/api/status")["photos"] == 24: break
            except Exception: pass
            time.sleep(0.2)
        # folder "Tiere": subfolders Animali, 03, 02 (each one series). Folder "Reihe": three series directly in it.
        tiere = call("POST", "/api/series-folders", {"name": "Tiere"})["id"]
        for n, ids in (("Animali", [1, 2, 3]), ("03", [4, 5, 6]), ("02", [7, 8, 9])):
            sf = call("POST", "/api/series-folders", {"name": n, "parent_id": tiere})["id"]
            call("POST", "/api/series", {"name": n + " serie", "ids": ids, "folder_id": sf})
        reihe = call("POST", "/api/series-folders", {"name": "Reihe"})["id"]
        for n, ids in (("Eins", [10, 11, 12]), ("Zwei", [13, 14, 15]), ("Drei", [16, 17, 18])):
            call("POST", "/api/series", {"name": n, "ids": ids, "folder_id": reihe})
        solo = call("POST", "/api/series", {"name": "Solo", "ids": [19, 20, 21, 22, 23, 24]})["id"]  # one series, three frames on its linked board
        f0 = call("GET", f"/api/series/{solo}/board")["frames"][0]
        call("PUT", f"/api/series/{solo}/board", {"items": [{"id": 19, "x": 60, "y": 90}, {"id": 20, "x": 60, "y": 450}, {"id": 21, "x": 60, "y": 810},
             {"id": 22, "x": 240, "y": 810}, {"id": 23, "x": 60, "y": 1200}, {"id": 24, "x": 240, "y": 1200}],
             "frames": [dict(f0, label="A", x=40, y=40, w=400, h=250), {"label": "B", "x": 40, "y": 400, "w": 400, "h": 250, "maybe": 0}, {"label": "C", "x": 40, "y": 760, "w": 400, "h": 250, "maybe": 0}]})
        sframes = lambda: [f["label"] for f in sorted(call("GET", f"/api/series/{solo}/board")["frames"], key=lambda f: f["y"])]
        slanes = lambda: [l["label"] for l in call("GET", f"/api/series/{solo}/strip")["lanes"] if l["frame"]]
        groups = lambda: [g["name"] for g in call("GET", f"/api/series-folders/{tiere}/strip")["groups"]]
        lanes = lambda: [l["label"] for l in call("GET", f"/api/series-folders/{reihe}/strip")["lanes"]]
        frames = lambda: [f["label"] for f in sorted(call("GET", f"/api/series-folders/{reihe}/board")["frames"], key=lambda f: (round(f["y"] / 50), f["x"]))]
        check(groups() == ["Animali", "03", "02"] and lanes() == ["Eins", "Zwei", "Drei"], "fixture: Tiere = Animali, 03, 02; Reihe = Eins, Zwei, Drei")

        with sync_playwright() as p:
            firefox = browser_name == "firefox"
            if firefox:
                browser = p.firefox.launch(headless=True)
            else:
                browser = p.chromium.launch(executable_path=None if os.environ.get("PW_CHROMIUM") else "/usr/bin/google-chrome", headless=True)
            ctxargs = dict(viewport={"width": 412, "height": 915}, has_touch=True, device_scale_factor=2.625, user_agent=FF_UA if firefox else UA)
            if not firefox: ctxargs["is_mobile"] = True
            ctx = browser.new_context(**ctxargs)
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            cdp = None if firefox else ctx.new_cdp_session(pg)

            def finger(steps, hold=0.0):
                """steps: [(x, y), ...]: touch down at the first, wait `hold`, move through the rest, lift."""
                pt = lambda x, y: [{"x": x, "y": y, "id": 1}]
                x, y = steps[0]
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": pt(x, y)})
                time.sleep(hold)
                for x, y in steps[1:]:
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": pt(x, y)}); time.sleep(0.02)
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

            def centre(loc):
                b = loc.bounding_box(); return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

            def line(a, b, n=20):
                return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]

            def open_card(name):  # cards come folders first (by name as created), then series: Tiere, Reihe, Solo
                if name == "Solo": pg.get_by_text("Solo", exact=True).first.tap()  # a series card opens by a tap on it
                else: pg.get_by_role("button", name="Open", exact=True).nth(["Tiere", "Reihe"].index(name)).tap()

            def open_folder(name, shot=None):
                pg.goto(base + "/"); pg.get_by_role("button", name="Series").last.tap()
                open_card(name)
                pg.get_by_role("button", name="Strip", exact=True).first.tap()
                expect(pg.locator(".strip.phone")).to_be_visible()
                time.sleep(0.8)

            def no_overflow(w):
                return pg.evaluate("[document.documentElement.scrollWidth, innerWidth]")

            # ---- 1. folder headings: rename by tap, reorder by their handle
            open_folder("Tiere")
            pg.screenshot(path=str(shots / "01-tiere-strip.png"))
            name = pg.locator(".sg-name", has_text="03")
            name.tap()
            ed = pg.get_by_label("New name for the folder 03")
            expect(ed).to_be_focused(); pg.screenshot(path=str(shots / "02-folder-rename-open.png"))
            pg.keyboard.press("ControlOrMeta+a"); pg.keyboard.type("Vögel"); pg.keyboard.press("Enter")
            expect(pg.locator(".sg-name", has_text="Vögel")).to_be_visible(timeout=5000)
            check(groups() == ["Animali", "Vögel", "02"], "tap on a folder heading's name opens the editor; Enter renames it (server)")
            pg.get_by_role("button", name="Rename the folder 02").tap()
            pg.keyboard.press("ControlOrMeta+a"); pg.keyboard.type("Fische"); pg.keyboard.press("Enter")
            expect(pg.locator(".sg-name", has_text="Fische")).to_be_visible(timeout=5000)
            check(groups() == ["Animali", "Vögel", "Fische"], "the folder heading's pencil (44 px) renames it (server)")
            pg.get_by_role("button", name="More for the folder Vögel: open, rename, colour, new series, delete").tap()
            pg.get_by_role("menuitem", name="Rename").tap()
            expect(pg.get_by_label("New name for the folder Vögel")).to_be_focused()
            time.sleep(0.6)
            expect(pg.get_by_label("New name for the folder Vögel")).to_be_focused()  # the closing menu did not take the focus away
            pg.keyboard.press("ControlOrMeta+a"); pg.keyboard.type("Vogel"); pg.keyboard.press("Enter")
            expect(pg.locator(".sg-name", has_text="Vogel")).to_be_visible(timeout=5000)
            check(groups() == ["Animali", "Vogel", "Fische"], "the ⋯ menu's Rename opens the editor and keeps the focus (server)")

            if not firefox:
                g = lambda n: pg.locator(f'.strip-group:has(.sg-name:text-is("{n}")) .lane-grip')
                a, f_ = centre(g("Animali")), centre(g("Fische"))
                # long press, then drag the last folder above the first (the lane under the finger is not the page's scroll)
                finger(line(f_, (a[0], a[1] - 30), 28), hold=0.5)
                time.sleep(1.2)
                check(groups() == ["Fische", "Animali", "Vogel"], "long press on a folder handle + drag above another: order saved on the server")
                # press-and-drag with no wait at all
                v, fi = centre(g("Vogel")), centre(g("Fische"))
                finger(line(v, (fi[0], fi[1] - 30), 20), hold=0.0)
                time.sleep(1.2)
                check(groups() == ["Vogel", "Fische", "Animali"], "press-and-drag on the handle (no hold) reorders too")
                pg.reload(); time.sleep(1)
                pg.get_by_role("button", name="Strip", exact=True).first.tap(); time.sleep(0.8)
                check([x.inner_text() for x in pg.locator(".sg-name").all()] == ["Vogel", "Fische", "Animali"], "after a reload the page shows the saved order")
                # the drop indicator and the lifted lane while dragging (screenshot taken mid-drag)
                a = centre(g("Vogel")); b = centre(g("Animali"))
                pt = lambda x, y: [{"x": x, "y": y, "id": 1}]
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": pt(*b)}); time.sleep(0.5)
                for x, y in line(b, (a[0], a[1] - 20), 20)[1:]:
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": pt(x, y)}); time.sleep(0.02)
                time.sleep(0.2)
                check(pg.locator(".strip-mark").evaluate("e => getComputedStyle(e).display") == "block", "a drop indicator is visible while dragging")
                pg.screenshot(path=str(shots / "03-folder-drag.png"))
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}); time.sleep(1)
                check(groups() == ["Animali", "Vogel", "Fische"], "dropped at the top: order saved")

            # ---- 2. series lanes of a linked folder board: rename by tap, reorder, board follows
            open_folder("Reihe")
            pg.screenshot(path=str(shots / "04-reihe-strip.png"))
            pg.locator(".lane-name", has_text="Zwei").tap()
            expect(pg.get_by_label("New name for Zwei")).to_be_focused()
            pg.keyboard.press("ControlOrMeta+a"); pg.keyboard.type("Zwo"); pg.keyboard.press("Enter")
            expect(pg.locator(".lane-name", has_text="Zwo")).to_be_visible(timeout=5000)
            check(lanes() == ["Eins", "Zwo", "Drei"], "tap on a lane's name renames the series (server)")
            check(sorted(frames()) == ["Drei", "Eins", "Zwo"], "board linked: the folder board's frame carries the new name")
            if not firefox:
                gl = lambda n: pg.locator(f'section.lane:has(.lane-name:text-is("{n}")) .lane-grip')
                d, e1 = centre(gl("Drei")), centre(gl("Eins"))
                finger(line(d, (e1[0], e1[1] - 30), 28), hold=0.5)
                time.sleep(1.2)
                check(lanes() == ["Drei", "Eins", "Zwo"], "long press on a lane handle + drag above another: order saved on the server")
                check(sorted(frames()) == ["Drei", "Eins", "Zwo"] and len(call("GET", f"/api/series-folders/{reihe}/board")["frames"]) == 3, "the folder board still has its three frames after the reorder")
                pg.screenshot(path=str(shots / "05-reihe-reordered.png"))

            # ---- 3. one series, three frames, board linked: the frame follows the lane
            pg.goto(base + "/"); pg.get_by_role("button", name="Series").last.tap()
            open_card("Solo")
            pg.get_by_role("button", name="Strip", exact=True).first.tap(); time.sleep(0.8)
            pg.locator(".lane-name", has_text="B").first.tap()
            expect(pg.get_by_label("New name for B")).to_be_focused()
            pg.keyboard.press("ControlOrMeta+a"); pg.keyboard.type("Bea"); pg.keyboard.press("Enter")
            expect(pg.locator(".lane-name", has_text="Bea")).to_be_visible(timeout=5000)
            check(slanes() == ["A", "Bea", "C"] and sframes() == ["A", "Bea", "C"], "frame lane renamed by tap: the board's frame is renamed too")
            if not firefox:
                gs = lambda n: pg.locator(f'section.lane:has(.lane-name:text-is("{n}")) .lane-grip')
                c_, a_ = centre(gs("C")), centre(gs("A"))
                finger(line(c_, (a_[0], a_[1] - 30), 28), hold=0.5)
                time.sleep(1.5)
                check(slanes() == ["C", "A", "Bea"], "frame lane reordered by long press + drag (server)")
                check(sframes() == ["C", "A", "Bea"], "board linked: the frames on the board follow the new order")
                pg.screenshot(path=str(shots / "05b-solo-reordered.png"))

            # ---- 4. the selection bar fits at 412 and 360 px
            for w in (412, 360):
                pg.set_viewport_size({"width": w, "height": 915}); time.sleep(0.5)
                pg.locator(".sth").first.tap(); time.sleep(0.4)
                bar = pg.locator(".strip-sel"); expect(bar).to_be_visible()
                dims = bar.evaluate("""e => ({sw: e.scrollWidth, cw: e.clientWidth, doc: document.documentElement.scrollWidth, vw: innerWidth,
                    btns: [...e.querySelectorAll('button, a')].map(b => { const r = b.getBoundingClientRect(); return [b.innerText.trim(), Math.round(r.left), Math.round(r.right), Math.round(r.height)] })})""")
                print(dims)
                check(dims["sw"] <= dims["cw"] and dims["doc"] <= dims["vw"], f"selection bar at {w} px: no horizontal overflow")
                check(all(l >= 0 and r <= w and h >= 40 for _, l, r, h in dims["btns"]) and len(dims["btns"]) >= 4, f"selection bar at {w} px: every action inside the screen, at least 40 px high ({len(dims['btns'])} actions)")
                pg.screenshot(path=str(shots / f"06-selection-bar-{w}.png"))
                pg.locator(".sth").first.tap(); time.sleep(0.2)
            check(not errors, "no page errors: " + "; ".join(errors))
            browser.close()
        print("\nALL OK (%d checks, %s)" % (len(ok), browser_name))
    finally:
        srv.terminate(); srv.wait(10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "chromium")
