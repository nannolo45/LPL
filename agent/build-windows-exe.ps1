# Optional: build the standalone agent yourself (GitHub Actions does this automatically on every push).
# Needs Python 3 and Node.js on this PC. Output in dist\:
#   lpl-agent.exe   with a console window (setup, status)
#   lpl-agentw.exe  without a window (runs in the background)
# Both include this PC's local library (server + web app). Put exiftool in agent\exiftool\exiftool.exe
# (from https://exiftool.org, Windows 64-bit zip, rename "exiftool(-k).exe") before running install-windows.ps1.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $here "..\web")
npm ci --no-audit --no-fund
npx vite build
Set-Location $here
py -m pip install --upgrade pyinstaller
py -m pip install -r ..\server\requirements.txt
$common = @("--onefile", "--icon", "lpl.ico", "--paths", "..\server",
            "--collect-submodules", "lpl", "--collect-submodules", "uvicorn",
            "--collect-binaries", "rawpy", "--add-data", "..\web\dist;web_dist",
            "--exclude-module", "rawpy.enhance", "--exclude-module", "scipy", "--exclude-module", "skimage",
            "--exclude-module", "cv2", "--exclude-module", "imageio", "--exclude-module", "matplotlib", "--exclude-module", "tkinter")
py -m PyInstaller @common --console   --name lpl-agent  lpl_agent.py
py -m PyInstaller @common --noconsole --name lpl-agentw lpl_agent.py
Write-Host "Built dist\lpl-agent.exe and dist\lpl-agentw.exe. Now run install-windows.ps1." -ForegroundColor Green
