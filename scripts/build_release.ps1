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

$version = ((Select-String -Path "app\constants.py" -Pattern 'APP_VERSION\s*=\s*"([^"]+)"').Matches.Groups[1].Value)

Write-Host "[2/2] Building installer with Inno Setup (version $version)..."
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "ISCC.exe (Inno Setup) not found" }
# Pass the version so the installer name tracks APP_VERSION (installer.iss has an
# #ifndef fallback for manual compiles).
& $iscc "/DMyAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
$installerExe = Join-Path $root "installer_output\BulkSeqStudio-Setup-$version.exe"
if (-not (Test-Path $installerExe)) { throw "Installer not produced at $installerExe" }

Write-Host "[3/3] Creating portable (click-and-run) ZIP..."
# Zip the onedir folder so a user can unzip and double-click BulkSeqStudio.exe
# with no install. dist/ is pre-cleaned each build, so the ZIP is always current.
$installerOut = Join-Path $root "installer_output"
if (-not (Test-Path $installerOut)) { New-Item -ItemType Directory -Path $installerOut -Force | Out-Null }
$onedir = Join-Path $root (Join-Path "dist" "BulkSeq Studio")
$portableZip = Join-Path $installerOut "BulkSeqStudio-Portable-$version.zip"
if (Test-Path $portableZip) { Remove-Item $portableZip -Force }
# A freshly-built dist/ stays locked by the AV/search indexer for a while, so let
# it settle, then retry generously.
Start-Sleep -Seconds 8
$zipped = $false
foreach ($attempt in 1..8) {
    try {
        Compress-Archive -Path $onedir -DestinationPath $portableZip -CompressionLevel Optimal -ErrorAction Stop
        $zipped = $true
        break
    } catch {
        if (Test-Path $portableZip) { Remove-Item $portableZip -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 10
    }
}
if (-not $zipped) { throw "Portable ZIP creation failed after retries (a dist/ file stayed locked)." }

Write-Host "[4/4] Refreshing the repo-root launch copy so it is always the latest build..."
# Keep BulkSeqStudio.exe + _internal at the repo root in sync with this build, so
# the click-to-run copy there (e.g. a Desktop shortcut target) is never stale.
# Best-effort: if the root exe is currently running it is locked, so warn instead
# of failing the whole build.
$rootExe = Join-Path $root "BulkSeqStudio.exe"
$rootInternal = Join-Path $root "_internal"
try {
    if (Test-Path $rootExe) { Remove-Item $rootExe -Force -ErrorAction Stop }
    if (Test-Path $rootInternal) { Remove-Item $rootInternal -Recurse -Force -ErrorAction Stop }
    Copy-Item (Join-Path $onedir "BulkSeqStudio.exe") $rootExe -Force -ErrorAction Stop
    Copy-Item (Join-Path $onedir "_internal") $rootInternal -Recurse -Force -ErrorAction Stop
    Write-Host "  Repo-root copy refreshed to this build."
} catch {
    Write-Warning "Could not refresh the repo-root copy (is BulkSeqStudio.exe running? close it and rebuild): $_"
}

Write-Host ""
Write-Host "Done."
Write-Host "  Executable:   dist\BulkSeq Studio\BulkSeqStudio.exe"
Write-Host "  Installer:    installer_output\BulkSeqStudio-Setup-$version.exe"
Write-Host "  Portable ZIP: installer_output\BulkSeqStudio-Portable-$version.zip"
