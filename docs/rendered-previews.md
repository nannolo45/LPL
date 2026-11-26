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

`darktable-cli` is found beside the configured darktable, on PATH (also the snap's `darktable.darktable-cli`), in the
flatpak (`flatpak run --command=darktable-cli org.darktable.Darktable`), or inside an **AppImage**: the agent makes a
link named `darktable-cli` to the AppImage in its own folder (`bin/`), and the AppImage runs the binary named like the
link (darktable manual, "invocation per OS"). A PC that cannot render says why in its state (`render_unavailable`),
and the 409 of `POST /api/previews/render` lists each online PC's reason. It runs with `--core --configdir <copy>
--library :memory:`: a temporary copy of `darktablerc` (LUT folder, OpenCL) and `data.db` (styles, presets), so it
never waits for or locks an open darktable. A PC announces the cap `render` only when it has darktable-cli.

## Progress, stop and whole series / folders (26 Nov 2026, later)

- **Scopes** for `POST /api/previews/render` (and `POST /api/previews/count`, same body, for the confirmation):
  `ids` (the edited ones among them), `stale`, `series`, `series_folder` (its subfolders, their series and the photos on
  its board), `folder` (a library folder and its subfolders). For series and folders only out-of-date previews unless
  `all: true` (the web UI offers that when every edited photo there is already up to date).
- **Where:** the series view's ⋯ menu and a library folder tile's ⋯ menu ("Update previews from the darktable edits"),
  the inspector for a selection, the Devices page for every out-of-date one.
- **Progress:** the agent reports `state.render` (`job, total, done, failed, current, started, ended, stopped`), waking
  its heartbeat after each photo. The web shows a strip under the top bar (also in full screen and on the phone) with
  the count, the photo in hand and **Stop**; it stays 20 s after the end. Library tiles, board and strip reload as
  previews arrive.
- **Stop:** `POST /api/previews/render/{job}/stop`: a job not handed out yet becomes `stopped`; a running one becomes
  `stopping` and every heartbeat answer of its PC carries `stop_render: [job]` until the agent reports it done; the
  agent stops after the photo in hand.

## A PC that sleeps or restarts mid-job

- **Sleep (lid closed):** the agent is paused with the PC. LPL shows "Laptop stopped reporting while rendering previews
  (n of N)"; on waking the job carries on where it was. A photo whose download or upload the sleep cut off is tried once
  more after 5 s before it counts as failed.
- **Long jobs:** while the agent reports a render job running, the server keeps it freshly taken, so it is never handed
  out a second time beside itself (before, any job taken over 30 minutes ago was handed out again). The agent also
  ignores a render job it already has.
- **Restart (PC or agent):** the agent tells LPL when it started (`state.agent_started`). A render job taken before
  that and not reported running is queued again at once (at most 3 hand-outs in all), without the photos whose
  preview arrived since the job was made. A job being stopped when the agent restarted is simply stopped.

## Speed (26 Nov 2026)

Measured with darktable-cli 4.6 on 24 MP images (CPU only, no OpenCL): one run per photo at the default high quality
2.1 s per photo; with `--hq false` (processed at about the output size, which is all a 1920 px preview needs) 1.2 s;
several photos in one run (`--import` each file, sidecars beside them, output `$(FILE_NAME)` with `--out-ext jpg`)
0.83 s, the same pixels. On a PC whose darktable sets up OpenCL at every start the saving per run is larger.

The render job now: a download thread fetches originals and XMPs up to 8 photos ahead (as `<id>.<ext>` and
`<id>.<ext>.xmp` in a folder per photo) while darktable-cli renders the ones that are there in runs of up to 6 (waiting
half a second for more to arrive). A photo a batch run left out, a working-set copy, or a batch of one is rendered alone.
Each failure is tried once more after 5 s.
