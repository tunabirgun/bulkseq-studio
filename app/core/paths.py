from __future__ import annotations

from pathlib import Path


def app_root() -> Path:
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
