# LightPhotoLibrary agent installer for Windows
# Right-click > "Run with PowerShell" (or: powershell -ExecutionPolicy Bypass -File install-windows.ps1)
#
# What it does:
#   1. copies the agent to %LOCALAPPDATA%\LightPhotoLibrary\agent
#   2. asks for the NAS address and finds darktable (setup)
#   3. installs the darktable bridge script (lpl_bridge.lua + a line in luarc)
#   4. starts the agent now and at every login (shortcut in the Startup folder, no console window)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $env:LOCALAPPDATA "LightPhotoLibrary\agent"

Write-Host "LightPhotoLibrary agent - Windows setup" -ForegroundColor Yellow

# --- the agent: standalone .exe files if present (from GitHub or build-windows-exe.ps1), otherwise Python
#     lpl-agent.exe  = with a console window, used for setup
#     lpl-agentw.exe = no window, runs in the background
$exe = Join-Path $here "dist\lpl-agent.exe"
$exew = Join-Path $here "dist\lpl-agentw.exe"
$usePython = -not ((Test-Path $exe) -and (Test-Path $exew))
$pythonw = $null
if ($usePython) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $py) {
        Write-Host "Python 3 is needed (or build lpl-agent.exe with build-windows-exe.ps1)." -ForegroundColor Red
        Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this again."
        exit 1
    }
    $pyexe = & $py.Source -c "import sys; print(sys.executable)"
    $pythonw = Join-Path (Split-Path $pyexe) "pythonw.exe"
    if (-not (Test-Path $pythonw)) { $pythonw = $pyexe }
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $dest "darktable") | Out-Null
Copy-Item (Join-Path $here "darktable\lpl_bridge.lua") (Join-Path $dest "darktable\lpl_bridge.lua") -Force
if ($usePython) {
    Copy-Item (Join-Path $here "lpl_agent.py") (Join-Path $dest "lpl_agent.py") -Force
}

# stop a running copy before setup (and before overwriting the .exe files)
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "lpl_agent.py|lpl-agentw?\.exe" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
if (-not $usePython) {
    Copy-Item $exe (Join-Path $dest "lpl-agent.exe") -Force
    Copy-Item $exew (Join-Path $dest "lpl-agentw.exe") -Force
}

# --- interactive setup (server address, darktable, bridge script)
if ($usePython) {
    & $pyexe (Join-Path $dest "lpl_agent.py") --setup
} else {
    & (Join-Path $dest "lpl-agent.exe") --setup
}

# --- start at login, hidden
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "LightPhotoLibrary agent.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
if ($usePython) {
    $sc.TargetPath = $pythonw
    $sc.Arguments = '"' + (Join-Path $dest "lpl_agent.py") + '"'
} else {
    $sc.TargetPath = Join-Path $dest "lpl-agentw.exe"
}
$sc.WorkingDirectory = $dest
if ($usePython -and (Test-Path (Join-Path $here "lpl.ico"))) {
    Copy-Item (Join-Path $here "lpl.ico") (Join-Path $dest "lpl.ico") -Force
    $sc.IconLocation = (Join-Path $dest "lpl.ico")
}
$sc.WindowStyle = 7
$sc.Description = "LightPhotoLibrary agent (hands photos to darktable)"
$sc.Save()

if ($sc.Arguments) {
    Start-Process -FilePath $sc.TargetPath -ArgumentList $sc.Arguments -WorkingDirectory $dest -WindowStyle Hidden
} else {
    Start-Process -FilePath $sc.TargetPath -WorkingDirectory $dest -WindowStyle Hidden
}
Write-Host ""
Write-Host "Done. The agent runs now and at every login." -ForegroundColor Green
Write-Host "Log file: $env:LOCALAPPDATA\LightPhotoLibrary\agent.log"
Write-Host "Restart darktable once so it loads the bridge script."
Read-Host "Press Enter to close"
