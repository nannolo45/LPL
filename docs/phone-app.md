# LPL as an app on the phone (R9) and the desktop's tabs on the phone (R10)

Branch `phone-app`, on top of `android-handoff` (see `docs/android-handoff.md`). Works the same in Chrome and
Firefox for Android and in the LPL shell (a web view that adds ` LPLShell/<version>` to the user agent).

## R9: Back, install, standalone

### Back button and back gesture (`web/src/back.ts`)
Phone layout only. Each thing that is open is a "layer" with its own history entry
(`history.pushState({lplDepth: n})`); `popstate` closes the layers above the entry arrived at, top first.
Closing a layer in the app (Done, ×, ‹, a tab in the bottom bar) takes its entry off again with
`history.go(-n)` (several closed at once: one step). So the first history entry always has nothing open,
and Back there leaves LPL (in a browser) or is the shell's to handle.

| Layer | Back does |
|---|---|
| Sort & filter, Rate & tag, Tabs (sheets) | closes the sheet |
| Settings, the device picker of R3 | closes it |
| Photo page | back to the list |
| A series' board | back to the Series list |
| Library opened from a folder (Folders tab) or from "Photos in order" (Series tab) | back to Folders / Series |
| Inside a folder in the Folders tab | one folder level up |
| Full-screen photo | closes it (existing code, its own `{lplFull}` entry, unchanged) |

- Only `pushState`, `go`, `back` and `popstate`: no `hashchange`, no Navigation API.
- Reload: what was open is forgotten (as before), and LPL goes back to the first entry
  (`history.go(-depth)` at start-up), so Back never steps through entries that open nothing.
- Deep links (R2): the parameter is removed with `replaceState` before anything is pushed;
  `/?photo=<id>` opens the photo page as a layer, so Back goes to the library.
- Desktop: no change; the full-screen photo already closed with Back. Settings and the device picker add
  no history entries on the desktop.
- Pure parts tested: `depthOf`, `layersToClose` (`web/tests/phone.test.ts`).

### Install (`web/src/install.ts`, Settings in `components/Shortcuts.tsx`)
One line at the top of Settings (the ⚙ in the phone header on every tab but Library; the gear on the desktop):

| Situation | Shown |
|---|---|
| inside the LPL shell, already running as an installed app (`display-mode: standalone`), or installed in this session (`appinstalled`) | nothing |
| the browser sent `beforeinstallprompt` (Chrome over https) | "Install app" button (the browser's own prompt) |
| secure context but no prompt event (Firefox; Chrome before its engagement rules are met) | "To install it as an app, open the browser's menu and choose “Install” or “Add app to Home screen”." |
| not a secure context (plain http, not localhost) | "Installing it as an app needs LightPhotoLibrary to be opened over https (see “Install as an app” in the README)." |

Feature checks only (`isSecureContext`, the event, `matchMedia`); the user agent is only read for the shell.
The `beforeinstallprompt` listener is registered at start-up and calls `preventDefault()`, so Chrome's own
mini-infobar is replaced by the button; Chrome's menu still offers installing.
README: new section "Install as an app" (Tailscale Serve, generic, no addresses).

**No service worker.** Chrome's current install criteria (web.dev "What does it take to be installable?",
updated 2024-09-19) list https, a manifest with name, 192 and 512 px icons, `start_url` and `display`, and
user engagement, but no service worker. Chrome dropped the `fetch` handler requirement for installing from
the menu in Chrome 108 (Android) and 112 (desktop) (Chrome blog "Revisiting Chrome's installability
criteria", 2023); that article still said the automatic prompt needed one, the later criteria page doesn't.
If the button never appears in Chrome over https, that would be the first thing to check.

### Standalone polish
- Manifest: `"id": "/"` and `"scope": "/"` added.
- Safe areas: checked, already there (`.mhead` top, `.mtabs` and `.sheet` bottom use `env(safe-area-inset-*)`). No change.
- Pull-to-refresh: **not changed**. The phone's lists scroll inside `.scroll` (the page itself does not
  scroll); Chrome may chain an overscroll at the top of such a list to a pull-to-refresh. That could not be
  verified here (headless Chrome has no pull-to-refresh), so per the brief nothing was changed. If it
  happens on the phone, the fix is one CSS line: `.scroll { overscroll-behavior-y: contain; }` in the
  phone media query.

## R10: the desktop's tabs on the phone (`components/Mobile.tsx` `TabsSheet`, `web/src/phonetabs.ts`)
- Header: a button with the number of open tabs. It opens a sheet: each tab with the desktop's title
  (`tabTitle`), the open one outlined and marked "open now"; tap switches; ✎ names it (Enter or leaving the
  field saves; same rule as the desktop: keeping the automatic title stores no name); × closes it (not shown
  with one tab); "+ New tab, showing all photos" adds one after the current one, as on the desktop.
- Same state as the desktop (`tabs`, `activeTab`, `switchTab`, `newTab`, `closeTab`, `renameTab` of
  `store.tsx`, kept in `localStorage["lpl.tabs"]`): a tab made on one layout exists on the other (same browser).
- Where the phone shows a tab (`phoneSpot`): grid → Library; folders → Folders; work → Devices;
  board → that series' board (or the Series list if the tab has no series); list, timeline, send → the grid,
  with "List / Timeline / Send to NAS: shown as the grid on the phone" in the tab list. The stored tab keeps
  its view. Before this change, opening the phone layout turned such a tab's view into the grid; it no
  longer does.
- The bottom bar still changes what the current tab shows: Library sets the tab to the grid, Folders to
  folders (as before), Devices now sets it to the Working set view (new, so the tab comes back to Devices).
  The Series list has no desktop view; a tab left on the Series list comes back as what it showed before.
- Scroll position and places: the phone's content is drawn afresh per tab (`key={activeTab}`, like the
  desktop's `.main`), so what the desktop keeps per tab comes back on the phone too: the folder level, each
  board's position, the timeline per filter. The desktop does not keep the grid's scroll position per tab
  (it starts at the top after a switch), and neither does the phone.
- Desktop: unchanged.

## How to try it by hand (phone, 390 x 844 or a real phone)
1. Open a photo, press Back: the list. Open Sort, Back: closed. Folders → a year → a day folder (opens the
   Library) → Back: the year's folders → Back: the top level → Back: leaves LPL (browser) / the shell's own Back.
2. Series → Board → Back: Series. Open a photo, reload, Back: leaves LPL at once.
3. Tabs button → + New tab → the count goes up; ✎ to name it; switch between them; × closes one.
   On a desktop browser (same browser profile) the same tabs are there. Make a Timeline tab on the desktop,
   open the phone layout: the list says "Timeline: shown as the grid on the phone".
4. Settings (⚙ on Folders, Series or Devices): on `http://<LAN address>` the https line; on https Chrome the
   Install app button (after Chrome's engagement rules: a tap and 30 s); in Firefox over https the menu line;
   in the shell nothing.

## What was exercised
Headless Chrome (Google Chrome on the build workspace) at 390 x 844 with Android Chrome, Firefox and shell
user agents and at 1400 x 900: all steps above except a real "Install app" prompt (headless Chrome sent no
`beforeinstallprompt`), real Firefox (its user agent only, the engine was Chrome), the real shell, and
pull-to-refresh. Back was driven with the browser's own history back (`page.go_back()`), which is what
Android's Back does in Chrome; Firefox's and the web view's history behaviour were not run.
