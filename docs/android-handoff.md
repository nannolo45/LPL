# Hand-off to the darktable Android app

LightPhotoLibrary (LPL) side of the darktable Android port
(public repository `nannolo45/darktable-android`, `docs/PLAN.md` section 4 "LPL integration").
The Android app behaves as one more LPL agent: it uses the endpoints the PC agent uses
(`/api/agents/heartbeat`, jobs, `/checkout`, `/original`, `/xmp`, `/preview`), reports `os = "android"`
and heartbeats only while it is in the foreground.

This document covers requests R1, R2, R3, R4, R6 and R8 of that plan, branch `android-handoff`.
Nothing here was tested against the real Android app (it does not exist yet); the server is tested with
simulated agents, the web app with unit tests and headless Chrome (see "Tests").

## 1. "Edit on this phone" (R1)

Shown only when the web app runs on Android (`/\bAndroid\b/` in the user agent: Chrome, Firefox and web
views, including the LPL shell) and LPL is not in local mode. Places (phone layout only):

| Where | `ids` | `start` | `series`, `title` |
|---|---|---|---|
| Photo page (`Mobile.tsx`, `PhotoPage`), under "Edit in darktable" | the library list as loaded, in display order (series order when a series is open with "Photos in order"); just the photo when it was opened by a deep link and is not in the list | the photo shown | the open series (id and name); otherwise no `series` and `title` = folder name or tag, if one is open |
| Selection bar in the library (`MobileApp`), button "This phone" | same list | the first selected photo in display order | as above |
| Board on the phone (`Board.tsx`, selection bar), when photos are selected | the board's reading order, then the photos not in it (the same list the full-screen view pages through) | the first selected photo in that order | the board's series id and name |

It is a normal `<a href>` the user taps (Android only opens another app on a user gesture). No job is
created. The link never contains the password or the login cookie.

### Link format

```
intent://edit?server=<encodeURIComponent(location.origin)>&ids=<comma separated ids>&start=<id>[&series=<id>][&title=<encodeURIComponent(name)>]#Intent;scheme=lpldt;package=app.dtandroid;S.browser_fallback_url=<encodeURIComponent(location.origin + "/android-app.html")>;end
```

- `ids`: at most 200. A longer list is cut to the 200 around `start` (100 before it where possible,
  shifted at the ends so it is always 200). If `start` is not in the list it is put first.
- `series` and `title` only when known. `title` is the series name, else the folder's last part, else the tag.
- Built by `phoneEditLink()` in `web/src/devices.ts`.

Example (made-up ids, documentation address 192.0.2.10):

```
intent://edit?server=http%3A%2F%2F192.0.2.10%3A8080&ids=12,7,31&start=7&series=4&title=Lake%20%26%20hills%2F2024#Intent;scheme=lpldt;package=app.dtandroid;S.browser_fallback_url=http%3A%2F%2F192.0.2.10%3A8080%2Fandroid-app.html;end
```

Which the app receives as `lpldt://edit?server=http%3A%2F%2F192.0.2.10%3A8080&ids=12,7,31&start=7&series=4&title=Lake%20%26%20hills%2F2024`.

### When the app is not installed
- Chrome follows `S.browser_fallback_url` to `web/public/android-app.html` (served at `/android-app.html`),
  a static page in LPL's look that says the darktable app is not on this phone. **Where to get it is a
  placeholder** (`data-placeholder="android-app-download"` in that file): fill it in once a download
  location exists.
- Firefox for Android opens `intent://` through an "open in another app" prompt and does not reliably
  follow the fallback URL (Mozilla bugs 1859052, 1882137). So after the tap LPL waits 1.8 s; if the page is
  still visible (`document.hidden` false, no `visibilitychange` to hidden in between) a small note appears
  at the bottom: "The darktable app didn't open. Not on this phone yet? How to get it" (link to
  `/android-app.html`, × closes it). It also appears in Chrome if the app did not open. **Not exercised in
  Firefox here** (only headless Chrome was available): must be tried on the phone, including what happens
  while Firefox's prompt is still open (the note may appear under it; it is non-blocking).
- Inside the LPL shell the note is never shown (see section 8).

## 2. Deep links (R2)

- `<origin>/?photo=<id>`: phone layout: the photo page (Library tab). Desktop layout: the Library opens on the
  photo's folder with the photo selected and scrolled to, so the inspector shows it (the existing
  "Show in folder" behaviour, `reveal`).
- `<origin>/?series=<id>`: phone: the series' board (Series tab). Desktop: the Board view on that series.
- The parameters are read once at start-up (`takeDeepLink()` in `devices.ts`, also when the login page
  comes first) and removed from the address bar with `history.replaceState` at once, so a reload does not
  repeat them. Other query parameters stay.
