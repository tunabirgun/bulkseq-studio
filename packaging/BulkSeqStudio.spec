# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for BulkSeq Studio (onedir). Run from anywhere:
#   pyinstaller packaging/BulkSeqStudio.spec
import os
import re

# PyInstaller defines SPECPATH as the directory containing this spec, so the
# repository root is its parent (not SPECPATH itself).
ROOT = os.path.dirname(os.path.abspath(SPECPATH))


def collect(directory):
    items = []
    abs_dir = os.path.join(ROOT, directory)
    for root, _dirs, files in os.walk(abs_dir):
        rel = os.path.relpath(root, ROOT).replace("\\", "/")
        if "__pycache__" in rel or rel.startswith("scripts/logs"):
            continue
        for name in files:
            if name.endswith(".pyc") or name.endswith(".log"):
                continue
            items.append((os.path.join(root, name), rel))
    return items


datas = []
for d in ("app/data", "app/assets", "workflow", "scripts", "examples"):
    datas += collect(d)

ICON = os.path.join(ROOT, "app", "assets", "icons", "bulkseq.ico")

# Derive the Windows resource from the same application constant used by every
# package name.  Explorer, Apps & Features, and support tools can therefore
# identify the portable executable without a second hard-coded version.
with open(os.path.join(ROOT, "app", "constants.py"), encoding="utf-8") as handle:
    APP_VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', handle.read()).group(1)
version_numbers = tuple(int(part) for part in APP_VERSION.split("."))
VERSION_TUPLE = (version_numbers + (0, 0, 0, 0))[:4]
VERSION_INFO = None
if os.name == "nt":
    # PyInstaller's Windows version-info helper imports pefile, which is not a
    # Linux build dependency. Keep the Windows-only resource path out of Linux
    # spec evaluation instead of masking the platform split with an extra package.
    from PyInstaller.utils.win32 import versioninfo

    VERSION_INFO = versioninfo.VSVersionInfo(
        ffi=versioninfo.FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE),
        kids=[
            versioninfo.StringFileInfo([
                versioninfo.StringTable("040904B0", [
                    versioninfo.StringStruct("CompanyName", "Tuna Birgun"),
                    versioninfo.StringStruct("FileDescription", "BulkSeq Studio"),
                    versioninfo.StringStruct("FileVersion", APP_VERSION),
                    versioninfo.StringStruct("InternalName", "BulkSeqStudio"),
                    versioninfo.StringStruct("OriginalFilename", "BulkSeqStudio.exe"),
                    versioninfo.StringStruct("ProductName", "BulkSeq Studio"),
                    versioninfo.StringStruct("ProductVersion", APP_VERSION),
                ])
            ]),
            versioninfo.VarFileInfo([versioninfo.VarStruct("Translation", [1033, 1200])]),
        ],
    )

a = Analysis(
    [os.path.join(ROOT, "app", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["openpyxl"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "pytest",
        "_pytest",
        "py",
        "pygments",
    ],
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
    version=VERSION_INFO,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="BulkSeq Studio",
)
