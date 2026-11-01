# ANG-09 spike: build the Desktop App (one folder) and zip it.
#   powershell -NoProfile -File desktop/build.ps1 -Venv <desktop venv> -Out <dir>
param(
    [Parameter(Mandatory = $true)][string]$Venv,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $root 'laserforce_simulator'
$env:DJANGO_SETTINGS_MODULE = 'laserforce_simulator.settings'
$env:SECRET_KEY = 'build-only'
npm --prefix (Join-Path $root 'frontend') run build
& (Join-Path $Venv 'Scripts\pyinstaller.exe') (Join-Path $PSScriptRoot 'laserforce.spec') --noconfirm --log-level ERROR --distpath (Join-Path $Out 'dist') --workpath (Join-Path $Out 'build')
$folder = Join-Path $Out 'dist\LaserforceSimulator'
$zip = Join-Path $Out 'LaserforceSimulator.zip'
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $folder -DestinationPath $zip -CompressionLevel Optimal
'folder MB: {0:N1}' -f ((Get-ChildItem $folder -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
'zip MB:    {0:N1}' -f ((Get-Item $zip).Length / 1MB)
