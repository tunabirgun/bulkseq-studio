from __future__ import annotations

import ast
import ctypes
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import app.ui.open_external as open_external

APP = Path(__file__).resolve().parents[1] / "app"
CHILD = ("import ctypes; b = ctypes.create_unicode_buffer(1024); "
         "ctypes.windll.kernel32.GetDllDirectoryW(1024, b); print(b.value)")


def _child_dll_directory() -> str:
    return subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True,
                          timeout=60, check=True).stdout.strip()


@pytest.mark.skipif(sys.platform != "win32", reason="SetDllDirectoryW inheritance is Windows behaviour")
def test_programs_started_inside_the_guard_do_not_inherit_the_bundle(monkeypatch, tmp_path) -> None:
    # Reproduce what the PyInstaller bootloader does, then start children with and without the guard.
    kernel32 = ctypes.windll.kernel32
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    kernel32.SetDllDirectoryW(str(tmp_path))
    try:
        assert _child_dll_directory() == str(tmp_path)
        with open_external.bundle_dll_directory_cleared():
            assert _child_dll_directory() == ""
        assert _child_dll_directory() == str(tmp_path)
    finally:
        kernel32.SetDllDirectoryW(None)


def test_open_path_clears_the_bundle_only_while_it_opens(monkeypatch, tmp_path) -> None:
    calls: list[object] = []
    state = {"dll_directory": "bundle"}

    class Kernel32:
        @staticmethod
        def SetDllDirectoryW(value):  # noqa: N802 - Win32 name
            calls.append(value)
            state["dll_directory"] = value

    class Windll:
        kernel32 = Kernel32()

    class FakeCtypes:
        windll = Windll()

    opened: list[object] = []
    monkeypatch.setattr(open_external, "ctypes", FakeCtypes)
    monkeypatch.setattr(open_external.sys, "platform", "win32")
    monkeypatch.setattr(open_external.sys, "frozen", True, raising=False)
    monkeypatch.setattr(open_external.sys, "_MEIPASS", "bundle", raising=False)
    monkeypatch.setattr(open_external.QDesktopServices, "openUrl",
                        lambda url: opened.append((url.toLocalFile(), state["dll_directory"])) or True)

    assert open_external.open_path(tmp_path)
    assert opened == [(tmp_path.as_posix(), None)]
    assert calls == [None, "bundle"]


def test_nothing_outside_the_helper_opens_urls_directly() -> None:
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "open_external.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr == "openUrl":
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    assert offenders == []
