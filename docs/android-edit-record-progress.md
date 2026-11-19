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

### Tests and results
```
python -m venv venv && venv/bin/pip install -r server/requirements-dev.txt
cd server && ../venv/bin/python -m pytest -q tests        # 49 passed (13 before this branch)
cd web && npm ci && npm test && npm run build              # 25 passed (16 before), build passes
```
Run here with Python 3.13, FastAPI 0.165.0, Starlette 1.10.0, Node 22.23.

**From a fresh clone (as the reviewer checks), commit 0de8126:**
```
git clone --branch android-edit-record-progress https://github.com/nannolo45/lpl fresh-lpl && cd fresh-lpl
python3 -m venv ../fresh-venv && ../fresh-venv/bin/pip install -r server/requirements-dev.txt
cd server && ../../fresh-venv/bin/python -m pytest -q tests   # 49 passed, 1 warning (Starlette's httpx notice)
cd ../web && npm ci && npm test && npm run build              # tests 25, pass 25, fail 0; built
```
`git status --short --ignored` in the working clone and in the fresh clone lists only `agent/__pycache__/`,
`server/.pytest_cache/`, `server/lpl/__pycache__/`, `server/tests/__pycache__/`, `web/dist/`, `web/node_modules/`,
`web/tsconfig.tsbuildinfo`: caches and build output, nothing the branch needs.

| File | Tests | What |
|---|---|---|
| `server/tests/test_edit_record.py` | 20 | history of real XMPs: default pipeline, one module added, `history_end` before the last item, the same instance twice (last counts), five exposure and two color balance instances, no history, darktable 5 sidecar with preset instance names, changed settings, switched on/off; module-names file present, missing, entry missing, unreadable; events from posts (device and its name then, changes, summary, `last_edit`), PC then phone, old agent without `agent=`, unknown agent id, XMP without history, scanner "outside", rating-only change is no edit, outside change found at the next post, first scan only remembers; the names file and the fixtures are not ignored by git or `.dockerignore` |
| `server/tests/test_jobs_auth_progress.py` | 13 | R13: handed out again only after the time, only to the agent that took it, never when done or failed, at most 3 times, off with 0, jobs taken before the update left alone; R14: header right / wrong / missing, cookie alone refused, rest of the API unchanged, no password; R16: step/progress kept, cleaned, old items unchanged, dropped when the device is offline |
| `server/tests/test_migration.py` | 2 | migration of main's schema with 20 000 photos (every old row unchanged, new tables and column, runs twice, integrity check); the real start-up on that library in its own process |
| `server/tests/test_pc_agent.py` | 1 | the real `lpl_agent.py` against the server over HTTP: `downloading` 0 → 1, `sending preview`, `sending edit`, no step after; the event names the agent and its name; a step left in the state file is dropped on restart |
| `web/tests/edits.test.ts` | 9 | the last-edit line (device, time, summary; outside / import / local / unknown; same names told apart), the list's lines, the bar appearing, going away when the step ends, when the device is offline, after 15 s by the server's clock when the page stops getting answers; old agents |

Rendered check in headless Chrome (optional, needs the Python package `playwright` and Google Chrome; not in
`npm test`): `python web/tests/browser-check.py <python with server requirements> <screenshot folder>` after
`npm run build`. Result here: OK on desktop (1400 x 900) and phone (390 x 844, Android Chrome user agent):
line, list with 3 edits, bars in the inspector, on the phone's photo page and in Devices; after the phone's
heartbeats stopped the bar went away (14 s after the check began waiting) and "on Phone: copying from the NAS"
was shown instead.

### Screenshots (`docs/shots/edit-record-progress/`)
- `desktop-last-edit-and-progress.png`: inspector with the line, the open list and the phone's download bar
- `desktop-devices-progress.png`: Devices (Working set) with "sending the edit" (busy bar) and "downloading · 43%"
- `phone-last-edit-and-progress.png`, `phone-list-of-edits.png`: the phone's photo page
- `phone-after-device-stopped.png`, `desktop-after-device-stopped.png`: the bar gone, the plain state back

The test photos are flat colours and the edits come from darktable's test files (different images), so the
third edit in the screenshots changes many modules; a real session changes a few.

### Database migration
- New tables `edit_events` (one row per edit) and `edit_baseline` (the modules of the XMP LPL last recorded
  per photo), new index `idx_edit_events_photo`, new column `jobs.attempts` (default 0).
- Only `CREATE TABLE IF NOT EXISTS` and one `ALTER TABLE ADD COLUMN`; nothing is dropped or rewritten. It runs
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
 "modules": [{"operation": "exposure", "name": "exposure", "instance": "", "priority": 0, "enabled": true, "label": "exposure"}],
 "changes": {"added": [], "removed": [], "switched_on": [], "switched_off": [], "changed": [ …modules… ]},
 "summary": "exposure, color balance rgb"}
```
- `source`: `agent` (a post), `outside` (the scanner found the XMP changed; also when a post finds the file it
  replaces changed since LPL's last record), `import` (sent from a PC's local library), `local` (darktable on
  the PC of a local library). `device`/`device_os`: the agent's name and os when the edit came; null when unknown.
- `modules`: the module instances of the edit, most recently changed first; `name` from `module-names.json`,
  else the operation; `label` = name plus the instance name (darktable's `_builtin_` prefix left out).
- `changes`: against the XMP LPL had before; `null` when there was nothing to compare with. `changed` = settings
  (`params`, `blendop_params`, `modversion`) or the instance name differ; settings are never decoded.
- `summary`: what the one-line display shows after the time: the changed modules (at most 4 names, "and N more"),
  or the modules that are on when there is nothing to compare with, "no module changed", "no darktable edit".
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

### Open questions
1. R14 path `GET /api/auth/token`: fine for the app, or should `/api/auth` answer instead?
2. Event JSON field names (above): to be checked against the app's client.
3. Should LPL hide darktable's always-applied modules (raw black/white point, demosaic, input/output color
   profile…) in "modules in use" when there is nothing to compare with? Today they are listed; nothing tells
   them apart in the XMP, a list would have to come with `module-names.json`.
4. Many events per photo: the PC agent syncs a few seconds after every darktable write, so one session can
   record several events. Keep all (now), or merge events of the same device within some minutes?

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
