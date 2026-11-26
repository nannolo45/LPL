# Previews that show the darktable edit (26 Nov 2026)

The NAS has no darktable, so a photo's preview is the camera's JPEG (or the image itself) until a PC sends darktable's
rendering. Three ways now:

1. **The bridge** (as before): leaving a photo in darkroom exports a 1920 px JPEG; the agent posts it with the XMP.
2. **The agent itself**: after any other sync of an edit (auto sync, "Save edited to NAS", darktable closed while in
   darkroom) that came with no rendering, it renders one with `darktable-cli` in a background thread and posts it.
   It remembers the XMP time it rendered (`preview_mtime`) so the same edit is not rendered twice.
3. **A "render" job** for photos already on the NAS: `POST /api/previews/render {ids | stale: true, agent_id?}`. The
   PC downloads each original and XMP to a temporary folder (or uses its working-set copy), renders, posts, deletes.
   Shown as "Update preview from the edit" in the inspector (selected edited photos) and, on the Devices page, as
   "N edited photos still show the camera's preview · Update them on <PC>" (`GET /api/previews/stale`).

Stale = `edited=1` and the preview is not darktable's, or is older than the XMP by more than a minute.

`darktable-cli` is found beside the configured darktable, on PATH, or in the flatpak
(`flatpak run --command=darktable-cli org.darktable.Darktable`). It runs with `--core --configdir <copy>
--library :memory:`: a temporary copy of `darktablerc` (LUT folder, OpenCL) and `data.db` (styles, presets), so it
never waits for or locks an open darktable. A PC announces the cap `render` only when it has darktable-cli.
