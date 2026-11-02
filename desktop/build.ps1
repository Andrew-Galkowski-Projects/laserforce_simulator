# DESK-02: build the Desktop build (one folder) and zip it. CI and local builds both run this.
#   powershell -NoProfile -File desktop/build.ps1 -Python <python.exe> -Out <dir>
# <python.exe> is a python.org CPython (not conda: ADR-0048) with
# laserforce_simulator/requirements-desktop.txt (the runtime file plus
# pywebview, DESK-03) and PyInstaller installed, and
# `npm --prefix frontend ci` already run. Writes
# <Out>/dist/LaserforceSimulator/LaserforceSimulator.exe and
# <Out>/LaserforceSimulator-windows-x64.zip.
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = 'Stop'

# $ErrorActionPreference does not stop on a native command's exit code.
function Invoke-Native([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

$root = Split-Path -Parent $PSScriptRoot
# PyInstaller collects in a subprocess that ignores the spec's pathex (ADR-0048).
$env:PYTHONPATH = Join-Path $root 'laserforce_simulator'
$env:DJANGO_SETTINGS_MODULE = 'laserforce_simulator.settings'
$env:SECRET_KEY = 'build-only'

Invoke-Native 'npm run build' { npm --prefix (Join-Path $root 'frontend') run build }
Invoke-Native 'PyInstaller' {
    & $Python -m PyInstaller (Join-Path $PSScriptRoot 'laserforce.spec') --noconfirm --log-level WARN `
        --distpath (Join-Path $Out 'dist') --workpath (Join-Path $Out 'build')
}

$folder = Join-Path $Out 'dist\LaserforceSimulator'
# The asset name: keep it equal to ZIP in .github/workflows/ci.yml.
$zip = Join-Path $Out 'LaserforceSimulator-windows-x64.zip'
if (Test-Path $zip) { Remove-Item $zip }
# Windows' bsdtar writes a zip (-a picks the format from the name) far faster
# than Compress-Archive on a few thousand files.
Invoke-Native 'zip' { tar.exe -a -c -f $zip -C (Join-Path $Out 'dist') LaserforceSimulator }
'folder MB: {0:N1}' -f ((Get-ChildItem $folder -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
'zip MB:    {0:N1}' -f ((Get-Item $zip).Length / 1MB)
