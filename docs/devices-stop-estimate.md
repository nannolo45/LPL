# Devices page: batches, overall estimate, Stop, Resume

Branch `devices-page`. What a device (the PC agent, the darktable Android app) sends and receives so that LPL can
show "Sending 38 of 120 photos to Laptop · 1.4 GB of 4.6 GB · about 6 min left" and stop a batch.
Server: `server/lpl/transfers.py`. PC agent: `agent/lpl_agent.py`. Web: `web/src/transfer.ts`, `components/Devices*.tsx`.

## Rule: everything new is opt-in
A device that sends no `caps` in its heartbeat gets and sends **exactly** what it did before: the answer is
`{"jobs": [...], "server_time": ...}` and nothing else, job payloads are untouched, hand-out again after
`JOB_RETRY_SECONDS` is unchanged (test `test_old_agent_exchange_is_unchanged`). For such a device LPL shows no totals
and offers no Stop for a copy under way; a job it has **not taken yet** can be stopped or thinned out on the server.
Additive only, for every client: each agent in `GET /api/agents` and `GET /api/status` has the new fields `caps`,
`transfer`, `stopped`, `folded`, `folded_phone`; jobs can have the new statuses `stopped` and `cancelled`.

## Capabilities (heartbeat request, top level)
`"caps": ["batch", "cancel", "open"]`, in every heartbeat. Each is independent, except that `cancel` needs `batch`.

| cap | the device promises |
|---|---|
| `batch` | to send `transfers` (below) in every heartbeat |
| `cancel` | to act on `cancel` in the heartbeat answer |
| `open` | to honour `"open": false` in an edit job's payload: copy the photos, do not open the editor |

## Heartbeat request with `batch`
```json
{"id": "android-3f6c…", "name": "Telefono", "os": "android", "state": {"working_set": [...]},
 "caps": ["batch", "cancel", "open"],
 "transfers": [
   {"job": 41, "state": "running", "arrived": [1201, 1202], "present": [1190], "failed": [], "skipped": [],
    "photo": 1203, "bytes": 18350080}
 ]}
```
- One entry per **edit** job the device holds, from the heartbeat after it received the job until `/done` for it
  was answered. `transfers: []` when it holds none. A job the server thinks is running and that is **not listed**
  is taken for lost (the app was killed): LPL shows it as stopped, "interrupted", with what arrived.
- `state`: `queued` (received, not started), `running`, or ended: `stopped`, `done`, `failed`.
- `arrived`: photo ids copied by this job (RAW and XMP both there). `present`: were on the device already, nothing
  copied. `failed`: could not be copied. `skipped`: left out because the server said so. All cumulative and
  idempotent; all optional.
- `photo`, `bytes`: the photo being copied now and how many bytes of its original are on the device; `null` / `0`
  between files. Sizes and totals come from the server (`photos.size`); the device sends no totals.
- Timing: at least every 2 s while a batch runs (the PC agent: 1 s). The estimate is worked out from these
  reports (server keeps 90 s of `[time, bytes done, photos done]`; the web app uses the last 60 s).
- When a job ends, send a heartbeat **at once** with its final `state` and the working set as it now is, then
  `POST /api/jobs/{id}/done`.

## Heartbeat answer with `cancel`
```json
{"jobs": [{"id": 42, "kind": "edit", "payload": {"ids": [1301, 1302], "open": true}}],
 "server_time": 1795331687.6, "cancel": {"jobs": [41], "photos": [1207, 1210]}}
```
- `cancel` is always present for a device with the cap, and is the **whole** list every time (replace, do not
  add up). It is repeated until the device confirms, so a lost answer loses nothing.
- `cancel.jobs`: stop these jobs. Check between files **and between download chunks** (the PC agent reads at most
  256 KiB at a time and shuts the socket down to wake a stalled read). Then: keep what arrived, delete the partial
  file, remove the half-made working-set entry, do **not** open the editor, report `state: "stopped"`.
  A job id the device does not know: ignore. A job still waiting on the device: end it as `stopped` without starting.
- `cancel.photos`: do not copy these photos (in any job). If one is being copied: give that file up and carry on
  with the next. List them under `skipped`.
