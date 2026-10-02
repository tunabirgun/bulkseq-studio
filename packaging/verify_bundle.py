from __future__ import annotations

import sys
from pathlib import Path


EXPECTED = {"setup_windows_wsl_admin.ps1", "launch_wsl_setup_admin.bat", "setup_wsl_bioenv.sh"}


def verify(onedir: Path) -> None:
    scripts = onedir / "_internal" / "scripts"
    actual = {path.relative_to(scripts).as_posix() for path in scripts.rglob("*") if path.is_file()}
    missing, extra = EXPECTED - actual, actual - EXPECTED
    if missing or extra:
        raise ValueError(f"Bundled root scripts differ: missing={sorted(missing)}, extra={sorted(extra)}")
    source_license = Path(__file__).resolve().parents[1] / "LICENSE"
    bundled_license = onedir / "_internal" / "LICENSE"
    if not bundled_license.is_file() or bundled_license.read_bytes() != source_license.read_bytes():
        raise ValueError("Bundled LICENSE is missing or differs from the source")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
