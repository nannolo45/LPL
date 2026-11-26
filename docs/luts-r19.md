# R19: LUT folder (LUT 3D files) — LPL side

Work package P6b, LPL branch `p6b-lut-folder`. The contract below is a copy of darktable-android `docs/PLAN.md` 4.6 (branch `p6b-lut3d-files`); that copy is the source of truth. A change to the contract is made in both places in the same step. PR text: `docs/luts-r19-PR.md`.

## LUT folder (R19, brief P6b, specified 26 Nov 2026)
Owner's decision (26 Nov 2026): LUT 3D files come from either place, a folder on the phone and the PCs' darktable LUT folder, carried by LPL. Opt-in like R11: a device adds `"luts"` to heartbeat `caps` (the PC agent always; the phone once the server has listed it in `server_caps`, as with `shared`), the server lists `"luts"` in `server_caps` to a device that announces caps. A device or server without it behaves as before.

**How desktop does it** (pinned `src/iop/lut3d.c`): the LUT folder is the preference `plugins/darkroom/lut3d/def_path`; the parameter `filepath` holds the path **relative to that folder** with `/` separators, so an XMP made on one device works on another whose LUT folder has the same file at the same relative path. Formats `.cube`, `.3dl`, `.png` (Hald CLUT), and `.gmz` only in a darktable built with G'MIC. A missing or unreadable file renders the module as a pass-through (the picture is unchanged) and shows "error - invalid cube file: <full path>" (`invalid png file`, `invalid 3dl file`); the file list shows `INVALID >> <relative path>`.

**A file** is identified by its **relative path**: UTF-8, `/` separators, at most 500 bytes, no leading `/`, no drive letter, no `\`, no empty, `.` or `..` step, no control characters, extension (any case) `.cube`, `.3dl`, `.png` or `.gmz`; at most 64 MB (`LPL_MAX_LUT_MB`, default 64). Its content is identified by the lowercase hex SHA-256 of its bytes. Paths are compared exactly (case matters, as on the phone and on Linux).

**Server calls** (header `x-lpl-token`; `agent` = the agent id):

| Call | Rule |
|---|---|
| `GET /api/luts?since=<seq>` | `{"seq": n, "files": [{path, size, sha256, seq, changed_at, device_id, device_name, deleted}]}`: the current entry of every path whose `seq` > `since` (removed ones too, `deleted: true`, keeping the last size and hash). `seq` is one counter over all paths; `since=0` lists everything. |
| `GET /api/luts/file?path=<p>` | The bytes of the current file; answers HTTP `Range` (as `/original`); headers `x-sha256`, `content-length`. 404 for an unknown or removed path. |
| `PUT /api/luts/file?path=<p>&agent=<id>` | Body = the bytes; header `x-sha256` (required; the server recomputes it, mismatch -> `400 {"error": "hash_mismatch"}`, nothing stored); optional `x-base-sha256` = the hash the device last had for this path, `""` when it had none. Rules: the path's current file has the same hash -> nothing changes; no current file (or a removed one) -> stored; otherwise, **without** `x-base-sha256` or with `x-base-sha256` equal to the current hash -> stored (newest upload wins); with a different `x-base-sha256` -> `409 {"error": "changed_elsewhere", "file": {…entry}}`, nothing stored. Stored = new current entry (`seq` + 1, `changed_at` = now, the device's id and name); the previous content stays on the server as history, never deleted by a PUT. Answer 200 `{"path", "sha256", "seq", "stored": true \| false}`. Errors: 400 bad path / extension / empty body / PNG without the PNG signature, 413 over the limit, 401. |
| `DELETE /api/luts/file?path=<p>` | **Web UI only** (devices never call it): removes the path from LPL (`deleted`, `seq` + 1); the content stays as history; no device deletes its copy. A later PUT of the path brings it back. |
| `PUT /api/luts/state?agent=<id>` | The device's whole list each time (replace): `{"folder": "<shown to the user>" \| null, "reason": "<why there is none>" \| null, "files": {"<path>": {"sha256": s, "state": "ok" \| "different" \| "removed" \| "conflict" \| "unusable"}}}`. Sent after every sync pass. `folder` null + `reason` = the device offers nothing (a PC whose darktablerc has no LUT folder). The phone sends `folder` = `"on this phone"`, never a file system path. |
| `GET /api/luts/overview` | Web UI: per path its entry, `history` (count of earlier contents) and `devices: [{agent_id, name, os, sha256, state}]`; per device with `luts` in its caps: name, os, folder, reason, file count, last report; the bytes stored (current and history). |

**What a device keeps** per path: `base` = the hash it last uploaded or took in (none for a file it never synced), and `removed` for a path the user removed on that device.
- *Local change* = the present file's hash differs from `base` (or a file without `base`).
- *Server change* = the server's current hash differs from `base`.

**PC agent** (`agent/lpl_agent.py`): the LUT folder is `plugins/darkroom/lut3d/def_path` in darktable's `darktablerc` (the same config folder the agent already uses for R11's `data.db`); absent or empty -> the agent offers nothing and its state says "darktable has no LUT folder set". On start and every 10 minutes it walks the folder (supported extensions only; links not followed; files over the limit skipped and reported):
- a local change -> `PUT` **without** `x-base-sha256` when the file had a `base` (the PC changed it: newest wins), **with** `x-base-sha256: ""` when it had none (a file found on a PC that joins later does not replace one already in LPL: a 409 makes it `different`);
- no local change and a server change -> the file in the user's folder is **not replaced**: state `different`;
- a path the server has (not removed) and the folder does not: taken in **only into the empty place**: downloaded to a temporary name in the same folder, checked by hash, then moved in only if the path is still free (never overwrites); subfolders are created. A path the agent had synced and that is gone from the folder was removed by the user: state `removed`, not taken in again.
- It never deletes or overwrites a file in the LUT folder. `.gmz` files travel between PCs.

**Phone (app):** its LUT folder is `luts/` in the app's private storage, given to the engine as darktable's preference. Files are added with "Add from phone…" (Android's picker, one or several, optionally into a subfolder); a taken name asks "replace" first (that is a local change). On each sync pass ("sync automatically" off -> only when the user asks), after `GET /api/luts?since=` from its last `seq`:
- a local change -> `PUT` with `x-base-sha256` = `base` (`""` for none). 409 -> state `conflict`;
- no local change and a server change (not removed) -> download, replace the local file, `base` = new hash;
- a local change and a server change -> `conflict` (also found without a 409);
- a server path the phone lacks -> downloaded, unless `removed` or `.gmz` (the app's darktable has no G'MIC: `.gmz` is not downloaded and an imported one is listed as not usable);
- a path removed in LPL -> the phone keeps its copy.
Conflict choices, as R11's dialog: **mine** -> `PUT` with `x-base-sha256` = the server's current hash; **theirs** -> download the server's file over the local one; **keep both** -> the local file is renamed `<name> (<this device's name>).<ext>` (a taken name gets ` 2`, ` 3`) and uploaded as a new path, then the server's file is downloaded to the original path. "Remove" in Settings › LUT files deletes the phone's copy only, marks the path `removed` (not downloaded again); "Get from LightPhotoLibrary" in the module clears the mark and downloads it.

**Not shared:** which LUT a photo uses (that travels in its XMP); G'MIC's compressed LUTs inside the parameters.
