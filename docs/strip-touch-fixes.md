# Strip on a phone: rename, reorder, selection bar (branch `strip-touch-fixes`)

## Causes
- **Rename**: a series folder's heading (the "Animali", "03", "02" rows of a folder with subfolders) could only be renamed by
  double-click; it had no pencil, and a double-tap is not a rename on a phone. Series lanes had a 24 px pencil only. The name
  editor also committed on any blur, so a blur right after opening from the ⋯ menu (menu closing, focus handed back) ended it at once,
  and it relied on the Enter key event, which some phone keyboards do not send.
- **Reorder**: a lane handle used the long-press-on-touch-events path of photos: a 350 ms timer cancelled by 8 px of finger
  movement, plus a stale-closure risk. In emulated Chromium that old path also worked, so the exact failure on the owner's phones
  could not be reproduced; it is hardened, not proven fixed on a real device.
- **Selection bar**: `white-space: nowrap; overflow-x: auto` in one row: the right-hand actions were cut off.

## Changes
- Handles (`.lane-grip`, touch-action none): pointer events with pointer capture; press and drag (4 px) or hold 220 ms lifts the lane.
  The old touch-event path no longer handles handles (photos unchanged). Auto-scroll and the drop line are the existing ones.
- Rename: pencil on folder headings; on a phone a tap on a lane or folder name opens the editor; pencil 44 px; editor is a form
  (Enter / Done submit), 16 px, user-select text, ignores a blur in its first 400 ms. Desktop (double-click, pencil, F2, ⋯) unchanged.
- Selection bar on a phone: two-column grid, count on its own row, buttons 44 px, no overflow at 360 and 412 px.
- `web/src/stripTouch.ts` holds the thresholds and rules (unit tests: `web/tests/striptouch.test.ts`, in `npm test`).

## Tests
- `npm test` (76 pass). `python web/tests/strip-touch-check.py python3 docs/shots/strip-touch` (after `npm run build`): Chromium,
  mobile emulation, touch, 412x915; real touch events via CDP. Long press and press-and-drag on a handle reorder folder headings and
  lanes (checked on the server and after reload), the linked board follows frame reorder and rename, rename by tap/pencil/menu,
  selection bar fits at 412 and 360. Screenshots in `docs/shots/strip-touch/`.
- Firefox: Playwright's Firefox could not be downloaded here (proxy blocks cdn.playwright.dev); the script supports `firefox` but was not run.
- Note: reordering series inside a folder does not move the folder board's frames (existing server behaviour, unchanged).
