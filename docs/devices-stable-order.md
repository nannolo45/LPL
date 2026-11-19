# Devices in a stable order, a phone described as a phone

Branch `devices-stable-order`, from `main` 398274a. Report first, log below.

## Report

### Not met or not run
- Nothing knowingly left out. Not run: the real Android app (its heartbeat was played from what its code sends,
  `app/lpl/.../Model.kt` `AgentState` in darktable-android 6682e81), real phones (headless Chrome only).
- "This device first on its own screen" was not done: the web page cannot tell which agent runs on the computer
  it is shown on (the browser and the agent do not know each other), so name order is the stable rule.

### 1. Stable order
- `list_agents()` (`server/lpl/api.py`) now returns the agents by name, case-insensitive, then id, instead of
  `ORDER BY last_seen DESC`. `/api/agents`, `/api/status` and so the Working set page, the phone's Devices tab
  and the device picker all follow it.
- Callers checked:
  - `job_create` (picks a device only when exactly one PC is online) and `_posting_agent` (only when exactly one
    device holds the photo): order plays no part (test `test_routing_still_ignores_order`).
  - Web `defaultAgent` (the picker's suggestion) relied on the old order for "the PC heard from last" among
    several online, or among offline PCs, and for the last fallback. It now sorts by `last_seen` itself, so it
    suggests the same device as before.
  - `App.tsx` top bar and `store.tsx` `editInDarktable` use the first online agent only when there is exactly one.
  - `photoWhere` / `photoActivity` (lines on a photo) now come in the stable order too.

### 2. A phone described as a phone
The darktable phone app sends `darktable_running` (= a photo is open in its darkroom), `bridge_alive` (always
true: it has no bridge), `version`, `message`, and heartbeats only while it is open on the screen.
Its card now shows, from `devicePills()` in `web/src/devices.ts`:

| | PC (unchanged) | Phone |
|---|---|---|
| online | "online", "darktable running/not running", "bridge script active/not seen" (when darktable runs) | "online · app open", "photo open in the darkroom" / "no photo open" |
| offline | "offline · seen …" | "app not open · seen …", and the line "The darktable app reports only while it is open on Phone: what you start here waits until it is opened there." |

Texts: "Nothing checked out on Phone." becomes "No photos on Phone." (PCs unchanged); "Remove saved copies from
Phone" and "Done · remove from Phone" read correctly (the app deletes its copies when it gets the job) and stay;
the remove-device confirmation says "It comes back if it connects again" (was "if its agent connects again").
Device picker: a phone is "app open" / "app not open" (PCs "online" / "offline").

### 3. Progress bar on the Working set page
Checked in headless Chrome with a phone that sends `step` and `progress`: the row shows its status pill
("copying from the NAS") and under it the bar "downloading · 58%" (12 px text, below the pill, not over it).
The bar used to replace the pill in Devices; now both are shown. When the phone stops reporting, the bar goes.

### Tests
```
cd server && python -m pytest -q tests      # 67 passed (64 on main), new tests/test_agent_order.py (3)
cd web && npm ci && npm test && npm run build  # 30 passed (27 on main), build passes
```
- `test_agent_order.py`: three devices heartbeating in turn keep the order Desktop, laptop, Phone in
  `/api/agents` and `/api/status`; equal names by id; routing unaffected. Two of the three fail on main's code.
- `web/tests/devices.test.ts` (3 new): the picker's suggestion by recency whatever the list order; the phone's
  pills (never "bridge" or "darktable running"); a PC's pills as before.
- Browser check `web/tests/devices-check.py` (optional, playwright + Chrome, after `npm run build`): OK.
  Over 12 s of alternating heartbeats the cards were always Desktop, Phone; phone card pills; pill and bar in
  the row; picker order and words; phone layout Devices tab; the phone stopping: "app not open", the hint, no bar.
  The earlier `web/tests/browser-check.py` still passes.

Fresh clone: see the log.

### Screenshots (`docs/shots/devices-stable-order/`)
`desktop-devices.png`, `desktop-device-picker.png`, `phone-devices.png`, `phone-devices-app-closed.png`.

### After a merge
Pull the new image as usual. Nothing else.

## Log
- 2026-11-19: branch from main 398274a; stable order, phone card, picker words, pill and bar together; tests;
  browser checks OK.
