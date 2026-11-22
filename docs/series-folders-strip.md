# Series folders, Strip mode, and the board link (branch `series-strip`)

Screenshots: `docs/shots/series-strip/` (main-strip, board-linked, board-unlinked, strip-phone, nested-board, nested-strip).

## Data model
- `series` gets `folder_id` (NULL = top level), `pos` (place among its neighbours), `color` (lane / frame colour),
  `linked` (its own board gives its order; default 1). Series names stay unique across the library.
- `series_folders(id, name, pos, linked, created)`: `linked` is the folder board's switch. (Since the branch `nested-folders` also
  `parent_id`, `color`: see "Nested folders" below.)
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

## Leftovers done (branch `series-leftovers`)
- Lanes of a single series: dragging a frame lane by its header (long press on the phone) changes the reading order.
  `POST /api/series/{id}/strip/lanes {lane, before}` (linked series only; an unlinked series has one lane). The frame
  moves on the board to where the frame it goes in front of lies, with its photos, notes and free arrow ends; that
  frame and everything after it (and what is loose below) is pushed down by the frame's height, and the order is read
  back. "Outside the frames" stays last and cannot be dragged.
- Folders and top-level series share one manual order (`series_folders.pos` and top-level `series.pos` are one
  numbering): drag a folder or a series in the tree (a line shows where it lands), or "Move up / Move down" in the
  "⋯" menu. `POST /api/series/top-order {entries}`, `before_folder` on series move. `db.migrate` renumbers an older
  library once where the two numberings collide (folders first, as before). New folders and series come last.
- "Take the order from the board" is one undo (and redo) step: the server keeps a snapshot before the relink
  (membership, order, frames, places, notes, the link, series made from extra frames) and
  `POST …/link/restore {token}` puts it back, tags included. The snapshots are in memory (30, newest last): after a
  server restart the undo answers 410.
- Re-flow moves notes: when a frame grows, notes and free arrow ends inside a pushed frame, or loose below the old
  bottom of the frame over its width, move down by the same amount; those inside the growing frame stay.

## Known limits
- Series names and folder names are each unique library-wide, at any depth (see "Nested folders" for the rest).
- Undo on the board covers strip edits, "Arrange the board from the strip", "Take the order from the board", and
  deleting a series' frame (it comes back as a new series).
- Deleting a folder drops its shared board (layout, notes); series, order and photos stay.

## Nested folders (branch `nested-folders`)
Folders can contain folders, to any depth. On the board a subfolder is **a frame that contains frames**.

### Model
- `series_folders.parent_id` (NULL = top level) and `color`; `folder_frames.sub_id` = the subfolder a group frame is.
  `db.migrate` adds the three columns; an older library opens unchanged (every folder top-level, no group frames).
- What lies in a folder (subfolders and series) shares one order, `pos`, as the top level always did.
- **One board per top-level folder** (`folder_frames` / `folder_items` / `folder_shapes` keyed by its id), and one
  `linked` switch, the top-level folder's. A subfolder has no board: every `/api/series-folders/{id}/…` call on a
  subfolder works on the board of its top-level folder (`root`); `focus` in the board answer is its group frame.
- Inside a top-level folder (its whole tree) a photo is in one series.
- Tags: `series|<folder>|<subfolder>|…|<name>`. Renames use the tag rename on the folder's prefix; moves, dissolving
  and board saves rename the tag of every series whose path changed (`retag_changed`). `WRITE_SERIES_TAGS=0`: none.
- A folder and a series lying next to each other can not share a name (their tags would collide).

### Rules while linked (all in `server/lpl/series.py`)
- The tree is read from the board on a board save and on "Take the order from the board" (`sync_structure`): a series'
  frame or a group frame belongs to the innermost group frame its centre lies in (and that is larger), else to the
  top-level folder. What changes its folder comes last there. Group frames own no photos: a photo outside every
  series' frame keeps its series.