- Expected: a Stop takes effect within about 2 s of the heartbeat that carries it.
- `payload.open` is only sent to devices with `open`. For devices with `batch`, `payload.ids` has each id once.
- A job id is never handed out twice to a device that lists it. Resume always makes a **new** job id.

## `POST /api/jobs/{id}/done`
Unchanged: `{"status": "done" | "failed"}`. New: `"stopped"`, and optionally the same four lists as above
(`arrived`, `present`, `failed`, `skipped`). Calling it again is harmless.

## What the web app calls (not for devices)
| Call | Effect |
|---|---|
| `POST /api/agents/{id}/stop` | all of that device's batches: not taken yet → stopped at once; running → told via `cancel`; device offline → stopped at once and told when it is back. Nothing running: `{"ok": true, "stopped": 0}`. 409 if it runs on a device without `cancel` |
| `POST /api/agents/{id}/resume` | a new edit job with what the stop left unsent; counts carry on ("38 of 120") |
| `POST /api/agents/{id}/dont-send` `{"ids": [...]}` | takes photos out: of a job not taken (payload rewritten; empty → job `cancelled`), of a running one (via `cancel.photos`), of what a stop left |
| `POST /api/agents/{id}/dismiss` | closes the "Stopped" note |
| `POST /api/jobs/{id}/cancel` | any job still `queued` → `cancelled`; 409 once taken |
| `PUT /api/devices/fold` `{"folds": {"<id>": true/false/null}}` | fold choices, merged; the phone layout uses `"phone:<id>"` |

## Data model
- `transfers` (one row per edit job): `job_id`, `agent_id`, `run`, `state` (queued / running / stopping / stopped /
  done / cancelled / resumed / legacy), `created`, `started`, `ended`, `cancel`, `reason` (user / interrupted),
  `dismissed`, `cur_photo`, `cur_bytes`. Batches of one device that are live at the same time, and a resumed
  batch, share a `run`: one overall figure.
- `transfer_items`: `job_id`, `photo_id`, `pos`, `filename`, `size`, `state` (waiting / skipping / arrived /
  present / failed / skipped / unsent / dup).
- `agents.caps` (JSON list, NULL for devices from before). Migration: `CREATE TABLE IF NOT EXISTS` and one
  `ALTER TABLE agents ADD COLUMN`; runs at every start; existing rows untouched (test from the previous schema).
- Settings (`pref.settings`): `devicesAutoOpen`, `devicesEtaInBar`. Fold choices: `pref.devices.fold`.

## The estimate (`web/src/transfer.ts`, unit-tested)
Rate = bytes moved over the last 60 s of samples. "estimating…" until 8 s of samples or 3 photos (and 2 s), when
no report came for 12 s, or when nothing moved for 10 s. Words: less than a minute / about a minute / about N min
(steps of 5 from 10) / about an hour / about N hours. Several devices at once: counts added, the longest time shown.

## Known limits
- The Android app and the Windows exe were not run; `lpl-agent.exe` was not built. The socket shutdown that wakes
  a stalled read was exercised on Linux only; without it a Stop waits for the next bytes.
- The samples for the estimate live in the server's memory: after a server restart it says "estimating…" for a few seconds.
- A device with `batch` no longer gets a lost edit job back after `JOB_RETRY_SECONDS`: it shows as stopped with Resume.
- While a new batch runs on a device, an older "Stopped" note of that device is hidden until the batch ends.
- The PC agent now marks only the photo opened in the darkroom as "open in darktable"; the others of a batch read
  "ready to edit" (new status `ready`). Arrived photos of a stopped batch are removed with the normal `release` job,
  which saves edits first.
- Existing, unchanged: if the agent's `state.json` is lost while edited copies are on disk, sending those photos
  again overwrites the local XMP with the NAS one.
- Page vs. mockup: "Library on the NAS" is in Settings (owner's decision), so "Recent hand-offs" stands alone with a
  "Device settings" link at the top; groups show 3 rows before "show them"; a "×" closes the Stopped note.

## Tests
`server/tests/test_transfers.py` (33), `test_pc_agent.py` (+4, the real agent loop, Stop mid-file, restart),
`web/tests/transfer.test.ts` (14). Browser and real-agent runs: scripts in `/home/claude/devices-test/` on the build
workspace (`check.py`, `e2e_agent.py`), screenshots in `docs/shots/devices-page/`.
