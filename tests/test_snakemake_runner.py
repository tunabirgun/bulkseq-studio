from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core.config_models import default_config
from app.core.snakemake_runner import build_snakemake_command, build_wsl_kill_command


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


def test_wsl_stop_finds_the_exported_tag_in_proc_environments() -> None:
    tag = "BULKSEQ_RUN_TAG_0123456789abcdef0123456789abcdef"
    command = build_wsl_kill_command(tag, "Ubuntu-24.04")
    inner = command[-1]
    assert command[:5] == ["wsl", "-d", "Ubuntu-24.04", "--exec", "bash"]
    assert "/proc/[0-9]*/environ" in inner
    assert f"{tag}=1" in inner
    assert "grep -Fqx" in inner
    assert "kill -TERM $pids" in inner
    assert "pkill" not in inner


def test_wsl_stop_rejects_untrusted_tags_and_signals() -> None:
    with pytest.raises(ValueError):
        build_wsl_kill_command("BULKSEQ_RUN_TAG_bad; reboot")
    with pytest.raises(ValueError):
        build_wsl_kill_command(
            "BULKSEQ_RUN_TAG_0123456789abcdef0123456789abcdef", signal="USR1"
        )


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


def test_figures_mode_forces_gsva_only_when_its_rule_and_inputs_exist(tmp_path) -> None:
    # gsva writes a styled heatmap, so a restyle must re-render it -- but only when the Snakefile
    # actually defines the rule (GSVA_ON) and every declared input is still on disk. The score CSV
    # alone is not enough: results/export/ is not protected by reclaim_run_space.sh.
    cfg = default_config("demo", tmp_path)
    cfg.workflow.gsva = True
    (tmp_path / "config").mkdir(parents=True)
    gmt = tmp_path / "config" / "sets.gmt"
    gmt.write_text("SET1\tna\tGENE1\tGENE2\n")
    cfg.gene_sets.custom_gene_sets = "config/sets.gmt"
    # Nothing produced yet -> not forced.
    assert "gsva" not in build_snakemake_command(tmp_path, cfg, mode="figures").command
    (tmp_path / "results" / "gsva").mkdir(parents=True)
    (tmp_path / "results" / "gsva" / "gsva_scores.csv").write_text("x")
    # Scores present but the normalized matrix reclaimed -> still not forced (MissingInputException).
    assert "gsva" not in build_snakemake_command(tmp_path, cfg, mode="figures").command
    (tmp_path / "results" / "export").mkdir(parents=True)
    (tmp_path / "results" / "export" / "normalized_expression_matrix.csv").write_text("x")
    assert "gsva" in build_snakemake_command(tmp_path, cfg, mode="figures").command
    # Each half of the Snakefile's GSVA_ON guard must switch it back off: without a gene-set file,
    # and on a deseq2-results upload, the rule is undefined and naming it aborts the whole run.
    cfg.gene_sets.custom_gene_sets = None
    assert "gsva" not in build_snakemake_command(tmp_path, cfg, mode="figures").command
    cfg.gene_sets.custom_gene_sets = "config/sets.gmt"
    cfg.input.type = "deseq2_results"
    assert "gsva" not in build_snakemake_command(tmp_path, cfg, mode="figures").command
    cfg.input.type = "fastq"
    cfg.workflow.gsva = False
    assert "gsva" not in build_snakemake_command(tmp_path, cfg, mode="figures").command


