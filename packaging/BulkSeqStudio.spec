# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for BulkSeq Studio (onedir). Run from anywhere:
#   pyinstaller packaging/BulkSeqStudio.spec
import os

ROOT = os.path.dirname(SPECPATH)  # repository root (this spec lives in packaging/)


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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="BulkSeq Studio",
)
