from __future__ import annotations

import os

import pytest

from app.core.paths import windows_to_wsl_path

# windows_to_wsl_path relies on Path.resolve()/drive parsing, which only behaves
# correctly for Windows paths on a Windows host.
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path translation")


def test_simple_drive_path() -> None:
    assert windows_to_wsl_path(r"C:\a\b") == "/mnt/c/a/b"


def test_lowercase_drive() -> None:
    assert windows_to_wsl_path(r"d:\Data\run") == "/mnt/d/Data/run"


def test_path_with_spaces() -> None:
    assert windows_to_wsl_path(r"C:\Users\Tuna\BulkSeq Studio\p") == "/mnt/c/Users/Tuna/BulkSeq Studio/p"
