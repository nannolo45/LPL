# Auto-tagging (B&W) and the Strip's photo panel

Screenshots: `docs/shots/autotag-strip/` (settings, preview-run, photo-panel, strip-photos-drag, strip-rename, phone-tray).

## Auto-tagging: Settings > Auto-tagging
One rule for now: **Black & white**. Off until switched on; the first switch-on opens a preview run, so nothing is
written before the user has seen the counts. Auto tags are ordinary tags (default `auto|look|bnw`, `auto|look|toned`),
set through the same code as any tag set in LPL: DB first, then the XMP (`api.write_xmps`). LPL stays the only writer,
so darktable and LPL agree and nothing is wiped later (the problem of the standalone `fototag` script).

How a photo is judged (`server/lpl/autotag.py`), each source can be switched off:
1. **darktable's rendered preview** (`photos.preview_src = 'rendered'`, from the bridge or the local outbox). Trusted only
   while the edit it shows is the current one (`rendered_hist` = a hash of the history; cleared when an XMP arrives
   within 10 minutes of the preview, since the bridge sends both in either order).
2. **the edit history**: the latest state of each module instance up to `history_end` (a module switched on and then
   off again is off). B&W = `monochrome` on, or `channelmixerrgb` (color calibration, params version 3) with any of
   `grey[0..2]` not 0 (darktable's `apply_grey`). Params are hex or `gz` + 2 digits + base64(zlib). Other B&W recipes
   (color balance rgb at -100 % chroma, LUTs) can not be read: they need source 1.
3. **the camera preview** for photos with no edit: LPL's own preview, i.e. the JPEG inside the raw (or the image).
   Shows what was shot in B&W in camera. On by default.

Pixels: YCbCr on a 200 px copy, clipped shadows/highlights left out. `neutral` = 95th percentile of the chroma;
`resid` = the same after taking out the mean colour of each of 12 brightness bands (toned pictures, sepia and
split-toning, have one colour per brightness); `tint` = the strongest band colour. Limit = 3 + strictness × 0.07
(Cb/Cr units, 3..10); `neutral <= limit` → B&W; `resid <= limit` and `tint <= 45` → B&W, toned; within the
"unsure band" above the limit → unsure; else colour. Toned counts as "B&W + toned" (default), "B&W only" or colour.

Tables: `autotag_results(photo_id, rule, verdict, toned, source, score, detail, sig, applied, checked)`,
`autotag_overrides(photo_id, rule, verdict, toned, ts)`; settings JSON under `settings.autotag`. New columns
`photos.preview_src, preview_ts, rendered_hist` (`db.migrate` → `autotag.migrate`; nothing rewritten).

Rules for applying: unsure photos are left as they are (review page). A correction (review page, photo panel) is kept
and always wins. A tag the rule put on and the user took off becomes a "colour" correction (never re-added). With
"remove stale" (default on) the rule takes its own tags off photos that no longer match. Renamed tags: the old name is
remembered (`managed`) and moved at the next run. Colour photos get no tag.

Runs: by itself for new and changed photos (scanner: new preview, changed XMP; agent: preview, XMP; local outbox) when
the rule is on and "Run on new and changed photos" is on; a background thread collects them for 3 s.
A preview run (`POST /api/autotag/run {force}`) checks the scope without writing; `POST /api/autotag/apply {enable}`
applies everything.

API: `GET/PUT /api/autotag`, `POST /api/autotag/run {apply, force, wait}`, `GET /api/autotag/status`,
`GET /api/autotag/review?kind=unsure|bnw|toned|removals|adds`, `POST /api/autotag/correct {ids, verdict: bnw|toned|colour|clear, apply}`,
`POST /api/autotag/apply {enable}`. `GET /api/photos/{id}` has `autotag` (null when the rule is off).
Filter `look: [bnw, toned, colour, unsure, unchecked]` (the "Look (auto)" filter; corrections count).

Web: `components/Autotag.tsx` (settings section, preview run, the photo panel's "Look (auto)" box with Mark B&W /
Mark toned / Not B&W / Let the rule decide), auto tags marked "auto" in the tag list, the "Look (auto)" filter.

## Strip: rename in place, the photo panel, the phone's tray
- **Rename in place**: double-click a lane's name (or its pencil, F2 with a photo of the lane selected, or "Rename" in
  its ⋯ menu); Enter keeps, Esc drops. A folder's lanes are series (series rename, tags follow), a single series' lanes
  are frames (`PATCH /api/series/{sid}/frames/{fid} {label}`, the board's frame label), its one lane when unlinked is
  the series. Group headings (subfolders) rename the folder.
- **Photo panel** (desktop, Strip mode): the left column switches between "Series" and "Photos" (remembered). The panel
  browses the whole library: search, "Not in <open series/folder>" (default) / "In no series yet" / "All", a library
  folder, rating at least, B&W. Photos already in the strip are dimmed with their lane's name. Drag photos (the
  selection, Ctrl/Shift) onto a lane at a place, or onto "+ New series" (folders). Server: a single series' strip move
  adds photos that are not in it yet (`api.series_add`, then places them); a series in a folder passes all ids to the
  folder move, which takes them in. New filters `no_series`, `not_in_series_folder`.
- **Tray** (phone): "Tray" in the library's selection bar collects photos (kept on the phone, `localStorage lpl.tray`).
  In a Strip the tray sits at the bottom: tap photos to pick some (none picked = all), "+ N here" on a lane puts them at
  its end; in a folder also "New series from …" and "One series per day" (capture day; a taken name gets " (2)").