- Unknown or malformed ids give LPL's usual not-found message as a toast ("Not Found" for photos, which is
  what `GET /api/photos/{id}` answers; "series not found" for series). Nothing else changes.

## 3. Choosing the device; job routing (R3)

Server (`server/lpl/api.py`):
- `heartbeat()`: an agent whose heartbeat says `os == "android"` receives only queued jobs whose `agent_id`
  is its id. Every other agent receives, as before, jobs addressed to it and unaddressed jobs.
- `job_create()`: without `agent_id`, the job is addressed automatically only if exactly one online agent
  whose os is not `"android"` exists; otherwise it stays unaddressed and the first PC agent to ask takes it
  (as before). A job addressed to an offline agent waits until that agent heartbeats.
- Local mode (`LPL_MODE=local`) is unchanged: `job_create` returns before any of this.

Rule in one line: **phones only get what is addressed to them; LPL never addresses a phone by itself.**

Web (`store.tsx` `editInDarktable`, `editOn`; `components/DevicePicker.tsx`; `devices.ts`):
- "Edit in darktable" (every place: photo page, inspector, large view, board, key E) asks "Edit … on which
  device?" when more than one agent is known (any os, online or not), or when the only known agent is a
  phone. The list shows each device's name and online/offline. Suggested: the device last chosen in this
  browser (`localStorage["lpl.editDevice"]`) if it still exists, else an online PC agent, else any PC agent,
  else the first one. The job is addressed to the chosen agent.
- With a single non-Android agent nothing is asked; behaviour as before.
- Local mode: unchanged (opens darktable on this PC directly).

## 4. Resumable download (R4)

`GET /api/photos/{id}/original` is a Starlette `FileResponse`. Starlette answers `Range` itself since 0.39;
before 1.0 its 416 answer had a malformed `Content-Range: */<size>` (without `bytes`). The Dockerfile
installs the newest FastAPI/Starlette (Starlette 1.10.0 at the time of writing), which is correct:

- no Range: `200`, `Accept-Ranges: bytes`
- `Range: bytes=0-99`: `206`, `Content-Range: bytes 0-99/<size>`
- `Range: bytes=500-` (resume): `206`, the rest of the file
- `Range: bytes=-50`: `206`, the last 50 bytes
- end beyond the file: clamped, `206`
- start at or beyond the end: `416`, `Content-Range: bytes */<size>`

No code change; `server/requirements.txt` now says `starlette>=1.0` (used by the Windows agent build and
manual installs; note the Dockerfile does not read requirements.txt, it installs the newest versions).
The Android app should resume with `Range: bytes=<bytes already on disk>-` and, to be safe against a
changed file, compare `size`/`mtime` from `/checkout` (or send `If-Range` with the `ETag`/`Last-Modified`
of the first response; Starlette honours it).

## 5. Where a photo is (R6)

`photoWhere()` in `devices.ts`, shown by `WhereLine` (`components/Inspector.tsx`) on the phone's photo page
and in the desktop inspector (one photo selected): one line per agent whose last reported `working_set`
contains the photo, e.g. "on Phone: edited · not synced yet", "on Desktop: open in darktable". Offline agents
count too (a phone is offline whenever the app is in the background). The status words are the
working-set screen's (`STATUS`, moved from `Work.tsx` to `devices.ts` so they can be tested):

| status | words |
|---|---|
| downloading | copying from the NAS (was "copying to this PC") |
| opening | opening in darktable |
| editing | open in darktable |
| dirty | edited · not synced yet |
| syncing | syncing |
| synced | synced to NAS |
| error | error |
| anything else | shown as sent |

The Android app's "waiting to sync" is LPL's "edited · not synced yet" (`dirty`).

## 6. Devices by name (R8)

Every agent is called by the `name` it sends in its heartbeat (set on the device; the Android app sends the
name its user chose in its settings, not a model). LPL never renames agents. When two agents have the same
name, LPL adds the os, and if that is equal too, the first 8 characters of the id: "Desktop (windows)",
"Desktop (windows, pc-aaaa1)". (`agentLabel()` in `devices.ts`.)

Strings changed:

