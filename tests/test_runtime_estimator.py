from __future__ import annotations

from pathlib import Path

from app.core.config_models import default_config
from app.core.runtime_estimator import estimate_runtime, INDEX_MINUTES


def test_runtime_estimate_has_range() -> None:
    cfg = default_config("demo", Path("manual_test_runtime/demo"))
    estimate = estimate_runtime(cfg)
    assert "range" in estimate
    assert estimate["low_seconds"] > 0


def test_star_index_build_time_is_skipped_when_a_prebuilt_index_is_configured() -> None:
    # reference.smk's star_index rule is skipped when config.reference.star_index is set, so
    # the estimate must not pay the index-build cost in that case.
    def build(star_index: str | None):
        cfg = default_config("demo", Path("manual_test_runtime/demo"))
        cfg.workflow.aligner = "STAR"
        cfg.reference.genome_size_category = "mammalian"
        cfg.reference.star_index = star_index
        return estimate_runtime(cfg)

    with_index_set = build("/pre/built/star_index")
    without_index_set = build(None)

    assert without_index_set["compute_minutes"] >= INDEX_MINUTES["mammalian"]
    assert with_index_set["compute_minutes"] < without_index_set["compute_minutes"]
    assert not any("STAR index build" in b for b in with_index_set["bottlenecks"])
    assert any("STAR index build" in b for b in without_index_set["bottlenecks"])
