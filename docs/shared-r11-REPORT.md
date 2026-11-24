# R11 report: shared darktable styles, presets and module layouts (LPL side)

Brief and contract: `docs/shared-r11.md` (unchanged). Branch `shared-r11`, not merged. Screenshots (headless Chrome):
`docs/shots/shared-r11/`. All numbers are from the x86_64 build workspace.

## What was asked and what was done
| Deliverable | State |
|---|---|
| **Server** tables: `shared_items` (current body + meta, deletions kept as tombstones with their last body and hash), `shared_candidates` (one per agent and item), `shared_state` (per agent and item: rev, state), `shared_agents` (phone policy); `db.migrate` creates them | done (`server/lpl/shared.py`, `db.py`) |
| Contract calls: `GET /api/shared`, `GET /api/shared/{id}`, `PUT /api/shared`, `DELETE /api/shared/{id}`, `POST /api/shared/{id}/resolve`, `PUT /api/shared/state`, `GET /api/shared/devices`, `POST /api/shared/update` | done |
| `"shared"` in `server_caps` (only for devices that announce caps, as R18) | done (`api.py` heartbeat) |
| `shared-update` job (PC agents with the cap only; a queued one not yet taken is replaced) | done |
| Tests for every rule of the contract (first sync identical = no conflict, one-side change, stale base = 409 + candidate replaced by a newer PUT, mine / theirs / both, newest wins at PUT for phone and PC, deletion + delete / keep, kept item changed again comes back, waiting per device incl. unusable, hash vectors, update job, migration) | done: `server/tests/test_shared.py`, 20 tests |
| **PC agent**: `"shared"` in caps (left out when `darktable/module-names.json` is missing); read-only snapshot of `data.db` (sqlite backup into memory, `mode=ro`) at start, when darktable closes and every 10 min while it runs; failed snapshot skipped | done (`agent/lpl_agent.py`, section "shared styles, presets and module layouts") |
| Which presets count: operations from darktable-android `schema/module-names.json` (copied to `agent/darktable/module-names.json` with a `_source` line) + `modulegroups` for layouts, `writeprotect = 0`; all styles except darktable's own `_l10n_…` | done |
| Per-item state file (`shared.json` in the agent's data folder: id, rev, local_hash, state; plus a held job) | done |
| Uploads (PUT / DELETE), 409 -> state `conflict`, not uploaded again until resolved; "mine" / "theirs" noticed when the conflict is gone (theirs: not a local change, waits for the update) | done |
| `shared-update` job held until `darktable_running()` is false, applied in one transaction by column name (preset: insert or replace with `writeprotect = 0`; style: delete by name + items, insert; deletion: remove), `keep` = state `kept`; heartbeat `state.shared_update`; then `/done` and state PUT | done |
| Test against darktable's real `data.db` schema | done: `server/tests/fixtures/darktable-data-schema.sql` from darktable `src/common/database.c` at the commit pinned in darktable-android (04477d3: `_create_data_schema` + upgrade steps to data version 13); `server/tests/test_pc_agent_shared.py`, 6 tests (a real agent against the server over HTTP) |
| Installers and Windows package ship `module-names.json` | done (`install-linux.sh`, `install-windows.ps1`, `agent-windows.yml` package step) |
| **Web UI** Devices tab: per device "n styles, presets, layouts waiting"; opened (PC): table (checkbox, item, kind, what: new / changed / deleted on X with delete here · keep, from, when), conflicts with Keep <PC>'s / Take <other>'s / Keep both, note that darktable must be closed, Later and **Update <device> · n**; phone line "n waiting · updated from the app" without Update; "Shared through LPL" panel (item, kind, last change by and when, a dot per device) | done (`web/src/components/Shared.tsx`, `web/src/shared.ts`, `web/src/sharedApi.ts`) |
| Phone layout of the Devices tab: the table becomes a list, panel below | done |
| Settings > Devices: "Same item changed on two devices: Ask me / Newest wins" (for PCs; pref `settings.sharedPolicy`) | done |
| Mockup's "download as .dtstyle" | dropped, as the brief says |
| Report | this file |

## How to reproduce
```
cd server && pip install -r requirements-dev.txt && python -m pytest -q          # all server + agent tests
python -m pytest -q tests/test_shared.py tests/test_pc_agent_shared.py            # only R11
cd web && npm ci && npm test && npm run build
python web/tests/shared-check.py <python with server/requirements.txt> docs/shots/shared-r11   # headless Chrome, after the build
```

## Results
- Server: **238 passed** (209 before; +22 `test_shared.py`, +7 `test_pc_agent_shared.py`).
  The only edit to an existing test: `test_pc_agent.py` now expects caps `["batch", "cancel", "open", "versions", "shared"]`.
- Hash vectors (`server/tests/fixtures/r11-hash-vectors.json`): server and agent both reproduce all three; the agent also
  reproduces them through a real `data.db` row (REAL, BLOB, NULL, non-ASCII) in `test_snapshot_picks_shared_items_and_matches_the_hash_vectors`.
- Snapshot is read-only: the test checks `data.db`'s mtime does not change.
- Web: **91 passed** (85 before; +6 `tests/shared.test.ts`); `npm run build` passes (tsc + vite).
- Headless Chrome (`web/tests/shared-check.py`): **6 checks passed**: device lines, table + conflict + note + Update · 3 + panel,
  "keep" and an unticked row give payload `{"ids": [1], "delete": [], "keep": [3]}`, Keep both makes "mono red filter (Laptop)",
  the setting reaches the server, the phone shows a list.
- CI: the repository's workflows run on pushes to `main` only. The Windows agent workflow was started by hand on this
  branch (workflow_dispatch; it publishes only from `main` / tags): run **45413637587, success** (exe build, `--selftest`,
  package with `darktable/module-names.json`), on commit 78b1122. The last
  run on `main` before this branch (45401676584) failed at "Get exiftool for Windows" (download), not related to this work.

## Review round 1 (supervisor)
- Proposals 1-3 accepted; `docs/shared-r11.md` updated: `changed_at` = seconds since 1970; `hash_mismatch` 400; optional `&changed_at=` on DELETE (server uses it for newest wins, the agent sends it). Test `test_delete_carries_changed_at_for_newest_wins`.
- Safety: the agent writes `data.db` only when `data_db_writable()`: a strict process check (a failed check = running) and no `data.db.lock` naming a live pid (a stale lock of a dead pid is ignored; an unreadable lock = locked). Test `test_never_writes_data_db_unless_surely_closed`.
- No `unusable` on PCs; count only for the phone (answered).
- Web: module display names ("preset · color calibration", from the server's module-names.json; `GET /api/shared/devices` adds `module`), and "1 style, preset or layout waiting". Screenshots refreshed.

## Choices the contract leaves open (as first proposed)
1. **`changed_at`**: the server takes seconds since 1970 (number) or an ISO 8601 text and stores and returns seconds.
   Proposal for PLAN 4.5: "changed_at = seconds since 1970, a number".
2. **Hash check**: the server recomputes the hash of the body and answers `400 {"error": "hash_mismatch", "hash": …}` when the PUT's `hash` differs. Not in the contract; proposal to add it.
3. **DELETE has no `changed_at`**: with "newest wins", a stale deletion is dated by the server's clock, so it usually wins. Proposal: optional `&changed_at=` on DELETE.
4. **PUT on a tombstone**: only `base_rev` = the deletion's rev brings an item back; a PUT with another `base_rev` is a conflict even when its hash equals the last body's (the current revision is a deletion, it has no content). A device that first syncs with an item that was deleted elsewhere therefore gets a 409 for it; mine = it comes back, theirs = it is offered as a deletion.
5. After a successful PUT / DELETE and after resolve "mine" / "both", the server records that agent's state at the new rev at once (the next state PUT replaces it anyway), so the Devices page does not show the device's own change as waiting for it.
6. "Keep both" is refused (400) when either side is a deletion; when "<name> (<device>)" already exists with other content, " 2", " 3"… is added.
7. `GET /api/shared/devices` answers `{policy, devices: [{id, name, os, phone, online, policy, update, darktable_running, job, waiting: [meta + what], conflicts: [meta + candidate]}], items: [meta + devices: {agent: newest | waiting | conflict | kept | unusable | absent}]}`. `what` is `new` / `changed` / `deleted`; an item brought back after this device deleted it shows as `changed`.
8. The `shared-update` payload is the web UI's `{ids, delete, keep}`; the agent fetches each body with `GET /api/shared/{id}`. An id in `ids` that is a deletion by then is removed, as for `delete`.

## Limits and things to check on a real PC
- Not run against a real darktable: the `data.db` schema comes from darktable's CREATE statements. Worth one manual run on Windows: make a preset and a style in darktable, close it, see them in LPL, update another PC, open darktable there.
- darktable also writes each style as a `.dtstyle` file under `<config>/styles/`; the agent only writes `data.db`. Whether darktable needs those files (it reads styles from `data.db`) should be checked on a real install.
- darktable has no unique index on style names: applying a style removes every style of that name first.
- The PC agent never reports `unusable`: it cannot know the module versions of the installed darktable without running it. A preset whose `op_version` is newer than the PC's darktable is written; darktable then ignores it (to be confirmed). Proposal: the agent could read the versions from darktable's library.db `history` / built-in presets, if wanted.
- "Newest first" uploads: `data.db` rows carry no change time, so the PC uploads in snapshot order.
- The agent checks with the server at start, when darktable closes and every 10 minutes, so a conflict resolved in LPL is noticed by the PC within 10 minutes (LPL itself shows it at once).
- The server hands a taken job out again after `JOB_RETRY_SECONDS` (30 min, up to 3 times); the agent keeps one held `shared-update` job per id (also over a restart, in `shared.json`) and ignores the repeats.

## Open questions for the reviewer
1. Proposals 1-3 above for PLAN 4.5 (changed_at format, hash check, changed_at on DELETE).
2. Should the PC agent try to report `unusable` (see limits)?
3. The phone's waiting list is not shown in LPL (count only, as in the mockup); fine?
