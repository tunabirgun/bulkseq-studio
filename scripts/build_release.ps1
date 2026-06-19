# Build the BulkSeq Studio Windows executable (PyInstaller) and installer (Inno Setup).
# Prerequisites: a populated .venv (pip install -r requirements.txt -r requirements-build.txt)
# and Inno Setup 6 (winget install JRSoftware.InnoSetup).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found at $py" }

Write-Host "[1/2] Building executable with PyInstaller..."
& $py -m PyInstaller packaging\BulkSeqStudio.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host "[2/2] Building installer with Inno Setup..."
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "ISCC.exe (Inno Setup) not found" }
& $iscc packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }

Write-Host ""
Write-Host "Done."
Write-Host "  Executable: dist\BulkSeq Studio\BulkSeqStudio.exe"
Write-Host "  Installer:  installer_output\BulkSeqStudio-Setup-0.1.0.exe"
