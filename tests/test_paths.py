from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.core.paths import (
    UnsupportedUncPathError,
    is_unsupported_unc_path,
    is_wsl_unc_path,
    usable_disk_free_bytes,
    windows_to_wsl_path,
    wsl_unc_distro,
    wsl_vhdx_basepath,
)


class TestWindowsPathTranslation:
    # windows_to_wsl_path relies on Path.resolve()/drive parsing, which only behaves
    # correctly for Windows paths on a Windows host.
    pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path translation")

    def test_simple_drive_path(self) -> None:
        assert windows_to_wsl_path(r"C:\a\b") == "/mnt/c/a/b"

    def test_lowercase_drive(self) -> None:
        assert windows_to_wsl_path(r"d:\Data\run") == "/mnt/d/Data/run"

    def test_path_with_spaces(self) -> None:
        assert windows_to_wsl_path(r"C:\Users\Tuna\BulkSeq Studio\p") == "/mnt/c/Users/Tuna/BulkSeq Studio/p"


def test_non_wsl_unc_path_is_refused_not_silently_mangled() -> None:
    # It used to produce '/mnt/\\server\share/...', a path WSL cannot open, with no error.
    for share in (r"\\server\share\data", r"\\10.0.0.5\lab\runs\p", "//server/share/data"):
        assert is_unsupported_unc_path(share), share
        with pytest.raises(UnsupportedUncPathError, match="network share"):
            windows_to_wsl_path(share)
    # Negative control: a WSL-native share still translates, and so does a local drive.
    assert not is_unsupported_unc_path(r"\\wsl.localhost\Ubuntu\home\u\p")
    assert windows_to_wsl_path(r"\\wsl.localhost\Ubuntu\home\u\p") == "/home/u/p"
    assert not is_unsupported_unc_path(r"C:\data")


# --- WSL vhdx free-space correction (cross-platform where the logic is pure) ---

def test_wsl_unc_distro_parsing() -> None:
    assert wsl_unc_distro(r"\\wsl.localhost\Ubuntu-24.04\home\u\p") == "Ubuntu-24.04"
    assert wsl_unc_distro(r"\\wsl$\Debian\home\u") == "Debian"
    assert wsl_unc_distro(r"C:\Users\u\p") is None
    assert wsl_unc_distro("/home/u/p") is None


def test_usable_disk_free_plain_path_unchanged(tmp_path: Path) -> None:
    # A normal (non-WSL) path must return exactly what shutil reports — no regression
    # for the overwhelmingly common local-drive case.
    assert not is_wsl_unc_path(tmp_path)
    assert usable_disk_free_bytes(tmp_path) == shutil.disk_usage(str(tmp_path)).free


def test_wsl_vhdx_basepath_bogus_distro_never_raises() -> None:
    # An unknown distro must degrade gracefully (a fallback drive or None), never raise.
    result = wsl_vhdx_basepath("NoSuchDistro-xyz-123")
    assert result is None or isinstance(result, Path)


@pytest.mark.skipif(os.name != "nt", reason="WSL vhdx lives on Windows")
def test_usable_disk_free_wsl_never_exceeds_backing_drive() -> None:
    from app.core.paths import wsl_recommended_workdir

    workdir = wsl_recommended_workdir()
    if not workdir or not is_wsl_unc_path(workdir):
        pytest.skip("no WSL-native workdir available")
    base = wsl_vhdx_basepath(wsl_unc_distro(workdir))
    if base is None:
        pytest.skip("could not resolve the vhdx backing drive")
    corrected = usable_disk_free_bytes(workdir)
    host_free = shutil.disk_usage(str(base)).free
    # The corrected free never exceeds the physical drive that backs the vhdx —
    # this is exactly the 91G-shown-as-991G bug guard.
    assert corrected <= host_free
