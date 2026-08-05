from __future__ import annotations

# Per-machine runtime self-calibration. After each local (non-SRA) run the app records the
# predicted raw compute time and the actual wall time; the ratio's median becomes a speed
# correction factor for future estimates, so the estimate converges to the user's real
# hardware. Stored in QSettings keyed by hostname + core count (a laptop and a workstation
# calibrate independently). Network-bound SRA downloads never update the factor, so download
# jitter is not mistaken for hardware speed.

import json
import socket
import statistics
from pathlib import Path

from app.core.paths import user_data_dir

# QSettings is the store when Qt is present, so an existing GUI user's calibration history
# survives untouched. It is imported lazily rather than at module scope because app.core is
# also the CLI's library layer, and a cluster login node has no Qt: a top-level PySide6
# import here made this module — and anything that reached it — unimportable there.
# Without Qt the samples go to a JSON file under the same per-user data directory.


def _qsettings():
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        return None
    return QSettings()


def _fallback_file() -> Path:
    return user_data_dir() / "runtime_calibration.json"


def _read_raw(key: str) -> str:
    settings = _qsettings()
    if settings is not None:
        return str(settings.value(key, "") or "")
    try:
        return json.loads(_fallback_file().read_text(encoding="utf-8")).get(key, "")
    except (OSError, ValueError):
        return ""


def _write_raw(key: str, value: str) -> None:
    settings = _qsettings()
    if settings is not None:
        settings.setValue(key, value)
        return
    path = _fallback_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            store = {}
        store[key] = value
        path.write_text(json.dumps(store), encoding="utf-8")
    except OSError:
        # Calibration is an optimisation, not a result. A read-only home directory must
        # not fail a run.
        pass


_MAX_SAMPLES = 10       # rolling window of the most recent runs
_MIN_PREDICT = 0.5      # ignore trivially small predictions (near-instant runs are noise)
_FACTOR_LO, _FACTOR_HI = 0.33, 3.0


def _key(cores: int) -> str:
    host = socket.gethostname() or "host"
    return f"runtime_calibration/{host}_{int(cores)}c"


def load_samples(cores: int) -> list[dict]:
    raw = _read_raw(_key(cores))
    if not raw:
        return []
    try:
        data = json.loads(raw if isinstance(raw, str) else str(raw))
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _save_samples(cores: int, samples: list[dict]) -> None:
    _write_raw(_key(cores), json.dumps(samples[-_MAX_SAMPLES:]))


def calibration_factor(cores: int) -> tuple[float, int]:
    """Return (factor, n_samples) for this machine+cores. (1.0, 0) when uncalibrated.

    factor = median(actual_wall / predicted_raw_compute), clamped, robust to one-off outliers.
    """
    samples = [s for s in load_samples(cores)
               if float(s.get("predicted", 0)) > _MIN_PREDICT and float(s.get("actual", 0)) > 0]
    if not samples:
        return 1.0, 0
    ratios = [float(s["actual"]) / float(s["predicted"]) for s in samples]
    factor = max(_FACTOR_LO, min(_FACTOR_HI, statistics.median(ratios)))
    return factor, len(samples)


def record_run(cores: int, predicted_raw_compute_min: float, actual_wall_min: float,
               gbase: float = 0.0, aligner: str = "", is_sra: bool = False) -> None:
    """Append one calibration sample. No-op for SRA runs (network-bound) or degenerate values."""
    if is_sra or predicted_raw_compute_min <= _MIN_PREDICT or actual_wall_min <= 0:
        return
    samples = load_samples(cores)
    samples.append({
        "predicted": float(predicted_raw_compute_min),
        "actual": float(actual_wall_min),
        "gbase": float(gbase),
        "aligner": str(aligner),
    })
    _save_samples(cores, samples)