def test_figures_mode_forces_the_transfer_figure_only_under_transfer_on(tmp_path) -> None:
    # The figure rule exists only while the Snakefile's TRANSFER_ON holds for the current
    # configuration; naming it otherwise aborts the whole regenerate.
    cfg = default_config("demo", tmp_path)
    cfg.workflow.enrichment = True
    cfg.reference.organism_name = "Fusarium graminearum"
    cfg.ppi.taxon = 229533

    def forced() -> bool:
        return "transfer_enrichment_figure" in build_snakemake_command(tmp_path, cfg, mode="figures").command

    assert not forced()
    out = tmp_path / "results" / "enrichment" / "transfer"
    out.mkdir(parents=True)
    (out / "transfer_ora.csv").write_text("x")
    assert forced()
    cfg.enrichment.transfer = "off"
    assert not forced()
    cfg.enrichment.transfer = "auto"
    cfg.reference.organism_name = "Drosophila melanogaster"
    assert not forced()
    cfg.enrichment.transfer = "on"
    assert forced()
    cfg.ppi.taxon = None
    assert not forced()
    cfg.enrichment.transfer_ko_table = "config/ko.txt"
    assert forced()
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "samples.tsv").write_text(
        "sample\tcondition\tdataset\na\tx\tS1\nb\ty\tS2\n", encoding="utf-8")
    cfg.workflow.meta_analysis = True
    assert not forced()
    cfg.workflow.meta_analysis = False
    cfg.workflow.enrichment = False
    assert not forced()


def test_figures_mode_forces_meta_per_study_under_the_meta_guard(tmp_path) -> None:
    # meta_per_study renders styled per-study figures; it must be forced when its manifest and
    # inputs exist, and must stay out whenever the meta rules themselves are undefined.
    cfg = default_config("demo", tmp_path)
    cfg.workflow.meta_analysis = True
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "samples.tsv").write_text(
        "sample_id\tdataset\tcondition\ns1\tD1\tA\ns2\tD2\tB\n")
    (tmp_path / "results" / "meta").mkdir(parents=True)
    (tmp_path / "results" / "meta" / "meta_analysis_results.csv").write_text("x")
    # Meta figures are forced, but per-study has not run (no manifest).
    cmd = build_snakemake_command(tmp_path, cfg, mode="figures").command
    assert "meta_figures" in cmd and "meta_per_study" not in cmd
    (tmp_path / "results" / "meta" / "per_study").mkdir()
    (tmp_path / "results" / "meta" / "per_study" / "manifest.json").write_text("{}")
    # Manifest without the pooled DE table it reads -> still not forced.
    assert "meta_per_study" not in build_snakemake_command(tmp_path, cfg, mode="figures").command
    (tmp_path / "results" / "deseq2").mkdir(parents=True)
    (tmp_path / "results" / "deseq2" / "deseq2_results.csv").write_text("x")
    assert "meta_per_study" in build_snakemake_command(tmp_path, cfg, mode="figures").command
    # Single-study sheet -> the meta rules are undefined, so per-study goes with them.
    (tmp_path / "config" / "samples.tsv").write_text(
        "sample_id\tdataset\tcondition\ns1\tD1\tA\ns2\tD1\tB\n")
    assert "meta_per_study" not in build_snakemake_command(tmp_path, cfg, mode="figures").command


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


def test_snakemake_run_state_detects_resumable(tmp_path) -> None:
    from app.core.snakemake_runner import snakemake_run_state
    # No .snakemake -> a fresh/clean project is not resumable.
    assert snakemake_run_state(tmp_path) == {"resumable": False, "locked": False, "incomplete": False}
    assert snakemake_run_state(None)["resumable"] is False
    # Incomplete outputs (a stopped/crashed run) -> resumable.
    inc = tmp_path / ".snakemake" / "incomplete"; inc.mkdir(parents=True); (inc / "x").write_text("1")
    st = snakemake_run_state(tmp_path)
    assert st["resumable"] and st["incomplete"] and not st["locked"]
    # A held lock (hard-killed / app-closed run) -> resumable + locked.
    lk = tmp_path / ".snakemake" / "locks"; lk.mkdir(parents=True); (lk / "0").write_text("1")
    assert snakemake_run_state(tmp_path)["locked"]
    # Empty locks/incomplete dirs (a cleanly completed run) -> NOT resumable.
    for d in ("locks", "incomplete"):
        for f in (tmp_path / ".snakemake" / d).iterdir():
            f.unlink()
    assert snakemake_run_state(tmp_path) == {"resumable": False, "locked": False, "incomplete": False}
