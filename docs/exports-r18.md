# Receiving exports from the darktable app (request R18, branch `exports-r18`)

The app's export screen gets the destination "LightPhotoLibrary" (mockup `B3-Export.html` in the app repo). The server side is
here; the app side is built against this page. Screenshots (headless Chrome): `docs/shots/exports-r18/`.

## What was done
| Item | State |
|---|---|
| `POST /api/photos/{id}/exports` streamed to disk (JPEG, PNG, TIFF, WebP), answers `{export_id, path}` | done (`server/lpl/api.py`, `exports.py`) |
| Storage: setting "exports folder" (default `<library root>/exports`) / photo's folder path / file name; clash -> ` (2)`, never overwritten | done |
| Table `photo_exports(photo, version, path, name, size, format, created, agent)`; `db.migrate` creates it | done |
| Web (desktop and phone): panel lists exports (name, size, version, device, date), Download, Delete (confirmation, file moved to `xmp-backups/exports/`); filter "has exports"; setting "Exports folder" in Settings > Devices | done |
| Heartbeat step `exporting` (kept as sent; the devices page shows "exporting") | done |
| Capability: app sends `"exports"` in heartbeat `caps`; the heartbeat answer carries `server_caps` | done |
| Resumable / chunked upload | not done: LPL has no such pattern (`/api/import/file` is one streamed request, no resume), so none was invented. A failed upload is repeated whole; the optional `size` check detects a cut-off body |

## Request / response shapes (for the app)
**Upload**
```
POST /api/photos/{id}/exports?agent=<device id>&version=<n>&name=<file name>[&size=<bytes>]
body: the file bytes (Content-Length or chunked; any content-type)
200 {"export_id": 12, "path": "exports/2026/2026-11-18 Lake/IMG_0001.jpg"}
```
- `version` absent = 0 (the original); `name` is a bare file name (folders in it are dropped) whose extension is jpg, jpeg, png, tif, tiff or webp,
  and whose first bytes must match that format. A name that is taken becomes `name (2).ext`, `(3)`, ... (the answer's `path` says what it became).
- `size` optional: if given and the received bytes differ: 400, nothing stored.
- Errors: 400 bad name / format / version / empty / size mismatch, 404 unknown photo, 413 over `LPL_MAX_EXPORT_MB` (default 4096), 401 as the other endpoints.
- `path` is relative to the library root when the exports folder is inside it, else absolute. Same auth as the other agent endpoints.
- Local-library mode (`LPL_MODE=local`) answers 400.

**List / download / delete** (web UI; apps may use them)
```
GET    /api/photos/{id}/exports               -> {"id": 1, "exports": [{"id","version","name","path","size","format","created","agent","device","has_file"}]}
GET    /api/photos/{id}/exports/{eid}/file    -> the file
DELETE /api/photos/{id}/exports/{eid}         -> {"ok": true, "moved_to": "<backup path>", "exports": [...]}
```
`POST /api/photos/query` accepts filter `"exports": "yes"`; photo objects carry `exports` (count).

**Heartbeat**
- Send `"caps": ["exports", ...]`. While exporting a photo, its working-set item may carry `"step": "exporting"` and `"progress": 0..1`.
- Answer: for a device that announces any caps, `"server_caps": ["versions", "exports"]` is added; a device that announces none gets the answer it always got.
  An app should treat a missing `server_caps` as an older server and keep the export waiting.

## Notes
- The exports folder is not scanned as photos (scanner skips it), also when it is set to another folder inside the library. The setting is `exportsFolder` in the `settings` preference (relative = inside the library; a relative path that leaves it falls back to `exports`; an absolute path is allowed).
- Deleting a photo's row does not delete its exports.

## Tests
- Server: `cd server && python -m pytest -q` : 187 passed (178 before; `tests/test_exports.py` adds 9: upload and path, 40 MB streamed and size mismatch, name clash, bad names/formats/versions, all formats, delete to backup, setting, filter and scanner, caps and step).
- Web: `cd web && npm ci && npm test && npm run build` : 74 passed (3 new in `tests/exports.test.ts`); build passes.
- Headless Chrome: `python web/tests/exports-check.py <python> <folder>` (after `npm run build`): list, download link, phone page, delete confirmation; 3 checks passed.
