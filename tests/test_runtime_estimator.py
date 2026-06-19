from __future__ import annotations

from pathlib import Path

from app.core.config_models import default_config
from app.core.runtime_estimator import estimate_runtime


def test_runtime_estimate_has_range() -> None:
    cfg = default_config("demo", Path("manual_test_runtime/demo"))
    estimate = estimate_runtime(cfg)
    assert "range" in estimate
    assert estimate["low_seconds"] > 0
