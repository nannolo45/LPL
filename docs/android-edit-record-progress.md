# Who edited last, and sync progress (R13 to R16)

Branch `android-edit-record-progress`. Report: not written yet (work in progress, see the log below).

## Log
- 2026-11-19: branch from `main` 0f1139c. Baseline: server 13 tests pass, web tests and build pass.
- 2026-11-19: done: server side of R15 (history read from the XMP, `edit_events` and `edit_baseline` tables,
  `agent=` on XMP posts, `GET /api/photos/{id}/edits`, `last_edit` in the photo's detail, scanner records
  "outside" edits), R16 server part (step/progress cleaned, dropped for offline devices), R13 (`JOB_RETRY_SECONDS`),
  R14 (`GET /api/auth/token`). Test XMPs from darktable's integration tests in `server/tests/data/xmp`.
  Found: darktable's `num` has gaps and the `mask_manager` pseudo-module is a history item (always off).
  Next: tests for R13, R14, R16 and the migration; PC agent; web UI.
- 2026-11-19: done: PC agent sends `agent=<id>` with XMP posts and `step`/`progress` (downloading with progress,
  sending preview, sending edit); test runs the real agent code against the server over HTTP. Migration test
  from main's schema with 20 000 photos (0.01 s). Server: 48 tests pass. Next: web UI (last-edit line, list of
  edits, progress bar), APK name in the help page, screenshots, report.
