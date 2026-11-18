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

## 1. Install the server on the NAS (Docker)

GitHub builds a ready-made image on every push: `ghcr.io/nannolo45/lpl:latest` (Intel/AMD and ARM). Because the repo is private, the NAS needs to log in to GitHub's registry once:

1. On GitHub: Settings → Developer settings → Personal access tokens → **Tokens (classic)** → Generate, with only the **read:packages** scope. Copy the token.
2. On the NAS, either SSH: `docker login ghcr.io -u nannolo45` and paste the token as password, or on **Synology**: Container Manager → Registry → Settings → Add → URL `https://ghcr.io`, user `nannolo45`, password = the token.
3. Put `docker-compose.yml` in a folder on the NAS, e.g. `/volume1/docker/lightphotolibrary`, and set the photo folder (the left side of `- /volume1/photo:/photos`). Optionally set `LPL_PASSWORD` and `user:` (see the comments in the file).
4. Start it:
   - **Synology**: Container Manager → Project → Create → set the path to that folder → it finds `docker-compose.yml` → Start.
   - **Other NAS / SSH**: `docker compose up -d`
   - To update later: pull the image again (Synology: Project → Action → Build / or `docker compose pull && docker compose up -d`).

(Prefer building on the NAS? Copy the whole repo there, and in `docker-compose.yml` swap `image:` for `build: .`.)
4. Open `http://<nas-address>:8080`. The first scan indexes everything and makes previews; with 20,000 photos expect roughly 30–90 minutes, depending on the NAS. You can browse while it works.

LPL writes **only** `.xmp` sidecar files next to your photos, never the photos themselves. Before it changes an existing XMP for the first time, a backup goes to `data/xmp-backups/`.

## 2. Install the agent on your PC

### Windows
1. Download **lpl-agent-windows.zip** from the repo's **Releases** page ("Windows agent (latest build)"). No Python needed.
   (Alternative: install Python 3 and use the `agent` folder of the repo directly.)
2. Unzip it, right-click `install-windows.ps1` → **Run with PowerShell**.
   It asks for the NAS address (e.g. `http://192.168.1.20:8080`), finds darktable, installs the darktable bridge script and starts the agent now and at every login.
3. Restart darktable once.

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

## Troubleshooting
- Agent log: `%LOCALAPPDATA%\LightPhotoLibrary\agent.log` (Windows), `~/.local/share/lightphotolibrary/agent.log` (Linux). `python lpl_agent.py --status` shows the setup.
- "bridge script not seen": the agent can still start darktable, but can't send photos to an already-open darktable on Windows. Run `lpl_agent.py --install-bridge` and restart darktable. darktable's own log shows lines starting with `[lpl]`.
- Server log: `docker logs lightphotolibrary`.

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