- A new frame is a new series in the folder of the group frame around it; a new frame sent with `"group": true` ("New
  subfolder" tool), or one that holds frames, is a new subfolder. A group frame's name and colour are the subfolder's.
  Leaving a group frame out of a save never removes the subfolder; the web app asks, then dissolves it
  (`DELETE /api/series-folders/{id}`): what was in it moves up one level in its place, only the group frame goes.
- Strip and tree moves, adds: `Board.lay` grows a series' frame, the group frames around it grow by as much, and what
  lies below any of them (frames, a group frame with what is in it, photos, notes) is pushed down. Strip moves never
  change the tree. A series or subfolder moved in the tree / strip goes to the end of its new folder's group frame
  (`Board.put_into`; the group frame grows, and is widened if needed: nothing is pushed sideways).
- A folder that changes its top-level folder takes its part of the board along (`transplant`): into another folder as
  a group frame below everything (a top-level folder's notes come along); out to the top level as a board of its own
  (notes stay behind). Deleting a top-level folder turns its subfolders into top-level folders the same way.
- "Arrange the board from the strip": frames that are no series and no subfolder are removed; every subfolder gets its
  group frame; if frames do not lie as the tree says (or overlap), the board is stacked again from the tree
  (`rebuild_layout`). "Take the order from the board": folders are read from group frames, a frame that holds frames
  becomes a subfolder. The undo snapshot of the latter also holds the tree (parents, places, names, new subfolders).
- Not linked: group frames are only drawn (dashed); the tree changes only from the strip and the tree.

### API (fields and endpoints added; nothing removed)
- `GET /api/series` → `folders[]` also `parent_id, color, depth, root_id, total_count, total_series` (parents first);
  `linked` of a subfolder is its top-level folder's.
- `POST /api/series-folders {name, parent_id}`; `PATCH …/{id} {name | color}`;
  `POST /api/series-folders/{id}/move {parent_id, before, before_folder}` (400 into itself or a subfolder of its own);
  `POST /api/series/{id}/move` takes any folder and `before_folder`; `POST /api/series/top-order` and
  `/api/series/order` take `folder_id` for the order inside a folder.
- Board answer: `root`, `path`, `focus`, `groups[]` (`id, name, color, parent_id, depth, series_count, count`), frames
  with `sub_id`, lanes with `folder_id, path`. Strip answer: `root`, `path`, `groups[]`, `tree[]` (`{folder|series,
  depth}` top to bottom), lanes with `folder_id, depth`; asked for a subfolder it has only that subfolder's lanes.
- Link state: `new_folders`, `refiled`; link answer: `created_folders`, `warnings`.
- `series_folder` photo filter: the folder's series and its subfolders', in tree order. `GET /api/photos/{id}`:
  `series[].path` (folder names, top down). `GET /api/series/{id}/board`: `path`.
- Send to NAS: a series entry's `folder` is its **top-level** folder; new `folder_path` (names down to its own folder)
  and `folder_meta` (place, colour); `series_folders[].groups` holds the group frames (`sub` = folder name), apart
  from `frames`. A NAS with one level of folders gets the series flattened into the top-level folder with their
  frames; one without folders gets them at the top level, as before. Empty subfolders are not sent.

### Web
Tree (`SeriesTree.tsx`, also the phone sheet): any depth, fold state per folder, "New folder in …", drag a folder or a
series onto a folder (into it), onto a row's edge (in front of / after it), to the bottom (top level); "Move to
folder…" lists the tree. Board: group frames lie under the frames in them, "New subfolder" tool, a subfolder opened
alone shows the same board at its group frame, the header has the way back. Strip: group headings (fold, drag by the
handle, "⋯"), lanes indented per depth; a lane or heading dropped on the middle of a heading goes into that folder.

### Limits
- Folder names are unique library-wide (not only among siblings), as series names are.
- While unlinked, "New subfolder" is not offered; a frame drawn around frames becomes a subfolder when linking again
  with "Take the order from the board".
- A subfolder moved out to the top level leaves the notes of its area on the old board.
- Group frames widen but never push anything sideways; a frame that already overlapped another before a re-flow is
  moved under everything, as before (it may leave its group frame; the tree does not change until the next board save).
- Subfolder order is not carried by Send to NAS beyond `folder_meta.pos` of folders the NAS does not have yet.
- The phone has the tree sheet and stacked lanes with group headings; no gestures of its own for nesting.
