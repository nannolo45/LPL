# Series folders, Strip mode, and the board link (branch `series-strip`)

Screenshots: `docs/shots/series-strip/` (main-strip, board-linked, board-unlinked, strip-phone).

## Data model
- `series` gets `folder_id` (NULL = top level), `pos` (place among its neighbours), `color` (lane / frame colour),
  `linked` (its own board gives its order; default 1). Series names stay unique across the library.
- `series_folders(id, name, pos, linked, created)`: one level of folders; `linked` is the folder board's switch.
- A folder's shared board: `folder_frames` (`series_id` = the series this frame is, NULL = only a visual group),
  `folder_items(folder_id, photo_id, x, y)`, `folder_shapes` (notes, arrows).
- The order is still `series_items.ord`. Existing libraries: `db.migrate` adds the columns and tables; every
  existing series is a top-level, linked series; nothing is rewritten.

## Rules (who wins when): all in `server/lpl/series.py`
- Linked folder board: a photo belongs to the series whose frame it lies in (the smallest frame, if several);
  its place in the frame (rows, left to right) is its place in the series. Outside every frame it keeps its series
  and comes last. A new frame becomes a new series; frame name / colour = series name / colour. Leaving a series'
  frame out of a save never deletes the series (delete the series; the web app asks first).
- Linked top-level series: order = its own board (frames in reading order, rows, then photos outside frames;
  "maybe" frames have no place). The client's `ord` in `PUT /api/series/{id}/board` is ignored.
- Not linked: board saves change neither membership nor order; only the strip does.
- Strip moves on a linked board re-flow the target frame in the Add-photos grid (160 x 115; the frame grows,
  frames below that share some width are pushed down), then the order is read back from the board.
- Inside a folder a photo is in one series: adding it to another series of the folder moves it (`moved` in the
  answer); a series sharing photos with a series of the folder can not be moved in (409 with the reason).
- A series inside a folder never has a linked board of its own (its order is the folder's); moving a series into
  or out of a folder leaves it unlinked. Link it again from its header.
- Hidden photos keep their place in `ord` but have no number (`num`).
- Tags: `series|<name>`, in a folder `series|<folder>|<name>`; renames and moves go through the existing tag
  rename (XMP written atomically, with backup). `WRITE_SERIES_TAGS=0` switches all of it off. A folder and a
  top-level series can not share a name (their tags would collide).
- Rows are found from the photos themselves (centres less than 55 px apart) instead of fixed 110 px bands, so a
  grid row is never split. On hand-arranged boards the order can differ from the old one in edge cases; it is
  recomputed only when a board is next saved.

## API added (existing endpoints and fields unchanged; fields only added)
- `GET /api/series`: each series also has `folder_id, pos, color, linked`; new top-level `folders`.
- `POST /api/series` accepts `folder_id`; `PATCH /api/series/{id}` accepts `color`; `POST /api/series/{id}/items`
  answers `moved` too. `GET /api/series/{id}/board`: also `linked`, `folder`, per item `num`, `series_id`.
- `POST/PATCH/DELETE /api/series-folders[/{id}]`; `POST /api/series/{id}/move {folder_id, before}`;
  `POST /api/series/order {folder_id, ids}`.
- `/api/series-folders/{id}/board` (GET, PUT `{items, frames, remove, restore}`), `/shapes`, `/remove`, `/match`,
  `/add-matching` (target `{series}` | `{new, label}` | `{per_folder}`).
- `GET …/strip` and `POST …/strip/move` for `/api/series/{id}` (`{ids, lane, before}`) and
  `/api/series-folders/{id}` (`{ids, series, before}`).
- `GET …/link` (what linking again would do) and `POST …/link {linked, mode: strip|board|none}` for both.
- Photo filter `series_folder: <id>`; with sort `custom` the folder's series follow each other.
- `GET /api/photos/{id}`: `series[]` entries also have `folder`, `folder_id`, `color`, `num`, `count`.
- Send to NAS: series entries carry `folder, folder_linked, pos, color, linked`; new `series_folders` (frames by
  series name, places by path, shapes). An older NAS ignores them: series arrive at the top level as before
  (their photos then carry both `series|<name>` and, from the XMP, `series|<folder>|<name>`).

## For the Android app / hand-off
Nothing it uses changed: `?series=<id>` deep links, `series` and `title` in the edit link, and the series
endpoints answer as before with extra fields. On a folder the phone's "Edit on this phone" link has no `series`
(only `title` = folder name). To show order, use `num` (or `ord`) from `…/strip`; do not compute it from places.

## Web
View id stays `board` (label "Series"); a tab's `boardSeries` is a series id, or minus a folder id. New files:
`SeriesView.tsx`, `SeriesTree.tsx`, `Strip.tsx`, `Ask.tsx`, `SeriesLine.tsx`, `seriesnav.ts`, `seriesops.tsx`.

## Known limits
- One level of folders; series names unique library-wide.
- Lanes of a single series (its frames) can not be reordered by their header; folders have no manual order.
- Re-flow and frame growth do not move notes or arrows out of the way.
- Undo on the board covers strip edits, "Arrange the board from the strip", and deleting a series' frame (it
  comes back as a new series). "Take the order from the board" is not undoable; the dialog does not claim it.
- Deleting a folder drops its shared board (layout, notes); series, order and photos stay.