| File | Before | After |
|---|---|---|
| App.tsx (top bar) | "*n* PCs ready" | "*n* devices ready" |
| App.tsx (top bar) | "no PC agent" (hint "Start the LightPhotoLibrary agent on your PC" unchanged) | "no device ready" |
| App.tsx (top bar) | name / list of names | the same, with `agentLabel` |
| Mobile.tsx | bottom tab and screen title "PC" | "Devices" |
| Mobile.tsx (photo page) | "Opens on your PC (or as soon as it's online). …" | "Opens on one of your devices (or as soon as it's online). …" |
| Work.tsx | "Photos copied to a PC so darktable can edit them at SSD speed. …" | "Photos copied to your devices so darktable can edit them at SSD speed. …" |
| Work.tsx | "copying to this PC" | "copying from the NAS" |
| Work.tsx (toast) | "Sent to the PC agent" | "Sent to *name*" |
| Work.tsx | "Remove saved copies from PC" | "Remove saved copies from *name*" |
| Work.tsx | "Done · remove from PC" | "Done · remove from *name*" |
| Work.tsx | "Nothing checked out on this PC." | "Nothing checked out on *name*." |
| Work.tsx | "This PC also has a local library: on that PC, open …" | "*name* also has a local library: on *name*, open …" |
| Work.tsx (Recent hand-offs) | the agent's id | its name |
| Work.tsx (titles, confirm) | `a.name` | *name* (with `agentLabel`) |
| store.tsx (toast) | "Sending … to darktable on *a.name*…" | same with `agentLabel` |
| store.tsx (toast, new) | — | "Queued. It opens in darktable as soon as *name* is online." (device chosen in the picker is offline) |
| new | — | picker: "Edit in darktable on which device?" / "It opens the next time the darktable app is opened on that device." (offline phone) / "It opens as soon as that device is online." |

Left as they are, on purpose:
- "No PC agent has connected yet. Install it on your Windows (or Linux) PC …" (about installing that program).
- "waiting for a PC" (Recent hand-offs, unaddressed job): correct, only PC agents take unaddressed jobs.
- "Queued. It opens in darktable as soon as your PC agent is online." (only when no agent or a single PC
  agent is known; the job is unaddressed and only a PC agent takes it).
- Settings: "every PC and the phone get the same" (about browsers, not agents).
- Local mode's "this PC" texts (Send to NAS, folders): that really is the PC LPL runs on.

Every action that can target one of several devices either sits in that device's card on the Devices
(working set) screen, with the device's name in its label or title, or goes through the picker.

## 7. What the Android app must know

- Heartbeat: `POST /api/agents/heartbeat`, header `x-lpl-token: <password>` if LPL has one. Sample body:

```json
{
  "id": "android-3f6c2a9e-0d1b-4c7e-9a55-1b2c3d4e5f60",
  "name": "Phone",
  "os": "android",
  "state": {
    "working_set": [
      {"id": 1234, "filename": "DSCF0001.RAF", "status": "dirty", "checkout_ts": 1795070000.5, "synced_ts": 0, "error": ""},
      {"id": 1235, "filename": "DSCF0002.RAF", "status": "synced", "checkout_ts": 1795070010.0, "synced_ts": 1795071000.0, "error": ""}
    ],
    "darktable_running": true,
    "bridge_alive": true,
    "version": "0.1.0 / darktable 5.2.1",
    "message": ""
  }
}
```

  `os` must be exactly `"android"` (lower case) in every heartbeat: that is what keeps PC jobs away from
  the phone. `name` is shown everywhere as the device's name. Answer: `{"jobs": [...], "server_time": …}`;
  the phone only ever gets jobs addressed to its `id`. Mark each with `POST /api/jobs/{id}/done`
  (`{"status": "done"}` or `"failed"`). A job is handed out once (status `taken`); if the app dies before
  doing it, it is not handed out again.
- Jobs for the phone are created only by the user choosing the phone in the picker ("Edit in darktable"
  on another device). "Edit on this phone" creates no job.
- `lpldt://edit` parameters: see section 1. `server` is `location.origin` of the page (scheme, host, port;
  no path, no trailing slash). `ids` is in LPL's order: keep it.
- Downloads: section 4. Upload of XMP and preview: unchanged.
- "Open in LightPhotoLibrary": `<server>/?photo=<id>` (section 2).
- The working-set screen ("Devices" tab on the phone) shows the phone's card with buttons
  "Save edited to NAS", "Remove saved copies from *name*", and per photo "Open in darktable",
  "Save edits to NAS", "Done · remove from *name*". These create `sync`, `release`, `open` jobs addressed
  to the phone, the same kinds and payloads (`{"ids": [...]}`) the PC agent gets.

## 8. LPL inside the Android shell

The shell (another repository) shows LPL's web interface full screen in an Android web view on LPL's
plain http address and adds ` LPLShell/<version>` to the user agent.
- `inShell()` in `devices.ts` checks for `LPLShell/` in the user agent (unit-tested).
- "Edit on this phone" is shown in the shell too (a web view's user agent contains "Android" and "; wv";
  tested). The shell intercepts the `intent://` link itself and reports a missing editor app natively,
  so LPL's own "didn't open" note is not shown inside the shell.
