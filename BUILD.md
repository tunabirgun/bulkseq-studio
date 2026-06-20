# Building the Windows executable and installer

BulkSeq Studio ships as a Windows-native GUI (the bioinformatics tools install
into WSL2 separately, driven by the app's setup screen). Packaging produces a
standalone `.exe` and a per-user installer.

## Prerequisites
- Windows 10/11 x64.
- Python 3.12 (e.g. `winget install Python.Python.3.12`).
- Inno Setup 6: `winget install JRSoftware.InnoSetup`.

## One-time setup
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
```

## Build
```powershell
.\scripts\build_release.ps1
```
This runs PyInstaller (`packaging\BulkSeqStudio.spec`, onedir), then Inno Setup
(`packaging\installer.iss`), then zips the onedir folder. Outputs (version comes
from `APP_VERSION`):
- `dist\BulkSeq Studio\BulkSeqStudio.exe` — the application (a folder bundle).
- `installer_output\BulkSeqStudio-Setup-<version>.exe` — the per-user installer.
- `installer_output\BulkSeqStudio-Portable-<version>.zip` — a portable
  click-and-run build. Unzip anywhere and double-click `BulkSeq Studio\BulkSeqStudio.exe`;
  no installation. Regenerated on every build.

## What gets bundled
The PyInstaller spec bundles `app/data`, `workflow/`, `scripts/`, and `examples/`
alongside the frozen Python app. At runtime `app.core.paths.app_root()` resolves
to the bundle (`sys._MEIPASS`), so the Snakemake workflow templates and the WSL
setup scripts are available and get copied into each project.

## Installer model
The installer is **per-user** (installs to `%LOCALAPPDATA%\Programs\BulkSeq Studio`,
no admin prompt). This keeps the app's directory writable at runtime, which the
WSL environment setup needs for its install log. Enabling WSL itself still prompts
for elevation separately, only when required.

## Verifying a build
```powershell
$env:BULKSEQ_SELFTEST="1"; $env:BULKSEQ_SKIP_READINESS_DIALOG="1"
& "dist\BulkSeq Studio\BulkSeqStudio.exe"   # constructs the UI, then exits 0
```

## Version
Bump `APP_VERSION` in `app/constants.py` and `MyAppVersion` in
`packaging/installer.iss` together. The installer and portable-ZIP filenames are
derived from `APP_VERSION` by the build script.
