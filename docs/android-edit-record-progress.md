# Who edited last, and sync progress (R13 to R16)

Branch `android-edit-record-progress`, from `main` 0f1139c. LPL's side of requests R13, R14, R15 and R16 in
`docs/PLAN.md` section 4.3 of the darktable-android repository, plus the APK name on the help page.
Nothing here was run against the real Android app or a real darktable; the PC agent's code was run against
the server, darktable was not started. XMPs in the tests are real darktable sidecars (see below).

## Report

### Not done or not met (first)
- **R14's path is my choice**: PLAN 4.3 names no path. I added `GET /api/auth/token`. The Android app's client
  (P2e report, request 2) suggested "`/api/auth` (or a small agent call)"; `/api/auth` keeps its old answer.
  If the app wants another path, it is a one-line change.
- **Field names of the edit events are my choice** (PLAN gives the endpoint and the content, not the JSON): see
  "What the app and the PC agent send and receive".
- **"rendering preview" is not sent by the PC agent**: darktable renders the preview through the Lua bridge
  and the agent only sees the finished JPEG. The agent sends `downloading` (with progress), `sending preview`
  and `sending edit` (no progress: one small request each).
- **The first change outside LPL after the update is not reported** for a photo LPL has no record of: the
  scanner only remembers its state (it cannot know what changed, and the change may be a rating only). Every
  later outside change is reported. A full rescan records the state of every photo at once.
- **Fixed after review (commit 0de8126):** the test data was first under `server/tests/data/`, and `.gitignore`
  ignores every folder named `data/`, so it never reached the branch (19 of 48 tests failed in a fresh clone);
  the module-names place `server/lpl/data/` was ignored too. Now `server/tests/fixtures/` and
  `server/lpl/resources/`. `.gitignore` and `.dockerignore` are unchanged. A test
  (`test_files_this_needs_are_not_ignored`) fails if git or `.dockerignore` would leave these paths out.
- **Docker image build: never run.** Docker runs in the workspace, but the proxy blocks Docker Hub (and
  mirror.gcr.io), so the base images cannot be pulled. What was checked instead: see "Where `module-names.json` goes".
- **Windows exe: not built** (no Windows here). The same PyInstaller options were run on Linux (below).
- Module names: no `module-names.json` exists yet; every name shown today is the operation (`colorbalancergb`).
- **`module-flags.json` must be generated** (shape below) and copied to `server/lpl/resources/`. Without it,
  only darktable's own presets (exposure, color calibration, sigmoid/filmic, "auto" orientation) are recognised
  as automatic; raw black/white point, demosaic, white balance, highlights, input/output profile and gamma are
  then listed in the line of a photo that was only opened.
- **Automatic modules, what the method gets wrong** (method in "Modules darktable applies by itself"):
  - after darktable's "compress history", a default module the user changed (e.g. white balance) has one item
    again; if it still sits in the leading run, it counts as automatic and is left out (test
    `test_known_limit_compressed_history`). A change of exposure or sigmoid after compressing is found only if
    darktable renamed the instance (it drops the preset name when the settings no longer match the preset);
  - a preset of the user's own that darktable applies automatically (not "_builtin_") counts as the user's;
  - default_enabled comes from a schema extracted with one raw file (mire1.cr2); for JPEGs darktable does not
    apply raw modules at all, so nothing is lost, but a module whose default depends on the camera could differ;
  - entries recorded before this round (not on `main`, only on this branch) have no item positions: nothing in
    them is marked automatic.
  When `changes` is not null (an earlier XMP to compare with), none of this matters: `changes` is shown.
- **Time zone in the image not proven**: the Dockerfile now installs `tzdata` (python:3.12-slim may come without
  it, and then `TZ` is ignored silently); the image build could not be run (see above).
- Never run: the real Android app, real darktable, real phones (headless Chrome only).

