# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for BulkSeq Studio (onedir). Run from anywhere:
#   pyinstaller packaging/BulkSeqStudio.spec
import os
import re
import sys

ROOT = os.path.dirname(SPECPATH)  # repository root (this spec lives in packaging/)
IS_MACOS = sys.platform == "darwin"


def collect(directory, exclude_windows_helpers=False):
    items = []
    abs_dir = os.path.join(ROOT, directory)
    for root, _dirs, files in os.walk(abs_dir):
        rel = os.path.relpath(root, ROOT).replace("\\", "/")
        if "__pycache__" in rel or rel.startswith("scripts/logs"):
            continue
        for name in files:
            if name.endswith(".pyc") or name.endswith(".log"):
                continue
            # macOS has no WSL and never drives app/core/setup_installer.py's
            # Windows-WSL flow, so its .ps1/.bat helpers are dead weight (and
            # confusing clutter) inside the .app bundle's datas.
            if exclude_windows_helpers and name.endswith((".ps1", ".bat")):
                continue
            items.append((os.path.join(root, name), rel))
    return items


datas = []
for d in ("app/data", "app/assets", "workflow", "scripts", "examples"):
    datas += collect(d, exclude_windows_helpers=IS_MACOS and d == "scripts")

ICON = os.path.join(
    ROOT, "app", "assets", "icons", "bulkseq.icns" if IS_MACOS else "bulkseq.ico"
)

# UPX-compressed binaries fail codesign's validation on Apple Silicon, so UPX
# must be off for macOS; Windows/Linux keep PyInstaller's existing default
# (upx enabled when a upx binary is discoverable) by passing True explicitly.
# https://pyinstaller.org/en/stable/usage.html (UPX section)
UPX = not IS_MACOS

a = Analysis(
    [os.path.join(ROOT, "app", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["openpyxl"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BulkSeqStudio",
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
    upx=UPX,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="BulkSeq Studio",
    upx=UPX,
)

if IS_MACOS:
    # Real signing (Developer ID, Hardened Runtime, entitlements, notarization)
    # happens afterwards in scripts/build_macos.sh, not here — codesign_identity/
    # entitlements_file are left unset on EXE() above so PyInstaller's own
    # ad-hoc signing pass doesn't get double-signed over by that later step.
    constants_src = open(os.path.join(ROOT, "app", "constants.py"), encoding="utf-8").read()
    APP_VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', constants_src).group(1)

    app = BUNDLE(
        coll,
        name="BulkSeq Studio.app",
        icon=ICON,
        bundle_identifier="com.tunabirgun.bulkseqstudio",
        version=APP_VERSION,
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            # Must match the floor of the Qt actually bundled, not the oldest Qt the
            # project would accept. requirements.txt pins "PySide6>=6.7" — a floor, not a
            # pin — so a build resolves to current PySide6 (6.11.x), whose Qt minimum is
            # macOS 13 (doc.qt.io/qt-6/macos.html). Declaring 11.0 would let Launch
            # Services START the app on macOS 11/12, where dyld then fails on missing
            # symbols: the user gets a crash instead of the clean "requires a newer
            # macOS" dialog this key exists to produce. Raise this in step with the
            # PySide6 floor in requirements.txt.
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            # Chromium (QtWebEngine) does not initialize correctly under App
            # Sandbox (doc.qt.io/qt-6/qtwebengine-platform-notes.html); this key
            # only affects Aqua-appearance opt-out, not sandboxing, but is set
            # explicitly so the app always honours the user's Dark Mode setting
            # rather than defaulting to light.
            "NSRequiresAquaSystemAppearance": False,
        },
    )
