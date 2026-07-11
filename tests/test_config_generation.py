from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.config_models import WorkflowConfig, default_config
from app.core.project import ProjectManager


BASE = Path("manual_test_config")


def test_project_creation_writes_config():
    base = BASE / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    root = ProjectManager().create_project("My Project", base)
    cfg = ProjectManager().load_config(root)
    assert cfg.project.name == "My_Project"
    assert (root / "config" / "samples.tsv").exists()
    assert (root / "workflow" / "Snakefile").exists()


def test_default_config_has_star_route():
    cfg = default_config("demo", BASE / "demo")
    assert cfg.workflow.aligner == "STAR"
    assert cfg.workflow.quantifier == "featureCounts"


def test_deseq2_shrinkage_method_validation():
    import pytest
    from pydantic import ValidationError

    from app.core.config_models import Deseq2Config

    for ok in ("apeglm", "ashr", "normal"):
        assert Deseq2Config(shrinkage_method=ok).shrinkage_method == ok
    with pytest.raises(ValidationError):
        Deseq2Config(shrinkage_method="bogus")


def test_organellar_genes_default_and_round_trip():
    # Default keeps organellar genes; the choice survives a dump -> reload round-trip.
    assert WorkflowConfig().organellar_genes == "keep"
    for mode in ("keep", "discard", "separate"):
        wf = WorkflowConfig(organellar_genes=mode)
        assert WorkflowConfig(**wf.model_dump()).organellar_genes == mode


def test_de_engine_default_and_round_trip():
    # DESeq2 is the default engine; the choice survives a dump -> reload round-trip.
    assert WorkflowConfig().de_engine == "DESeq2"
    for engine in ("DESeq2", "limma-voom", "edgeR"):
        wf = WorkflowConfig(de_engine=engine)
        assert WorkflowConfig(**wf.model_dump()).de_engine == engine


def test_de_engine_rejects_unknown():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkflowConfig(de_engine="sleuth")


def test_quantifier_dead_htseq_value_removed():
    # C1 cleanup: the unreachable htseq-count value (the Snakefile rejects it) is gone.
    assert "htseq-count" not in WorkflowConfig.model_fields["quantifier"].annotation.__args__


def test_trimmer_default_and_round_trip():
    assert WorkflowConfig().trimmer == "fastp"
    for t in ("fastp", "trim-galore", "trimmomatic"):
        assert WorkflowConfig(**WorkflowConfig(trimmer=t).model_dump()).trimmer == t


def test_rrna_tool_default_and_round_trip():
    assert WorkflowConfig().rrna_tool == "sortmerna"
    for t in ("sortmerna", "ribodetector"):
        assert WorkflowConfig(**WorkflowConfig(rrna_tool=t).model_dump()).rrna_tool == t


def test_contamination_screen_default_and_round_trip():
    assert WorkflowConfig().contamination_screen is False
    for v in (True, False):
        assert WorkflowConfig(**WorkflowConfig(contamination_screen=v).model_dump()).contamination_screen is v


def test_preproc_selectors_reject_unknown():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkflowConfig(trimmer="cutadapt")
    with pytest.raises(ValidationError):
        WorkflowConfig(rrna_tool="bbduk")


def test_gsva_rseqc_defaults_and_round_trip():
    assert WorkflowConfig().gsva is False
    assert WorkflowConfig().rseqc is False
    for field in ("gsva", "rseqc"):
        wf = WorkflowConfig(**{field: True})
        assert getattr(WorkflowConfig(**wf.model_dump()), field) is True


def test_advanced_tool_params_round_trip():
    # The new per-tool advanced parameters serialize and reload through the full config.
    from app.core.config_models import (
        AppConfig, ContaminationConfig, FastpConfig, RibodetectorConfig, TrimmomaticConfig,
    )

    assert FastpConfig().trim_poly_x is False
    assert TrimmomaticConfig().sliding_window_quality == 15
    assert RibodetectorConfig().ensure == "norrna"
    assert ContaminationConfig().subset == 100000

    cfg = default_config("demo", BASE / "adv")
    cfg.fastp.trim_poly_x = True
    cfg.trimmomatic.sliding_window_quality = 20
    cfg.ribodetector.chunk_size = 512
    cfg.contamination.subset = 250000
    cfg.star.twopass_mode = True
    cfg.deseq2.min_count = 5
    reloaded = AppConfig(**cfg.model_dump())
    assert reloaded.fastp.trim_poly_x is True
    assert reloaded.trimmomatic.sliding_window_quality == 20
    assert reloaded.ribodetector.chunk_size == 512
    assert reloaded.contamination.subset == 250000
    assert reloaded.star.twopass_mode is True
    assert reloaded.deseq2.min_count == 5


def test_volcano_top_n_zero_round_trips():
    # 0 = "no volcano labels" is offered by the spinner and handled by make_figures.R, so it must be a
    # valid, reopenable config value — the old positive-int rule rejected 0 and made the project fail to load.
    from app.core.config_models import FigureConfig
    assert FigureConfig(volcano_top_n=0).volcano_top_n == 0
    for bad in (-1,):
        try:
            FigureConfig(volcano_top_n=bad)
            assert False, "negative volcano_top_n must be rejected"
        except Exception:
            pass
    # other figure counts still require > 0
    for field in ("base_font_size", "heatmap_top_n", "pca_ntop"):
        try:
            FigureConfig(**{field: 0})
            assert False, f"{field}=0 must still be rejected"
        except Exception:
            pass


def test_volcano_y_scale_round_trips():
    # cap = default (current behaviour); full/sqrt let extreme genes show; a bad value is rejected.
    from app.core.config_models import FigureConfig
    assert FigureConfig().volcano_y_scale == "cap"
    for v in ("cap", "full", "sqrt"):
        assert FigureConfig(volcano_y_scale=v).volcano_y_scale == v
    try:
        FigureConfig(volcano_y_scale="nope")
        assert False, "invalid volcano_y_scale must be rejected"
    except Exception:
        pass
