from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings

# Isolate QSettings to a throwaway ini so the tests never touch the user's real settings.
QCoreApplication.setOrganizationName("BulkSeqStudioTest")
QCoreApplication.setApplicationName("BulkSeqStudioTest")
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())

from app.core.config_models import default_config  # noqa: E402
from app.core.runtime_calibration import calibration_factor, load_samples, record_run  # noqa: E402
from app.core.runtime_estimator import estimate_runtime  # noqa: E402


def _cfg(input_type: str = "sra"):
    cfg = default_config("demo", Path("manual_test_runtime/demo"))
    cfg.input.type = input_type  # type: ignore[assignment]
    return cfg


# --- Step 0: results-upload fast path ---------------------------------------

def test_deseq2_results_is_fast_and_not_alignment_shaped() -> None:
    est = estimate_runtime(_cfg("deseq2_results"))
    # Compute is tiny (enrichment/figures only), not an inflated alignment estimate.
    assert est["compute_minutes"] < 3
    assert est["high_seconds"] < 20 * 60        # minutes, not the hours an alignment estimate gave
    assert est["aligner"] == "n/a (results upload)"
    joined = " ".join(est["bottlenecks"]).lower()
    assert "alignment" not in joined and "sequencing volume" not in joined


# --- calibration_factor -----------------------------------------------------

def test_calibration_empty_is_neutral() -> None:
    assert calibration_factor(999) == (1.0, 0)


def test_calibration_median_of_ratios_and_clamp() -> None:
    cores = 101
    record_run(cores, predicted_raw_compute_min=10.0, actual_wall_min=20.0)  # ratio 2
    record_run(cores, predicted_raw_compute_min=10.0, actual_wall_min=30.0)  # ratio 3
    record_run(cores, predicted_raw_compute_min=10.0, actual_wall_min=25.0)  # ratio 2.5
    factor, n = calibration_factor(cores)
    assert n == 3
    assert abs(factor - 2.5) < 1e-6            # median of (2, 3, 2.5)
    # Clamp: a wild ratio cannot push the factor past 3.0
    for _ in range(5):
        record_run(cores, predicted_raw_compute_min=1.0, actual_wall_min=1000.0)
    factor, _ = calibration_factor(cores)
    assert factor <= 3.0


def test_calibration_ignores_sra_and_tiny() -> None:
    cores = 102
    record_run(cores, 10.0, 20.0, is_sra=True)      # SRA -> ignored
    record_run(cores, 0.1, 5.0)                      # predicted below floor -> ignored
    record_run(cores, 10.0, 0.0)                     # zero actual -> ignored
    assert load_samples(cores) == []
    assert calibration_factor(cores) == (1.0, 0)


def test_calibration_window_caps_at_ten() -> None:
    cores = 103
    for i in range(15):
        record_run(cores, 10.0, 10.0 + i)
    assert len(load_samples(cores)) == 10


# --- estimate_runtime honours the factor ------------------------------------

def test_factor_scales_compute_not_download() -> None:
    cfg = _cfg("sra")
    base = estimate_runtime(cfg, calibration_factor=1.0)
    doubled = estimate_runtime(cfg, calibration_factor=2.0)
    # compute doubles; download and overhead are untouched
    assert abs(doubled["compute_minutes"] - 2 * base["compute_minutes"]) < 0.05
    assert abs(doubled["download_minutes"] - base["download_minutes"]) < 1e-6


def test_range_narrows_as_calibration_grows() -> None:
    cfg = _cfg("sra")
    def width(n: int) -> float:
        e = estimate_runtime(cfg, calibration_runs=n)
        return e["high_seconds"] - e["low_seconds"]
    # more calibration runs -> tighter (or equal) range
    assert width(5) <= width(1) <= width(0)
    assert estimate_runtime(cfg, calibration_runs=0)["calibrated"] is False
    assert estimate_runtime(cfg, calibration_runs=2)["calibrated"] is True
