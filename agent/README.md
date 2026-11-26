# LightPhotoLibrary agent

Runs on the PC where darktable is installed. It receives "Edit in darktable" from the web app, copies the photo to this PC, opens it in darktable, and sends the XMP back to the NAS.

- **Windows**: right-click `install-windows.ps1` → Run with PowerShell. Needs Python 3 (or build `lpl-agent.exe` first with `build-windows-exe.ps1`).
- **Linux**: `./install-linux.sh` (systemd user service).

Files it uses:

| | Windows | Linux |
|---|---|---|
| settings | `%APPDATA%\LightPhotoLibrary\agent.json` | `~/.config/lightphotolibrary/agent.json` |
| working set, log, bridge folder | `%LOCALAPPDATA%\LightPhotoLibrary\` | `~/.local/share/lightphotolibrary/` |
| shared styles, presets, layouts (R11): state per item | `%LOCALAPPDATA%\LightPhotoLibrary\shared.json` | `~/.local/share/lightphotolibrary/shared.json` |
| LUT folder (R19): state per file (the hash last sent or taken in, files removed here) | `%LOCALAPPDATA%\LightPhotoLibrary\luts.json` | `~/.local/share/lightphotolibrary/luts.json` |
| darktable's LUT folder: read from `darktablerc` (`plugins/darkroom/lut3d/def_path`); files are sent to LPL and files you do not have are added, never over an existing file | the folder set in darktable's preferences › processing | same |
| darktable's `data.db` (read from a read-only snapshot; written only by "Update <PC>" in LPL's Devices tab, while darktable is closed) | `%LOCALAPPDATA%\darktable\data.db` | `~/.config/darktable/data.db` |
| darktable bridge script | `%LOCALAPPDATA%\darktable\lua\lightphotolibrary\lpl_bridge.lua` + `require "lightphotolibrary/lpl_bridge"` in `luarc` | `~/.config/darktable/lua/lightphotolibrary/…` (Flatpak: `~/.var/app/org.darktable.Darktable/config/darktable/…`) |

Settings in `agent.json`: `server`, `password`, `name`, `darktable` (path, or `flatpak`), `library` (`dedicated` = small separate darktable library for the working set, or `memory`), `auto_sync_seconds` (0 = only sync when you leave the image or press Sync), `darktable_configdir` / `darktable_datadb` (other places for darktable's config folder or its `data.db`).

`darktable/module-names.json` (a copy of darktable-android `schema/module-names.json`) says which presets are presets of processing modules and are shared; without it the agent does not share styles and presets.

**LUT files (R19, `docs/luts-r19.md`)**: when the server lists `luts`, the agent reads darktable's LUT folder from `darktablerc` (darktable › preferences › processing › "LUT 3D root folder"; none set = the agent offers nothing and says so in LPL's Devices tab). At start and every 10 minutes it sends new or changed `.cube`, `.3dl`, `.png` and `.gmz` files (at most 64 MB each) and takes in the files LPL has and this folder lacks. It never overwrites or deletes a file in your LUT folder: a different file at the same path is reported as "different", a file you deleted is not taken back. A file found on a PC that joins later does not replace the one LPL already has; a file you change on a PC does (newest wins).

Commands: `--setup`, `--status`, `--install-bridge`.

If darktable's script manager is active: lighttable → scripts → folder `lightphotolibrary` → start `lpl_bridge` (once).
