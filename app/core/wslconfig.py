from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Read/update the WSL2 resource caps in %UserProfile%\.wslconfig ([wsl2] memory=/processors=).
# The pipeline runs inside the WSL2 VM, whose caps (default ~50% of host RAM) bound memory-heavy
# steps like STAR; letting the user raise them from the app avoids hand-editing .wslconfig and
# running `wsl --shutdown`. Windows-only (there is no WSL on native Linux).


def wslconfig_path() -> Path:
    return Path.home() / ".wslconfig"


def read_wsl2_limits() -> dict[str, object]:
    """Return {'memory': '<value>'|None, 'processors': int|None} from the [wsl2] section."""
    result: dict[str, object] = {"memory": None, "processors": None}
    path = wslconfig_path()
    if not path.exists():
        return result
    in_wsl2 = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_wsl2 = line[1:-1].strip().lower() == "wsl2"
            continue
        if not in_wsl2 or line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key == "memory":
            result["memory"] = value or None
        elif key == "processors":
            result["processors"] = int(value) if value.isdigit() else None
    return result


def _format_memory(memory_gb: int | None) -> str | None:
    return f"{memory_gb}GB" if memory_gb and memory_gb > 0 else None


def write_wsl2_limits(memory_gb: int | None, processors: int | None) -> Path:
    """Set/clear [wsl2] memory= and processors= in .wslconfig, preserving everything else
    (other sections, comments, key order). A None value removes that key."""
    path = wslconfig_path()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    desired = {"memory": _format_memory(memory_gb),
               "processors": str(processors) if processors and processors > 0 else None}

    out: list[str] = []
    in_wsl2 = False
    wsl2_seen = False
    handled: set[str] = set()

    def flush_missing() -> None:
        # Append any desired key not already present when leaving the [wsl2] section.
        for key in ("memory", "processors"):
            if key not in handled and desired[key] is not None:
                out.append(f"{key}={desired[key]}")
                handled.add(key)

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_wsl2:
                flush_missing()
            in_wsl2 = stripped[1:-1].strip().lower() == "wsl2"
            if in_wsl2:
                wsl2_seen = True
            out.append(raw)
            continue
        if in_wsl2 and "=" in stripped and not stripped.startswith(("#", ";")):
            key = stripped.partition("=")[0].strip().lower()
            if key in ("memory", "processors"):
                handled.add(key)
                if desired[key] is not None:
                    out.append(f"{key}={desired[key]}")
                # None -> drop the line
                continue
        out.append(raw)

    if in_wsl2:
        flush_missing()
    if not wsl2_seen and any(v is not None for v in desired.values()):
        if out and out[-1].strip():
            out.append("")
        out.append("[wsl2]")
        for key in ("memory", "processors"):
            if desired[key] is not None:
                out.append(f"{key}={desired[key]}")

    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    return path


def apply_wsl_shutdown(timeout: int = 30) -> tuple[bool, str]:
    """Run `wsl --shutdown` so new .wslconfig caps take effect. Returns (ok, message)."""
    if not sys.platform.startswith("win"):
        return False, "WSL is only present on Windows."
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(["wsl", "--shutdown"], capture_output=True, text=True,
                              timeout=timeout, check=False, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, "WSL was shut down; it restarts with the new limits on the next run."
    return False, (proc.stderr or proc.stdout or "wsl --shutdown failed").strip()