- No `window.open`, no service worker and no downloads were added by this work; LPL's own pages are
  same-origin links.
- To check in the shell (existing code, not changed): on the Devices screen, a PC's card can show
  "*name* also has a local library: on *name*, open <link>" with `target="_blank"` (another origin, the
  PC's local LPL; the shell should send it to the browser, and it is only reachable from that PC anyway).
  There is no `<input type="file">` in the web app, and no `window.open`.

## 9. What changed where

| File | Function / part | Change |
|---|---|---|
| server/lpl/api.py | `heartbeat` | Android agents only take jobs addressed to them |
| server/lpl/api.py | `job_create` | automatic choice ignores Android agents |
| server/requirements.txt | | `starlette>=1.0` |
| server/requirements-dev.txt | new | pytest, httpx |
| server/tests/ | new | `conftest.py` (generated photos, fresh data folder), `test_android_handoff.py` |
| web/src/devices.ts | new | `STATUS` (moved from Work.tsx), `isPhoneAgent`, `isAndroidBrowser`, `inShell`, `agentLabel`, `defaultAgent`, `needsDeviceChoice`, `photoWhere`, `phoneEditLink`, `parseDeepLink`, `takeDeepLink` |
| web/src/components/PhoneEdit.tsx | new | `EditOnPhone` link + "didn't open" note, `useListTitle` |
| web/src/components/DevicePicker.tsx | new | the device picker |
| web/src/store.tsx | `editInDarktable`, new `editOn`, `devicePick` | picker, names in toasts |
| web/src/App.tsx | `TopBar`, new `useDeepLinkDesktop`, `Shell` | names, deep links, picker |
| web/src/components/Mobile.tsx | `PhotoPage`, `MobileApp` | link, where line, deep links, "Devices" |
| web/src/components/Board.tsx | selection bar | link on the phone |
| web/src/components/Inspector.tsx | new `WhereLine`, `Inspector` | where line |
| web/src/components/Work.tsx | `WorkView` | names instead of "PC", `STATUS` import |
| web/public/android-app.html | new | "app not installed" page (download location is a placeholder) |
| web/tests/devices.test.ts, web/package.json `test` | new | Node's built-in test runner, no new packages |

## 10. Tests

```
# server (Python 3.10+; exiftool on the PATH is used by the scan, as in the image)
python -m venv venv && venv/bin/pip install -r server/requirements-dev.txt
cd server && ../venv/bin/python -m pytest -q tests

# web (Node 22.6 or newer for --experimental-strip-types)
cd web && npm ci && npm test && npm run build
```

Server tests: job routing (unaddressed job goes to the PC and never to the phone, waits for a PC, addressed
jobs go only to their agent, automatic choice ignores the phone, two PCs unchanged) and Range requests
(none, first bytes, middle, from the middle to the end, suffix, end past the file, beyond the end).
They fail on the code before this branch (routing) and with Starlette 0.36 (no Range at all) or 0.39 to
0.52 (416 without `bytes`).

### By hand
Start LPL on a folder of test photos, then (`B=http://<lpl>:8080`):

- R3: `curl -XPOST $B/api/agents/heartbeat -H content-type:application/json -d '{"id":"android-test","name":"Phone","os":"android","state":{}}'`
  then `curl -XPOST $B/api/jobs -H content-type:application/json -d '{"kind":"edit","payload":{"ids":[1]}}'`:
  `agent_id` is `null`; heartbeating again as the phone returns no jobs; a heartbeat with `"os":"windows"`
  and another id returns the job. In the web app with two agents known, "Edit in darktable" shows the picker.
- R4: `curl -r 0-99 -o part $B/api/photos/1/original -D -` shows `206` and `Content-Range`;
  `curl -C - -o part $B/api/photos/1/original` completes it; `curl -r 999999999- $B/api/photos/1/original -D -` shows `416`.
- R1: open LPL in Chrome or Firefox on an Android phone (or desktop Chrome's device mode with an Android user
  agent), open a photo: "Edit on this phone" appears under "Edit in darktable"; long-press it to copy the
  link and check it. Without the app installed: Chrome shows android-app.html, Firefox shows its prompt and
  then (after about 2 s) LPL's note.
- R2: open `$B/?photo=1` and `$B/?series=1` on phone and desktop; the address bar loses the parameter;
  `$B/?photo=999999` shows "Not Found".
- R6/R8: send the heartbeat above with a `working_set` containing a photo id and status `dirty`, open that
  photo: "on Phone: edited · not synced yet". The Devices tab / Working set names the phone in its buttons.
