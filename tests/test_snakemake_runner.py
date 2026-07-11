from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core.config_models import default_config
from app.core.snakemake_runner import build_snakemake_command


def test_snakemake_command_uses_project_workflow_snakefile() -> None:
    cfg = default_config("demo", Path("demo"))
    command = build_snakemake_command(Path("demo"), cfg, mode="dry-run")
    assert command.command[:4] == ["snakemake", "-n", "--snakefile", "workflow/Snakefile"]
    assert "config/config.yaml" in command.command


def test_unlock_command_uses_project_workflow_snakefile() -> None:
    cfg = default_config("demo", Path("demo"))
    command = build_snakemake_command(Path("demo"), cfg, mode="unlock")
    assert command.command[:3] == ["snakemake", "--snakefile", "workflow/Snakefile"]
    assert "--unlock" in command.command


@pytest.mark.skipif(sys.platform != "win32", reason="Windows->WSL /mnt/c path translation only applies on a Windows host")
def test_wsl_command_quotes_project_paths_with_spaces() -> None:
    cfg = default_config("demo", Path("C:/Users/Tuna/Desktop/BulkSeq Studio/demo"))
    command = build_snakemake_command(Path("C:/Users/Tuna/Desktop/BulkSeq Studio/demo"), cfg, mode="dry-run", use_wsl=True)
    inner = command.command[-1]
    assert "cd '/mnt/c/Users/Tuna/Desktop/BulkSeq Studio/demo'" in inner
    assert "snakemake -n --snakefile workflow/Snakefile" in inner
    assert '"cd \'/mnt/c/Users/Tuna/Desktop/BulkSeq Studio/demo\'' in command.display


def test_wsl_command_activates_micromamba_env() -> None:
    # The login shell does not put the bulkseq env on PATH, so the runner must
    # invoke snakemake through `micromamba run -n bulkseq` (the same mechanism the
    # readiness check probes). This is the bug a string-only test would miss.
    cfg = default_config("demo", Path("C:/work/demo"))
    command = build_snakemake_command(Path("C:/work/demo"), cfg, mode="run", use_wsl=True)
    inner = command.command[-1]
    # The micromamba path is double-quoted (preserving $HOME expansion) to be safe
    # against spaces, so the env activation reads ".../micromamba" run -n bulkseq.
    assert 'micromamba" run -n bulkseq snakemake' in inner
    assert "MAMBA_ROOT_PREFIX" in inner
    assert command.command[:3] == ["wsl", "--", "bash"]


def test_figures_mode_gates_optional_targets_on_input_existence(tmp_path) -> None:
    # Optional figure rules must be forced only when their upstream input exists;
    # forcing one whose input is absent raises MissingInputException and fails the
    # whole "Regenerate figures" run.
    cfg = default_config("demo", tmp_path)
    cfg.workflow.enrichment = True
    cfg.ppi.enabled = True
    no_inputs = build_snakemake_command(tmp_path, cfg, mode="figures").command
    assert "figures" in no_inputs
    assert "enrichment_figures" not in no_inputs
    assert "network_string" not in no_inputs

    (tmp_path / "results" / "enrichment").mkdir(parents=True)
    (tmp_path / "results" / "enrichment" / "enrichment_objects.rds").write_text("x")
    (tmp_path / "results" / "deseq2").mkdir(parents=True)
    (tmp_path / "results" / "deseq2" / "deseq2_results.csv").write_text("x")
    with_inputs = build_snakemake_command(tmp_path, cfg, mode="figures").command
    assert "enrichment_figures" in with_inputs
    assert "network_string" in with_inputs


def test_native_command_has_no_use_conda() -> None:
    # No rule declares a conda: directive; --use-conda would be a no-op and is
    # intentionally omitted.
    cfg = default_config("demo", Path("demo"))
    command = build_snakemake_command(Path("demo"), cfg, mode="run")
    assert "--use-conda" not in command.command


def test_figures_mode_meta_targets_gated_on_current_sheet_being_multistudy(tmp_path) -> None:
    # A stale results/meta/ left from an earlier multi-study run must NOT make "Regenerate figures"
    # force the meta rules once the sheet is edited down to one study (those rules are then undefined,
    # and naming them aborts the run). The gate is the CURRENT samples.tsv, not the leftover outputs.
    cfg = default_config("demo", tmp_path)
    cfg.workflow.meta_analysis = True
    cfg.workflow.enrichment = True
    (tmp_path / "results" / "meta").mkdir(parents=True)
    (tmp_path / "results" / "meta" / "meta_analysis_results.csv").write_text("x")
    (tmp_path / "results" / "meta" / "meta_enrichment_objects.rds").write_text("x")
    (tmp_path / "results" / "reports").mkdir(parents=True)
    (tmp_path / "results" / "reports" / "meta_analysis_summary.json").write_text("{}")
    (tmp_path / "config").mkdir(parents=True)
    # Single-study sheet -> no meta targets even though the meta outputs still exist on disk.
    (tmp_path / "config" / "samples.tsv").write_text(
        "sample_id\tdataset\tcondition\ns1\tD1\tA\ns2\tD1\tB\n")
    single = build_snakemake_command(tmp_path, cfg, mode="figures").command
    assert "meta_figures" not in single
    assert "meta_report" not in single
    # Genuine multi-study sheet -> meta targets are forced.
    (tmp_path / "config" / "samples.tsv").write_text(
        "sample_id\tdataset\tcondition\ns1\tD1\tA\ns2\tD2\tB\n")
    multi = build_snakemake_command(tmp_path, cfg, mode="figures").command
    assert "meta_figures" in multi
    assert "meta_report" in multi
    # Even multi-study, the meta rules are undefined for microarray / uploaded-DE-results inputs
    # (Snakefile META_MODE excludes them), so figures-mode must not force them there either.
    for non_count in ("deseq2_results", "microarray"):
        cfg.input.type = non_count
        cmd = build_snakemake_command(tmp_path, cfg, mode="figures").command
        assert "meta_figures" not in cmd, non_count
        assert "meta_report" not in cmd, non_count


def test_is_multistudy_matches_snakefile_pandas_na_coercion(tmp_path) -> None:
    # _is_multistudy must mirror the Snakefile's MULTI_DATASET exactly, incl. pandas coercing NA-family
    # tokens to empty. A study literally named "NA" collapses to '', so {"NA","D2"} is a SINGLE study.
    from app.core.snakemake_runner import _is_multistudy
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    tsv = cfg_dir / "samples.tsv"

    def _write(rows):
        tsv.write_text("sample_id\tdataset\tcondition\n" + "".join(rows))

    _write(["s1\tNA\tA\n", "s2\tD2\tB\n"])          # NA -> '' -> only D2 -> single study
    assert _is_multistudy(tmp_path) is False
    _write(["s1\tD1\tA\n", "s2\tD2\tB\n"])          # two real studies
    assert _is_multistudy(tmp_path) is True
    _write(["s1\t \tA\n", "s2\tD1\tB\n"])           # whitespace-only + one real -> single study
    assert _is_multistudy(tmp_path) is False
    _write(["s1\tD1\tA\n"])                          # one study
    assert _is_multistudy(tmp_path) is False
    tsv.write_text("sample_id\tcondition\ns1\tA\n")  # no dataset column
    assert _is_multistudy(tmp_path) is False
