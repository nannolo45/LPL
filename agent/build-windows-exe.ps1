# Optional: build a standalone lpl-agent.exe (no Python needed on other PCs).
# Needs Python 3 on this PC. Output: dist\lpl-agent.exe
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
py -m pip install --upgrade pyinstaller
py -m PyInstaller --onefile --noconsole --name lpl-agent lpl_agent.py
New-Item -ItemType Directory -Force -Path "dist\darktable" | Out-Null
Copy-Item "darktable\lpl_bridge.lua" "dist\darktable\lpl_bridge.lua" -Force
Write-Host "Built dist\lpl-agent.exe. Now run install-windows.ps1." -ForegroundColor Green
