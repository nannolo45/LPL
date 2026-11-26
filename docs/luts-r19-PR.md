# PR: R19 LUT folder (LUT 3D files through LPL) — branch `p6b-lut-folder`

Work package P6b of darktable-android (its branch `p6b-lut3d-files`, report `docs/experiments/P6b-lut3d-files-REPORT.md` there). Contract: `docs/luts-r19.md` (copy of darktable-android PLAN.md 4.6).

## What it does
- **Server** (`server/lpl/luts.py`, mounted in `api.py`, tables made by `db.migrate`): `GET /api/luts?since=`, `GET /api/luts/file?path=` (Range, `x-sha256`), `PUT /api/luts/file?path=&agent=` (streamed, `x-sha256` checked, optional `x-base-sha256` → 409 `changed_elsewhere`), `DELETE /api/luts/file?path=` (web UI: removes from LPL only), `PUT /api/luts/state?agent=` (each device's list), `GET /api/luts/overview` (web UI). `luts` in `server_caps`. Files are stored by content under `DATA_DIR/luts/<2 hex>/<sha256>`; a replaced content stays as history (never deleted). Limit 64 MB per file (`LPL_MAX_LUT_MB`).
- **PC agent** (`agent/lpl_agent.py`, class `LutSync`): `luts` in caps; the LUT folder from `darktablerc` (`plugins/darkroom/lut3d/def_path`, in the darktable config folder the agent already uses, `darktable_configdir` overrides it); none set → nothing offered, the reason shown in the Devices tab. At start and every 10 minutes: sends new or changed files (a file never synced is sent with an empty base, so it does not replace what LPL has; a change made on the PC wins); takes in what it lacks only into empty places (temporary file, hash checked, hard link or exclusive create: never over a file); never deletes; a file the user deleted is not taken back. State in `luts.json` beside `shared.json`.
- **Web UI** (Devices page, below "Styles, presets and module layouts"): "LUT files": each device's LUT folder or why it has none, every file with size, last sender, earlier versions, a dot per device, "Remove from LPL" (asks first).
- **Docs**: `docs/luts-r19.md` (contract), README and `agent/README.md`.

## How to check
```
cd server && python -m pytest -q                       # 292 passed (test_luts.py 32, test_pc_agent_luts.py 9)
cd web && npm ci && npm run build && npm test           # 93 passed (luts.test.ts)
python web/tests/luts-check.py <server python> <dir>    # 3 rendered checks, screenshots in docs/shots/luts-r19/
```
One existing test changed: `test_pc_agent.py` expects the agent's caps list with `luts` added.

## What the owner must do after merging
1. **Rebuild / pull the NAS image** (new tables and calls; the tables are created at start, nothing to migrate by hand).
2. **Update the agent on each PC** (Windows: the new `lpl-agent.exe` or `lpl_agent.py`; Linux: `lpl_agent.py`, then restart the service).
3. In darktable on each PC: preferences › processing › "LUT 3D root folder" must be set, otherwise that PC offers no LUT files (the Devices tab says so).

## Open questions
- History is kept without a limit (one copy per distinct content). Fine for LUTs of a few MB; a cap can be added if the NAS should never hold more than a set size.
- A file found on a PC that joins later is not sent over a different one LPL already has (it shows "different"): the brief said "newest upload wins between PCs"; here newest *change* wins. Reviewer to confirm.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01SnXMKoZyuuWXQcumWd2fsR
