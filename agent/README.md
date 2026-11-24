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
| darktable's `data.db` (read from a read-only snapshot; written only by "Update <PC>" in LPL's Devices tab, while darktable is closed) | `%LOCALAPPDATA%\darktable\data.db` | `~/.config/darktable/data.db` |
| darktable bridge script | `%LOCALAPPDATA%\darktable\lua\lightphotolibrary\lpl_bridge.lua` + `require "lightphotolibrary/lpl_bridge"` in `luarc` | `~/.config/darktable/lua/lightphotolibrary/…` (Flatpak: `~/.var/app/org.darktable.Darktable/config/darktable/…`) |

Settings in `agent.json`: `server`, `password`, `name`, `darktable` (path, or `flatpak`), `library` (`dedicated` = small separate darktable library for the working set, or `memory`), `auto_sync_seconds` (0 = only sync when you leave the image or press Sync), `darktable_configdir` / `darktable_datadb` (other places for darktable's config folder or its `data.db`).

`darktable/module-names.json` (a copy of darktable-android `schema/module-names.json`) says which presets are presets of processing modules and are shared; without it the agent does not share styles and presets.

Commands: `--setup`, `--status`, `--install-bridge`.

If darktable's script manager is active: lighttable → scripts → folder `lightphotolibrary` → start `lpl_bridge` (once).
