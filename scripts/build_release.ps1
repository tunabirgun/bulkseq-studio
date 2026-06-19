# Build the BulkSeq Studio Windows executable (PyInstaller) and installer (Inno Setup).
# Prerequisites: a populated .venv (pip install -r requirements.txt -r requirements-build.txt)
# and Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Note: PyInstaller/ISCC write progress to stderr; do NOT use -ErrorActionPreference Stop
# here (PowerShell 5.1 would abort on that benign stderr). Success is checked via $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found at $py" }

Write-Host "[1/2] Building executable with PyInstaller..."
# Pre-clean build/ and dist/ ourselves (PyInstaller --clean can hit locked
# localpycs dirs from an interrupted run); retry once to dodge transient locks.
foreach ($d in @("build", "dist")) {
    if (Test-Path $d) {
        try { Remove-Item $d -Recurse -Force -ErrorAction Stop }
        catch { Start-Sleep -Seconds 2; Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
& $py -m PyInstaller packaging\BulkSeqStudio.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Write-Host "[2/2] Building installer with Inno Setup..."
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "ISCC.exe (Inno Setup) not found" }
& $iscc packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }

$version = ((Select-String -Path "app\constants.py" -Pattern 'APP_VERSION\s*=\s*"([^"]+)"').Matches.Groups[1].Value)
Write-Host ""
Write-Host "Done."
Write-Host "  Executable: dist\BulkSeq Studio\BulkSeqStudio.exe"
Write-Host "  Installer:  installer_output\BulkSeqStudio-Setup-$version.exe"
