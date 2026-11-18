<img src="docs/icon.png" width="96" alt="" align="right">

# LightPhotoLibrary 0.1

A fast photo library for a NAS, with a free-form **series board**, darktable-style **sorting and tagging**, phone access, and one-click hand-off to **darktable** for editing.

```
NAS (Docker)                                 your PC                          
┌──────────────────────────────┐            ┌─────────────────────────────────┐
│ LightPhotoLibrary server     │  HTTP      │ LPL agent  ── request.txt ──►   │
│  · index + previews (/data)  │◄──────────►│  (Windows/Linux)   darktable    │
│  · web app (desktop + phone) │            │  working set on SSD  + lpl_bridge.lua
│  · writes XMP next to photos │            └─────────────────────────────────┘
└──────────────────────────────┘
        ▲ browser: PC, phone, tablet (http://nas:8080)
```

- **Library**: grid, list, folders (as tiles, in your own order), timeline. Browsing uses previews stored by LPL, so it never waits for the NAS to read RAW files.
- **Sorting**: any number of levels ("capture time, then lens, then rating…"), every darktable sort plus lens, camera, focal length, aperture, ISO, exposure, tag, edited. Group headings by the first level.
- **Filters**: text, rating, colour labels (any/all), lens, camera, edited, folder, month, tag, series.
- **Tags**: darktable's model: hierarchy with `|`, attach/detach on a selection, create, rename/move, delete, synonyms, category/private, suggestions. Written to the `.xmp` sidecars exactly the way darktable reads them.
- **Series board**: drag photos freely, frames as chapters, a maybe pile, live reading order. Series are also tags (`series|name`).
- **Edit in darktable**: from any device. The PC agent copies the RAW + XMP to the PC's SSD, opens it in darktable, and sends the XMP back to the NAS when you're done. darktable never touches the NAS directly, so it's fast.

---

## 1. Install the server on the NAS (UGREEN, UGOS Pro)

Docker runs on the UGREEN **DXP** models (not on the DH series). GitHub builds a ready-made image on every push: `ghcr.io/nannolo45/lpl:latest`.

**a) One-time: let the NAS download the private image**
1. On GitHub: Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate new token, tick only **read:packages**. Copy the token.
2. On the NAS: Control Panel → **Terminal** → enable SSH. From your PC:
   ```
   ssh <your-nas-user>@<nas-address>
   sudo docker login ghcr.io -u nannolo45      # paste the token as the password
   id                                           # note uid and gid, e.g. uid=1000 gid=10
   ```
   You can turn SSH off again afterwards.
   (UGOS Pro 1.20+ also supports private registries in the Docker app; if you find the registry setting there, adding `ghcr.io` with user `nannolo45` and the token works too.)

