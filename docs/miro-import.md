# Import from Miro

Series view → "Import from Miro…" (tree, under "+ New folder"; also on the empty Series page). Server: `server/lpl/miro.py`, tests `server/tests/test_miro.py`; dialog `web/src/components/MiroImport.tsx`.

1. Paste the board link and, the first time, a Miro access token (Miro → Settings → Your apps → Create new app, scope `boards:read`, install on the board's team). The token is kept in the `settings` table (`miro.token`); "Forget it" removes it.
2. "Read the board" (`POST /api/miro/preview`) reads items and connectors through Miro's REST API v2 and matches pictures to photos. Nothing is written. The answer is cached for 30 min under a `key`.
3. "Make the series" (`POST /api/miro/import`) creates a new top-level, linked series.

Matching: the picture's title without extension against file names without extension (case-insensitive; RAW preferred), also with export suffixes stripped (`_01`, `-1`, ` (2)`, `-edit`…). Several candidates: the one in the folders most of the board comes from, else the closest by difference hash of Miro's preview vs LPL's thumbnail, else a best guess (reported as such). No name match: compared by picture with up to 4000 photos from those folders, taken only when clearly the same. Not found: a grey "Not found: <title>" note in its place (optional).

Layout: Miro centres (relative to the canvas or a parent frame's top-left) → LPL top-left corners, scaled so the median picture is as wide as its LPL tile, moved to start at (40, 40). Frames keep nesting (geometry; outer frames first) and get the nearest LPL colour (white = none). Sticky notes, text and shapes with text → notes (min 120×70); connectors → arrows (attached to photos/notes, captions kept). Embeds, documents, empty shapes etc. are left out and counted.

Order: the series is linked, so the order is the board's reading order; photos in a frame inside another frame count under the outer frame (as for any frame drawn inside another).
