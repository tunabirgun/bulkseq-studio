from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    # When frozen by PyInstaller, bundled data (app/data, workflow, scripts) lives
    # under sys._MEIPASS; in development it is the repository root.
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_path(name: str) -> Path:
    return app_root() / "app" / "data" / name


def workflow_root() -> Path:
    return app_root() / "workflow"


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def windows_to_wsl_path(path: str | Path) -> str:
    p = Path(path).resolve()
    drive = p.drive.rstrip(":").lower()
    rest = "/".join(p.parts[1:])
    if drive:
        return f"/mnt/{drive}/{rest}"
    return str(p).replace("\\", "/")
