# LightPhotoLibrary agent

Runs on the PC where darktable is installed. It receives "Edit in darktable" from the web app, copies the photo to this PC, opens it in darktable, and sends the XMP back to the NAS.

- **Windows**: right-click `install-windows.ps1` → Run with PowerShell. Needs Python 3 (or build `lpl-agent.exe` first with `build-windows-exe.ps1`).
- **Linux**: `./install-linux.sh` (systemd user service).

Files it uses:

| | Windows | Linux |
|---|---|---|
| settings | `%APPDATA%\LightPhotoLibrary\agent.json` | `~/.config/lightphotolibrary/agent.json` |
| working set, log, bridge folder | `%LOCALAPPDATA%\LightPhotoLibrary\` | `~/.local/share/lightphotolibrary/` |
| darktable bridge script | `%LOCALAPPDATA%\darktable\lua\lpl_bridge.lua` + `require "lpl_bridge"` in `luarc` | `~/.config/darktable/lua/…` (Flatpak: `~/.var/app/org.darktable.Darktable/config/darktable/…`) |

Settings in `agent.json`: `server`, `password`, `name`, `darktable` (path, or `flatpak`), `library` (`dedicated` = small separate darktable library for the working set, or `memory`), `auto_sync_seconds` (0 = only sync when you leave the image or press Sync).

Commands: `--setup`, `--status`, `--install-bridge`.
