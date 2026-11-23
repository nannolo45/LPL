# Devices: photos that could not be sent, full view, device names (23 Nov 2026)

## Photos a device could not copy
- Server (`server/lpl/transfers.py`): every device in `/api/agents` carries `failed`: the photos of its batches in
  state `failed` (newest first, each once), also after the batch ended. Two new item states: `retried`, `cleared`.
- `POST /api/agents/{id}/retry {ids}`: a new edit batch with those photos.
- `POST /api/agents/{id}/clear {ids}`: they leave the page; for photos the device lists as a failed copy it also
  gets a `forget` job (`{"ids": [...], "mode": "failed"}`) to delete what the copy left. A copy with edits is never touched.
- PC agent (`agent/lpl_agent.py`, version 0.2.0): a copy that failed is marked `copy_failed` in the working set,
  is copied again by a new edit job (it used to count as "present"), and `release` deletes it (it used to return
  early for any photo with an error, so such a photo could never leave the list). New job kind `forget`, modes
  `failed` (only a failed copy), `keep` (stop tracking, files stay), `discard` (delete without saving edits).
- Web: red banner per device (Look at them, Retry, Clear from queue), filter chips (All, Couldn't send, Sending,
  Ready to edit, Edited not saved, Saved to NAS, Not sent), one main button plus a ⋯ menu per row, Retry / Clear /
  Full view in the selection bar.

## Full view (`web/src/components/DeviceFullView.tsx`)
Thumbnail, Space or "Full view" opens it on the rows the current chip shows. ← → step, X select (the page's
selection), S save, ⇧S save and remove, O open in darktable, R retry, Del clear / remove / discard (asks first
when edits would be lost), Esc back. The picture is the NAS preview: a device's unsaved edits show only once
saved (darktable sends its preview with the XMP). Phone: swipe, a sheet with the same buttons.

## Device names and one computer under two ids (`server/lpl/identity.py`)
- The bug: `agent/install-linux.sh` ran `systemctl --user enable --now`, which leaves an agent that is already
  running alone; it went on reporting the old name. It now restarts the service. The agent also re-reads its
  name from its config file when that changes.
- Heartbeat: `machine` (hash of /etc/machine-id, Windows MachineGuid, Mac platform UUID; the raw id never
  leaves the computer). A new id on a known computer is merged into it when the old entry is offline
  (Settings › Devices & library › "Merge a reinstalled agent into its old entry", on by default): jobs,
  batches, edit log, version checkouts and fold choices move; the new entry keeps its name.
- `PUT /api/agents/{id}/name {name}` renames in LPL; heartbeat answers carry `rename` until the device reports
  that name. Devices that ignore it (phone app, old agents) keep the LPL name.
- `POST /api/agents/{old}/merge {into}`: merge by hand (refused while `old` is online).
- `/api/agents` adds `same_machine`, `renaming`, `merged_from`.

## For the darktable Android app
Nothing required. Optional: send `machine` in heartbeats, honour `rename` in the answer, and handle `forget` jobs.