### Item by item
| Item | State | Where |
|---|---|---|
| R15 `agent=` on XMP posts, old posts accepted | done | `api.py` `put_xmp` |
| R15 edit event per accepted post (photo, device id, name then, time, modules, changes) | done | `edits.py`, `api.py` `_record_post` |
| R15 history read from the XMP (`history_end`, `num`, instances, on/off, settings fingerprint, no history) | done | `xmp.py` `history` |
| R15 scanner: changed XMP no agent posted = "edited outside LPL" | done (see limit above) | `scanner.py`, `edits.observe_scan` |
| R15 `GET /api/photos/{id}/edits`, newest first; `last_edit` in `GET /api/photos/{id}` | done | `api.py` |
| R15 web: "last edited on … · … · …" and the list behind it, desktop and phone | done | `components/EditRecord.tsx`, `src/edits.ts` |
| R15 module names from `module-names.json`, fallback to the operation | done (file not there yet) | `edits.py` |
| R15 PC agent sends `agent=` | done | `agent/lpl_agent.py` `sync` |
| R16 server keeps `step`/`progress` with the item | done | `api.py` `_clean_progress`, `list_agents` |
| R16 web: bar with the step on the photo and in Devices, gone when the step ends or the device stops | done | `devices.ts` `itemProgress`, `photoActivity`; `Inspector.tsx` `WhereLine`; `Work.tsx` |
| R16 PC agent sends step and progress | partly ("rendering preview" not, see above) | `agent/lpl_agent.py` |
| R13 job handed out again | done | `api.py` `heartbeat`, `config.py` `JOB_RETRY_SECONDS` |
| R14 header-only check | done (path chosen by me) | `api.py` `auth_token` |
| Help text names `dt-android-arm64-v8a.apk` | done | `web/public/android-app.html`, `docs/android-handoff.md` |
| Database change with in-place migration, tested from main's schema | done | `db.py` `migrate` |
| Owner's round 2: modules darktable applies by itself marked `auto`, left out of the summary ("darktable's default edit"), all modules behind each entry | done (limits above) | `edits.automatic`, `xmp.history` (`first`, `second`), `EditRecord.tsx` `EditEntry` |
| Owner's round 2: saves of one device on one photo on one day are one entry, changes against the state before the run | done | `edits.record`, `edits.day_of`, `TZ` (`config.tz`), Dockerfile `tzdata` |

### Tests and results
```
python -m venv venv && venv/bin/pip install -r server/requirements-dev.txt
cd server && ../venv/bin/python -m pytest -q tests        # 64 passed (13 before this branch)
cd web && npm ci && npm test && npm run build              # 27 passed (16 before), build passes
```
Run here with Python 3.13, FastAPI 0.165.0, Starlette 1.10.0, Node 22.23.

**From a fresh clone (as the reviewer checks), commit 58a9a7e (the commits after it change only this report):**
```
git clone --branch android-edit-record-progress https://github.com/nannolo45/lpl fresh-lpl && cd fresh-lpl
python3 -m venv ../fresh-venv && ../fresh-venv/bin/pip install -r server/requirements-dev.txt
cd server && ../../fresh-venv/bin/python -m pytest -q tests   # 64 passed, 1 warning (Starlette's httpx notice)
cd ../web && npm ci && npm test && npm run build              # tests 27, pass 27, fail 0; built
```
`git status --short --ignored` in the working clone and in the fresh clone lists only `agent/__pycache__/`,
`server/.pytest_cache/`, `server/lpl/__pycache__/`, `server/tests/__pycache__/`, `web/dist/`, `web/node_modules/`,
`web/tsconfig.tsbuildinfo`: caches and build output, nothing the branch needs.

