from __future__ import annotations

from pathlib import Path

from app.core.config_models import default_config
from app.core.runtime_estimator import estimate_runtime, INDEX_MINUTES


def test_runtime_estimate_has_range() -> None:
    cfg = default_config("demo", Path("manual_test_runtime/demo"))
    estimate = estimate_runtime(cfg)
    assert "range" in estimate
    assert estimate["low_seconds"] > 0


def test_star_index_build_time_is_included_regardless_of_reference_star_index() -> None:
    # reference.smk's star_index rule always builds the index from GENOME_FA/ANNOTATION_GTF;
    # config.reference.star_index has no rule that reads it and never skips the build, so the
    # estimate must always pay this cost for the STAR aligner.
    def build(star_index: str | None):
        cfg = default_config("demo", Path("manual_test_runtime/demo"))
        cfg.workflow.aligner = "STAR"
        cfg.reference.genome_size_category = "mammalian"
        cfg.reference.star_index = star_index
        return estimate_runtime(cfg)

    with_index_set = build("/pre/built/star_index")
    without_index_set = build(None)

    assert with_index_set["compute_minutes"] >= INDEX_MINUTES["mammalian"]
    assert with_index_set["compute_minutes"] == without_index_set["compute_minutes"]
    assert any("STAR index build" in b for b in with_index_set["bottlenecks"])
