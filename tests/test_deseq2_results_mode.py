from app.core.config_models import InputConfig


def test_deseq2_results_input_mode_roundtrips() -> None:
    cfg = InputConfig(type="deseq2_results", deseq2_results="config/deseq2_results.csv")
    assert cfg.type == "deseq2_results"
    assert cfg.deseq2_results == "config/deseq2_results.csv"
    dumped = cfg.model_dump()
    assert dumped["type"] == "deseq2_results"
    assert dumped["deseq2_results"] == "config/deseq2_results.csv"
    assert InputConfig.model_validate(dumped).type == "deseq2_results"


def test_default_input_has_no_deseq2_results() -> None:
    cfg = InputConfig()
    assert cfg.type == "fastq"
    assert cfg.deseq2_results is None