**b) Create the project**
1. Install **Docker** from the App Center if it isn't there yet.
2. In **Files**, create the folder `docker/lightphotolibrary` on volume 1, and inside it an empty folder `data` (create it yourself: if Docker creates it, it belongs to root and LPL can't write into it).
3. Docker → **Project** → **Create**. Name: `lightphotolibrary`. Storage path: select `docker/lightphotolibrary`.
4. Paste the content of `docker-compose.yml` into the Compose configuration and edit the three marked lines:
   - `user:` your uid:gid from `id` (so the `.xmp` files belong to you, not to root)
   - `LPL_PASSWORD=` optional password
   - the photo folder, left of `:/photos` (e.g. `/volume1/Photos`; right-click the folder in Files → Properties shows its path)
5. Make sure your user has **read & write** permission on the photo folder (Control Panel → Shared folders). LPL writes `.xmp` files next to the photos.
6. Deploy. Then open `http://<nas-address>:8080`.

The first scan indexes everything and makes previews; with 20,000 photos expect roughly 20–60 minutes on a DXP model. You can browse while it works.

**Updating later**: Docker → Project → `lightphotolibrary` → pull the latest image / rebuild (or over SSH: `sudo docker compose pull && sudo docker compose up -d` in the project folder).

(Other NAS brands work the same way with any Docker Compose UI. Prefer building on the NAS? Copy the whole repo there and replace `image:` with `build: .`.)

LPL writes **only** `.xmp` sidecar files next to your photos, never the photos themselves. Before it changes an existing XMP for the first time, a backup goes to `data/xmp-backups/`.

## 2. Install the agent on your PC

### Windows
1. Download **lpl-agent-windows.zip** from the repo's **Releases** page ("Windows agent (latest build)"). No Python needed.
   (Alternative: install Python 3 and use the `agent` folder of the repo directly.)
2. Unzip it, right-click `install-windows.ps1` → **Run with PowerShell**.
   It asks for the NAS address (e.g. `http://192.168.1.20:8080`), finds darktable, installs the darktable bridge script and starts the agent now and at every login.
3. Restart darktable once. If darktable's **script manager** is active (it is in recent versions): lighttable → **scripts** module → folder **lightphotolibrary** → start **lpl_bridge**. Only needed once.

### Linux
```
cd agent && ./install-linux.sh
```
It installs a systemd user service. darktable from your distribution and the Flatpak version are both supported.

### darktable settings to check
- preferences → storage → **write sidecar file for each image**: "on import" or "after edit" (not "never").
- preferences → storage → **look for updated XMP files on startup**: can stay **off**. LPL hands darktable fresh files.
- preferences → lua options: Lua scripts must be enabled (they are by default).

## 3. Using it

- Select photos (click, Ctrl-click, Shift-click), then rate (0–5, R = reject), label (F1–F5), tag (right panel). Everything is saved to XMP immediately.
- **Edit in darktable**: the button, the E key, or double-click on the board. On the phone: "Edit in darktable" queues it; it opens when the PC agent is online.
- **Working set** tab: what is on your PC right now, sync and release. The agent syncs automatically a few seconds after darktable writes the XMP, and immediately when you leave the image in darkroom.
- **Phone**: open the same address. Add it to the home screen for an app-like window. From outside your home, use Tailscale (or similar) instead of opening a port.

### When the same photo is changed in two places
If rating, labels or tags are changed in LPL (e.g. on the phone) while the photo is open in darktable, the LPL values win and darktable is told to reload them. Otherwise darktable's values are kept. darktable's edit history always comes from darktable.

## 4. Local library on the laptop (travel, PC-only photos)

The Windows agent also runs a library on the PC itself, at **http://localhost:8090** (shortcut "LightPhotoLibrary (this PC)" on the desktop). It works with no NAS connection: same grid, list, folders, timeline, board, tags and filters.

1. Open it and go to **Send to NAS** → **Library folders**: add the folders you dump the card into (e.g. `D:\Trips\Lisbon`). New photos are picked up every couple of minutes, or press **Rescan**.
2. **Edit in darktable** opens the photo **in place** (no copy). Your edits go to the XMP next to the photo, as usual.
3. Back home (or on a fast connection): **Send to NAS** → **Send…** on a folder, or select photos → **Send to the NAS…** in the right panel.
   - Choose the NAS folder to put them in (it suggests where your `YYYY-MM-DD_*` folders usually live, e.g. `2026`) and the descriptor.
   - **One folder per capture day** makes `2026-02-12_Lisbon`, `2026-02-13_Lisbon`, …
   - **Delete the copies on this PC after the NAS confirmed them**: on = the PC is cleared after each file was checked (checksum) on the NAS; off = keep them. The choice is remembered.
   - Ratings, labels, tags, darktable edits (XMP) and series board positions go along.
4. Send again any time: files already on the NAS are skipped and only changed edits (XMP) are uploaded. The table shows "Edits not sent".

Folders you never send simply stay PC-only. Removing a folder from the library doesn't touch the files.

## Troubleshooting
- Agent log: `%LOCALAPPDATA%\LightPhotoLibrary\agent.log` (Windows), `~/.local/share/lightphotolibrary/agent.log` (Linux). `python lpl_agent.py --status` shows the setup.
- "bridge script not seen": the agent can still start darktable, but can't send photos to an already-open darktable on Windows. Run `lpl_agent.py --install-bridge` and restart darktable. darktable's own log shows lines starting with `[lpl]`.
- Server log: `docker logs lightphotolibrary`.
- Local library not opening: `lpl-agent.exe --status`; the Working set tab on the NAS shows "local library not available" with the reason. `lpl-agent.exe --selftest` checks the bundled parts.

## Development
```
# server
cd server && pip install -r requirements.txt
PHOTO_ROOT=/path/to/photos DATA_DIR=/tmp/lpl PORT=8091 python -m lpl.main
# web (proxies /api to :8091)
cd web && npm install && npm run dev
```

## Known limits in 0.1
- darktable duplicates (`IMG_01.RAF.xmp` versions) are not shown as separate photos yet.
- Exporting a series in order, map view and date/numeric range filters are planned.
- The darktable bridge uses darktable's Lua API (`dispatch`, `display_image`, `apply_sidecar`, `write_image`); it was written against the documented API and tested with a mock, so please report the first real run.
