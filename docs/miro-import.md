# Import from Miro

Series view → "Import from Miro…" (tree, under "+ New folder"; also on the empty Series page). Server: `server/lpl/miro.py`, tests `server/tests/test_miro.py`; dialog `web/src/components/MiroImport.tsx`.

1. Paste the board link and, the first time, a Miro access token (Miro → Settings → Your apps → Create new app, scope `boards:read`, install on the board's team). The token is kept in the `settings` table (`miro.token`); "Forget it" removes it.
2. "Read the board" (`POST /api/miro/preview`) reads items and connectors through Miro's REST API v2 and matches pictures to photos. Nothing is written. The answer is cached for 30 min under a `key`.
3. "Make the series" (`POST /api/miro/import`) creates a new top-level, linked series.

Matching: Miro's REST API returns no title and no geometry for pictures dropped onto a board (a known Miro limit), though Miro's UI shows the file name. So: a name when there is one (API title, or a file name in the original's storage link), matched without extension (RAW preferred, export suffixes like `_01` tried off); otherwise by picture: a 256-bit difference hash of Miro's preview against LPL's thumbnails, LPL's side hashed at 6 centre crops (edits are often cropped), cached in table `photo_phash` per preview version. Pool: the dialog's "folder name contains" / dates, else the folders of name matches, else the whole library (first full run: minutes; later runs reuse the cache). Sure → placed; a bit less sure → placed and listed to check; unsure → not placed, listed with its guess. The dialog shows Miro picture | library photo pairs to take or leave out (`use` in the import call). The preview runs in a thread: `POST /api/miro/preview` starts it, `GET /api/miro/preview/{key}` gives progress, then the report; `GET /api/miro/pic/{key}/{miro id}` serves Miro's preview for the pairs.

Layout: Miro centres (pictures without size: the median distance to the nearest neighbour = one LPL cell) (relative to the canvas or a parent frame's top-left) → LPL top-left corners, scaled so the median picture is as wide as its LPL tile, moved to start at (40, 40). Frames keep nesting (geometry; outer frames first) and get the nearest LPL colour (white = none). Sticky notes, text and shapes with text → notes (min 120×70); connectors → arrows (attached to photos/notes, captions kept). Embeds, documents, empty shapes etc. are left out and counted.

Order: the series is linked, so the order is the board's reading order; photos in a frame inside another frame count under the outer frame (as for any frame drawn inside another).