| File | Tests | What |
|---|---|---|
| `server/tests/test_edit_record.py` | 20 | history of real XMPs: default pipeline, one module added, `history_end` before the last item, the same instance twice (last counts), five exposure and two color balance instances, no history, darktable 5 sidecar with preset instance names, changed settings, switched on/off; module-names file present, missing, entry missing, unreadable; events from posts (device and its name then, changes, summary, `last_edit`), PC then phone, old agent without `agent=`, unknown agent id, XMP without history, scanner "outside", rating-only change is no edit, outside change found at the next post, first scan only remembers; the names file and the fixtures are not ignored by git or `.dockerignore` |
| `server/tests/test_jobs_auth_progress.py` | 13 | R13: handed out again only after the time, only to the agent that took it, never when done or failed, at most 3 times, off with 0, jobs taken before the update left alone; R14: header right / wrong / missing, cookie alone refused, rest of the API unchanged, no password; R16: step/progress kept, cleaned, old items unchanged, dropped when the device is offline |
| `server/tests/test_edit_merge_auto.py` | 14 | automatic modules with real XMPs: only opened (darktable's default pipeline; darktable 5 with its workflow presets) = "darktable's default edit", the user's module after the defaults, a rotated photo (flip's second item), multi-instance file, no flags file, the compressed-history limit, `auto` in the API with every module kept, old entries; merging: three exposure steps = one "exposure" against the state before the run, switched off and on again = "no module changed", another device in between, outside in between, a new day in Pacific/Auckland splits what is one UTC day, the same two saves in UTC are one entry, an unknown zone falls back |
| `server/tests/test_migration.py` | 3 | migration of main's schema with 20 000 photos (every old row unchanged, new tables and column, runs twice, integrity check); the real start-up on that library in its own process; `edit_events` as first made on this branch gets the new columns, rows kept |
| `server/tests/test_pc_agent.py` | 1 | the real `lpl_agent.py` against the server over HTTP: `downloading` 0 → 1, `sending preview`, `sending edit`, no step after; the event names the agent and its name; a step left in the state file is dropped on restart |
| `web/tests/edits.test.ts` | 11 | the last-edit line (device, time, summary; outside / import / local / unknown; same names told apart; "darktable's default edit"), the list's lines, automatic modules left out and listed under "all modules", several saves as one entry ("today 11:00–14:05 · 5 saves"), the bar appearing, going away when the step ends, when the device is offline, after 15 s by the server's clock when the page stops getting answers; old agents |

Rendered check in headless Chrome (optional, needs the Python package `playwright` and Google Chrome; not in
`npm test`): `python web/tests/browser-check.py <python with server requirements> <screenshot folder>` after
`npm run build`. Result here: OK on desktop (1400 x 900) and phone (390 x 844, Android Chrome user agent):
line ("· exposure" for three saves of the desktop), the list with 2 entries (the desktop's 3 saves as one entry;
the phone's opening as "darktable's default edit", its 8 modules under "all modules" marked automatic), a photo
only opened showing "darktable's default edit", bars in the inspector, on the phone's photo page and in Devices; after the phone's
heartbeats stopped the bar went away (14 s after the check began waiting) and "on Phone: copying from the NAS"
was shown instead.

### Screenshots (`docs/shots/edit-record-progress/`)
- `desktop-last-edit-and-progress.png`: inspector with the line, the open list and the phone's download bar
- `desktop-devices-progress.png`: Devices (Working set) with "sending the edit" (busy bar) and "downloading · 43%"
- `desktop-only-opened.png`: a photo that was only opened in darktable: "darktable's default edit"
- `phone-last-edit-and-progress.png`, `phone-list-of-edits.png`: the phone's photo page (the list with
  "3 saves" and "all modules" opened)
- `phone-after-device-stopped.png`, `desktop-after-device-stopped.png`: the bar gone, the plain state back

The browser check runs without `module-names.json`, so operation names (`rawprepare`) are shown, and with the
generated `module-flags.json` placed in a copy of the server.

### Database migration
- New tables `edit_events` (one row per edit) and `edit_baseline` (the modules of the XMP LPL last recorded
  per photo), new index `idx_edit_events_photo`, new column `jobs.attempts` (default 0).
- `edit_events` has `first_ts`, `saves`, `before` (merging); an `edit_events` table as first made on this
  branch gets them by `ALTER TABLE ADD COLUMN` (tested), its rows kept as they are.
- Only `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ADD COLUMN`; nothing is dropped or rewritten. It runs
  at every start (`db.migrate`, called by `db.init`) and does nothing the second time.
- Tested from main's schema: `server/tests/fixtures/schema-main-0f1139c.sql` was made by running main's
  `db.init()` on an empty folder and listing `sqlite_master`. A library of 20 000 photos with labels, tags,
  series, frames, shapes, agents, jobs, sent records and settings is migrated in 0.007 s; every row of every
  table main has is compared before and after; then the real server starts on it in a separate process.
- Old `taken` jobs get `attempts` 0 and are never handed out again (they may be weeks old).
- Edits made before the update have no events; the line appears with the first edit after it.

### What the Android app and the PC agent send and receive
**XMP post (R15)**: `POST /api/photos/{id}/xmp?checkout_ts=<server_time of the checkout>&agent=<agent id>`, body
the XMP. `agent` is the same id as in the heartbeat (URL-encoded). Optional: without it LPL records the post too,
naming the device only if exactly one device has the photo in its working set. Answer unchanged (the stored XMP,
`X-LPL-Merged: 0|1`).

**Edits (R15)**: `GET /api/photos/{id}/edits` (optional `limit`, default 200, at most 1000) →
`{"edits": [event, …]}` newest first; `GET /api/photos/{id}` has `"last_edit": event | null`. An event:
```json
{"id": 12, "photo_id": 3, "time": 1795070000.5, "source": "agent",
 "agent_id": "android-3f6c…", "device": "Phone", "device_os": "android",
 "modules": [{"operation": "exposure", "name": "exposure", "instance": "", "priority": 0, "enabled": true, "label": "exposure", "auto": false}],
 "changes": {"added": [], "removed": [], "switched_on": [], "switched_off": [], "changed": [ …modules… ]},
 "summary": "exposure, color balance rgb",
 "first_time": 1795068000.0, "saves": 3}
```
New in round 2, optional for clients (a client that knows only the other fields keeps working):
`modules[].auto` (darktable applied it by itself and nobody changed it; only in `modules`, not in `changes`),
`first_time` (the first save of the entry; equals `time` for one save) and `saves` (how many saves the entry holds).
- `source`: `agent` (a post), `outside` (the scanner found the XMP changed; also when a post finds the file it
  replaces changed since LPL's last record), `import` (sent from a PC's local library), `local` (darktable on
  the PC of a local library). `device`/`device_os`: the agent's name and os when the edit came; null when unknown.
- `modules`: the module instances of the edit, most recently changed first; `name` from `module-names.json`,
  else the operation; `label` = name plus the instance name (darktable's `_builtin_` prefix left out).
- One entry per run: consecutive saves on one photo with the same `source` and `agent_id` (outside counts as its
  own source) on the same calendar day of the server (time zone `TZ`) are one entry; `time` and `id` stay the
  entry's (`time` moves to the last save). Another device or source in between, or a new day, starts a new entry.
- `changes`: against the XMP LPL had before the entry's first save; `null` when there was nothing to compare with. `changed` = settings
  (`params`, `blendop_params`, `modversion`) or the instance name differ; settings are never decoded.
- `summary`: what the one-line display shows after the time: the changed modules (at most 4 names, "and N more");
  when there is nothing to compare with, the modules that are on and not `auto`, or "darktable's default edit"
  when there are none; "no module changed"; "no darktable edit" (no history).
  The web UI shows `last edited on <device> · <time> · <summary>`.

**Heartbeat (R16)**: a `working_set` item may add `"step": "downloading" | "sending edit" | "rendering preview" |
"sending preview"` and `"progress": 0..1`. Send the item without them when the step ends. LPL keeps them as sent
(a progress outside 0..1 is clamped, one that is not a number dropped, an empty step dropped) and leaves them out
of `/api/agents` and `/api/status` once the device is offline (15 s without a heartbeat). Unknown steps are shown
as sent.

**Jobs (R13)**: a job that is `taken` and never reported done (`POST /api/jobs/{id}/done`) comes back, with the
same id, in a heartbeat of the same agent after `JOB_RETRY_SECONDS` (default 1800), at most 3 hand-outs in all.
So an app must still report every job, and should treat a job id it already ran as done (report it again).

**Password check (R14)**: `GET /api/auth/token` with header `x-lpl-token: <password>` → 200
`{"required": true, "ok": true}`; wrong or missing → 401 `{"detail": "login required"}`; LPL without a
password → 200 `{"required": false, "ok": true}`. The login cookie is not looked at.
`GET /api/auth/check` is the same call under a second name.

**Checkout integrity**: `GET /api/photos/{id}/checkout` also carries `sha256` (hex) and `size` of the original file
as it is on disk now (the answer's older `size` is the same number unless the file changed since the last scan).
The hash is computed once per path + mtime + size and kept in memory, so a RAW is not read again on every request;
after a server restart the first checkout of a photo hashes it once. A missing file: both keys are left out.
`GET /api/photos/{id}/original` is unchanged (answers Range).

**Late report (R13)**: a job handed out again may still be reported done by its first taker; `POST /api/jobs/{id}/done`
is accepted, a repeat changes nothing, and a done job is never handed out again.

**PC agent**: sends `agent=` with every XMP post and step/progress as above. Agents from before keep working.

### Where `module-names.json` goes
`server/lpl/resources/module-names.json` (folder to create), a JSON object `{"<operation>": "<English name>"}`,
as generated by the Android repository at `schema/module-names.json`. LPL re-reads it when it changes; missing,
unreadable or without an entry: the operation is shown.
- **git**: not ignored (`git check-ignore --no-index` prints nothing; the old place `server/lpl/data/` was ignored).
- **Docker image**: the Dockerfile's `COPY server/lpl ./lpl` puts it at `/app/lpl/resources/module-names.json`;
  `.dockerignore`'s `data` only matches the top-level folder. Checked with Docker itself (the full image could
  not be built, see above): a `FROM scratch` build with the same `COPY server/lpl ./lpl`, from a clean export of
  the branch with a test file placed at the new location, exported with `--output type=local`, has
  `app/lpl/resources/module-names.json`; LPL's lookup run on that exported folder answers "color balance rgb" for
  `colorbalancergb` and "sigmoid" (not in the file) for `sigmoid`.
- **Windows exe**: `build-windows-exe.ps1` and the workflow `agent-windows.yml` now pass `--collect-data lpl` and
  set `PYTHONPATH` to `..\server` while building (without it PyInstaller says "skipping data collection for module
  'lpl' as it is not a package" and leaves the file out). Checked on Linux with PyInstaller 6.22.3, `--onefile
  --paths server --collect-submodules lpl --collect-data lpl` and a probe script: with the file placed, the frozen
  program finds it under its unpack folder (`…/_MEI…/lpl/resources/module-names.json`) and answers
  "color balance rgb"; without the file the build still succeeds and shows the operation.

### Modules darktable applies by itself (`module-flags.json`)
Method (`edits.automatic`, documented in the code). When darktable first opens a photo it writes, at the start
of the history and before anything the user does, one item for each module that is on by default, its internal
modules, and one per preset it applies automatically, which it names after the preset with the prefix
`_builtin_` (found in the darktable 5 test sidecar: `channelmixerrgb`, `exposure`, `sigmoid`
"_builtin_scene-referred default", `flip` "_builtin_auto"). So the leading run of the history whose items are
each such a module, and where no instance appears a second time, is darktable's own; it ends at the first item
of any other module or at any instance's second item. An instance whose only item lies in that run is
`auto`. Internal modules (gamma, mask manager) are always `auto`. Derived from the XMP plus generated data,
no hand-written module list; `xmp.history` now keeps each instance's first and second item number for this.

`server/lpl/resources/module-flags.json` (next to `module-names.json`; same rules for git, Docker and the exe,
see below), a JSON object, operation → flags:
```json
{"rawprepare": {"default_enabled": true, "hidden": false}, "gamma": {"default_enabled": false, "hidden": true}, "exposure": {"default_enabled": false, "hidden": false}}
```
Generated from the darktable-android repository's `schema/ui-schema.json`: `default_enabled` = the module's
`default_enabled` is `true`; `hidden` = its `status` is `"hidden"`. The command is in
`server/tests/fixtures/xmp/SOURCE.md`; `server/tests/fixtures/module-flags.json` is its output for schema
commit 37b8f27 (14 operations flagged: colorin, colorout, demosaic, demosaicscale, finalscale, flip, gamma,
highlights, mask_manager, overexposed, rawoverexposed, rawprepare, rotatepixels, temperature). LPL re-reads it
when it changes. Missing or unreadable: only single-item `_builtin_` instances are `auto` (logged once).
`hide_enable_button` was not needed: every module with it also has `default_enabled`.

### Time zone (merging by day)
The container's day follows `TZ` (`config.tz`, read with Python's `zoneinfo`; unset: the system's local time,
which in Docker is UTC, so an evening's saves after 00:00 UTC would split; unknown zone: local time and one
warning in the log). `docker-compose.yml` already had a `TZ=` line; it is now marked (4) and explained, and the
README's install step lists it. The Dockerfile installs `tzdata`, because a slim image may come without time-zone
data and would then ignore `TZ` without a word.

### R13: the time chosen
30 minutes (`JOB_RETRY_SECONDS=1800`; 0 = never, as before). The PC agent reports an `edit` job only after all
its photos are copied; 100 RAW files of 30 MB over a 20 Mbit/s remote link take about 20 minutes, so a shorter
time would hand a job out again while it still runs. A phone app that died with a job gets it back the next
time it is opened after that. At most 3 hand-outs, so a job that always kills the app does not loop forever.

### After a merge, the owner
1. Updates as always: Docker → Project → `lightphotolibrary` → pull the latest image and restart (or
   `sudo docker compose pull && sudo docker compose up -d`). The library is migrated at start, in well under
   a second; nothing to do.
2. Updates the PC agent (new Windows build from Releases, or the `agent` folder) so edits from the PC are
   named "Desktop" instead of being guessed from the working set.
3. Optional: `JOB_RETRY_SECONDS=…` in `docker-compose.yml` to change the 30 minutes.
4. Optional: a full rescan (Devices → Full rescan) to give every photo a record, so that the first edit made
   outside LPL is reported too.
5. Time zone: check that the Compose configuration of the `lightphotolibrary` project on the NAS has a line
   `- TZ=<Area/City>` with his own zone under `environment:` (the repository's file has had one; if his project
   was made from an older copy, add it). Then pull the new image (it brings the time-zone data). Without it,
   days in the list of edits end at midnight UTC.
6. The reviewer copies the generated `module-names.json` and `module-flags.json` into `server/lpl/resources/`.

### Open questions
1. R14 path `GET /api/auth/token`: fine for the app, or should `/api/auth` answer instead?
2. Event JSON field names (above): to be checked against the app's client.
(Decided by the owner and done: darktable's own modules are left out of the line; saves of one device on one
day are one entry.)

## Log
- 2026-11-19: branch from `main` 0f1139c. Baseline: server 13 tests pass, web tests and build pass.
- 2026-11-19: done: server side of R15 (history read from the XMP, `edit_events` and `edit_baseline` tables,
  `agent=` on XMP posts, `GET /api/photos/{id}/edits`, `last_edit` in the photo's detail, scanner records
  "outside" edits), R16 server part (step/progress cleaned, dropped for offline devices), R13 (`JOB_RETRY_SECONDS`),
  R14 (`GET /api/auth/token`). Test XMPs from darktable's integration tests in `server/tests/data/xmp` (moved to `server/tests/fixtures/xmp` later, see below).
  Found: darktable's `num` has gaps and the `mask_manager` pseudo-module is a history item (always off).
  Next: tests for R13, R14, R16 and the migration; PC agent; web UI.
- 2026-11-19: done: PC agent sends `agent=<id>` with XMP posts and `step`/`progress` (downloading with progress,
  sending preview, sending edit); test runs the real agent code against the server over HTTP. Migration test
  from main's schema with 20 000 photos (0.01 s). Server: 48 tests pass. Next: web UI (last-edit line, list of
  edits, progress bar), APK name in the help page, screenshots, report.
- 2026-11-19: done: web UI: last-edit line with the list of edits behind it (`components/EditRecord.tsx`, logic in
  `src/edits.ts`), progress bar on the photo (WhereLine) and in Devices (`Work.tsx`), hidden when the step ends or
  the device stops reporting (also by the server's clock followed from the last answer). Help page names
  `dt-android-arm64-v8a.apk`. Web: 25 tests pass, build passes. Next: rendered check and screenshots in a
  headless browser, the report.
- 2026-11-19: done: rendered check in headless Chrome (`web/tests/browser-check.py`), screenshots committed,
  `docs/android-handoff.md` updated (jobs are handed out again). Report written above.
- 2026-11-19: review: `server/tests/data/` was never committed (`.gitignore` ignores every `data/` folder), so
  19 of 48 tests failed in a fresh clone; `server/lpl/data/` (module names) was ignored too. Moved to
  `server/tests/fixtures/` and `server/lpl/resources/`, ignore rules unchanged, test against it added; exe build
  collects lpl's data files. Fresh clone: 49 server tests and 25 web tests pass, build passes. Docker image not
  built (Docker Hub blocked); context and COPY checked with a scratch build.
- 2026-11-19: owner's round 2: modules darktable applies by itself marked `auto` (leading run of default /
  internal / `_builtin_` preset items, from generated `module-flags.json`), "darktable's default edit"; saves of
  one device on one photo on one day merged, changes against the state before the run, day in `TZ`; tzdata in
  the image. Fresh clone at 58a9a7e: 64 server tests, 27 web tests pass, build passes; browser check OK.
