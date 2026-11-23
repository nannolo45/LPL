# Duplicates and versions (request R17, branch `versions-r17`)

Design: `docs/versions-r17-design.md`, mockup `docs/shots/versions-r17/mockup.html`. Screenshots (headless Chrome, desktop and
phone): `docs/shots/versions-r17/`. Owner's decisions of 23 Nov: one tile per photo with a badge; cover = the original unless
another is chosen; versions may be deleted (confirmation, file moved to the backup folder, never version 0).

## What was built
| Item | State |
|---|---|
| Scanner: `NAME_nn.EXT.xmp` beside a photo is a version of it, never a photo; name (`darktable:version_name`), rating, labels, tags per version; two digits, three from 100; gaps; v0 sidecar missing is fine; `D_01.jpg` that is itself a photo keeps its own sidecar; numbers never reused (highest ever + 1, `photo_version_hw`) | done (`server/lpl/versions.py`, `scanner.py`) |
| Database: `photo_versions(photo_id, n, name, xmp_path, mtime, size, cover, rating, labels, tags, edited, meta_mtime, preview_ver)` for photos that have versions (v0 has a row then); `version_checkouts`, `edit_baseline_v`, `edit_event_version`, `series_items.version`, `folder_items.version`; `db.migrate` adds them, nothing rewritten; `photos.*` keeps showing the cover, so filters, tiles, series work unchanged | done |
| API: `/checkout?agent=` lists `versions`, `cover`, `next` only when that agent's heartbeat `caps` has `"versions"`; `/xmp` (GET, POST), `/preview` (GET, POST), `DELETE /xmp` take `version` (absent = 0); `409 {"error":"version_exists","next":n}`; `POST /cover?version=n`; `GET /api/photos` carries `versions` and `cover`; `GET /photos/{id}/versions`; `PUT /photos/{id}/version-name`; `/api/meta` takes `version` (absent = the cover's) | done |
| Edit events (R15) carry `version` (saves of different versions are separate entries); working-set items keep `version` (cleaned); the R6 line says "on Phone: version 2, edited · not synced yet" | done |
| Web, desktop and phone: count badge on tiles (grid, phone grid, strip, board), versions strip (thumbnail, number, name, rating, labels, cover mark), set as cover, delete with confirmation, name, "edit this version" in darktable and on this phone, rating/labels/tags apply to the viewed version | done |
| Series and board entries pin a version: `PUT /api/series/{id}/pin`, `PUT /api/series-folders/{id}/pin`; entries carry `pin` and `show`; a deleted pinned version falls back to the cover; "Use in series" in the strip | done (API and button; the board thumbnail shows `show`) |
| R1 link `&version=n`; the "edit" job payload carries `version` | done (link, job payload); the phone app reads it later |
| PC agent | not changed, see below |

## Notes for the reviewer
- **PC agent: the design's premise was wrong.** `transfer.py` only *deletes* `_nn` files (after a send to the NAS); neither it nor `agent/lpl_agent.py`
  copies them. The agent downloads `GET /xmp` (version 0 only) and posts without `version`, which is exactly the old behaviour,
  so nothing breaks, but a PC never receives versions and a duplicate made in darktable on a PC stays there. Giving the agent the
  `versions` cap (download every version, post with `version=`, handle 409) is a separate small package; not built, as the brief said no agent change.
- `/checkout` needs `?agent=<id>` to know the caps (an app that never sends it gets the old answer). The 409 rule uses what that
  agent was told at its last checkout (`version_checkouts`); a post to an existing number from an agent without a recorded checkout is a 409.
- Cover change: `photos.rating/labels/tags/edited` are copied from the cover version and `preview_ver` goes up. A photo's tile
  preview is the cover's per-version preview, falling back to version 0's.
- Rating on a version other than the cover leaves the tile alone. Series tags (`write_series_tags`) are written to the cover version.
- Autotag still looks at version 0's preview.
- If the *last* version of a photo is removed (outside LPL or here), its rows are dropped: it is a plain photo again.

## Tests
- Server: `cd server && pip install -r requirements-dev.txt && python -m pytest -q` : 169 passed (151 before; `tests/test_versions.py` adds 18: scan with gaps, missing v0, 100, own-photo sidecar, ignored orphan; checkout with and without the cap; xmp/preview/delete with version; 409; per-version merge; cover; series pin; edit events; migration of an older library; working-set version).
- Web: `cd web && npm ci && npm test && npm run build` : 71 passed (10 new in `tests/versions.test.ts`: badge, strip, cover url, pin, delete text, R1 link, where line); build passes.
- Headless Chrome: `python web/tests/versions-check.py <python> <folder>` (after `npm run build`): badge, strip, rating goes to one version's XMP only, cover, delete confirmation, phone page and badge. Output of the committed run: all 8 checks passed.
- x86_64 build workspace numbers, not a phone's.

## Open questions
1. Should the PC agent get the `versions` cap now (see above)?
2. "Every version its own tile" stays a later setting, as decided.
3. `/checkout` identifies the agent by `?agent=`: the app must send it (the app brief should say so).
