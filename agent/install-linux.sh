#!/usr/bin/env bash
# LightPhotoLibrary agent installer for Linux (systemd user service)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/lightphotolibrary/agent"
mkdir -p "$dest/darktable"
cp "$here/lpl_agent.py" "$dest/"
cp "$here/darktable/lpl_bridge.lua" "$dest/darktable/"
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

python3 "$dest/lpl_agent.py" --setup

if command -v systemctl >/dev/null; then
  unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/lightphotolibrary-agent.service"
  mkdir -p "$(dirname "$unit")"
  cat > "$unit" <<EOF
[Unit]
Description=LightPhotoLibrary agent (hands photos to darktable)
After=graphical-session.target network-online.target

[Service]
ExecStart=/usr/bin/env python3 $dest/lpl_agent.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now lightphotolibrary-agent.service
  echo "Agent running as a user service: systemctl --user status lightphotolibrary-agent"
else
  echo "No systemd found: start it yourself with  python3 $dest/lpl_agent.py &"
fi
echo "Restart darktable once so it loads the bridge script."
