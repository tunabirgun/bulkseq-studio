from __future__ import annotations

import ctypes
import sys
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


@contextmanager
def bundle_dll_directory_cleared():
    """Keep the frozen bundle's DLL directory out of programs the application starts.

    PyInstaller's bootloader points SetDllDirectoryW at the bundle, and child processes
    inherit it. A browser opened on a report then loaded the bundle's VCRUNTIME140.dll and
    held the install folder open, so Setup could not remove it when updating.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or not bundle:
        yield
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        kernel32.SetDllDirectoryW(bundle)


def open_path(path: Path) -> bool:
    """Open a file or folder in its default application."""
    with bundle_dll_directory_cleared():
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
