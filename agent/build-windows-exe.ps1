# Optional: build the standalone agent yourself (GitHub Actions does this automatically on every push).
# Needs Python 3 on this PC. Output in dist\:
#   lpl-agent.exe   with a console window (setup, status)
#   lpl-agentw.exe  without a window (runs in the background)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
py -m pip install --upgrade pyinstaller
py -m PyInstaller --onefile --console   --name lpl-agent  lpl_agent.py
py -m PyInstaller --onefile --noconsole --name lpl-agentw lpl_agent.py
Write-Host "Built dist\lpl-agent.exe and dist\lpl-agentw.exe. Now run install-windows.ps1." -ForegroundColor Green
