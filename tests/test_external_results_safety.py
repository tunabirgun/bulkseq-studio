from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest


ROOT = Path(__file__).resolve().parents[1]
INGEST = ROOT / "workflow" / "scripts" / "ingest_deseq2_results.R"
EXPORT = ROOT / "workflow" / "scripts" / "export_downstream.R"


def _load_python_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


orientation = _load_python_script(
    "check_orientation_external_results",
    ROOT / "workflow" / "scripts" / "check_orientation.py",
)


def test_external_orientation_uses_only_source_direction_despite_conflicting_local_settings() -> None:
    cfg = {
        "input": {
            "type": "deseq2_results",
            "deseq2_results_direction": {
                "numerator": "treated_source",
                "denominator": "control_source",
                "confirmed": True,
                "confirmed_at": "2026-08-10T12:00:00+03:00",
            },
        },
        # Deliberately inverted and stale. None of these values may affect the external verdict.
        "deseq2": {
            "reference_level": {"legacy_factor": "legacy_case"},
            "contrasts": [{
                "factor": "legacy_factor",
                "numerator": "legacy_control",
                "denominator": "legacy_case",
            }],
        },
    }
    messages = orientation.orientation_messages(cfg)
    rendered = " ".join(m["message"] for m in messages)
    assert [m["status"] for m in messages] == ["PASS"]
    assert "treated_source" in rendered and "control_source" in rendered
    assert "legacy_" not in rendered
    assert "reference_level" not in rendered


def test_external_inverted_direction_requests_source_review_not_local_reference_change() -> None:
    messages = orientation.orientation_messages({
        "input": {
            "type": "deseq2_results",
            "deseq2_results_direction": {
                "numerator": "vehicle_control",
                "denominator": "tumor_case",
                "confirmed": True,
            },
        },
    })
    rendered = " ".join(m["message"] for m in messages)
    assert [m["status"] for m in messages] == ["REVIEW_REQUIRED"]
    assert "project copy's source analysis" in rendered
    assert "setting reference" not in rendered.casefold()
    assert "reference_level" not in rendered


def test_local_orientation_route_still_uses_local_contrast_and_reference() -> None:
    messages = orientation.orientation_messages({
        "input": {"type": "fastq"},
        "deseq2": {
            "reference_level": {"condition": "tumor_case"},
            "contrasts": [{
                "factor": "condition", "numerator": "vehicle_control",
                "denominator": "tumor_case",
            }],
        },
    })
    rendered = " ".join(message["message"] for message in messages)
    assert [message["status"] for message in messages] == ["REVIEW_REQUIRED"]
    assert "tumor_case" in rendered and "vehicle_control" in rendered
    assert "reference_level" in rendered


def test_external_ingest_has_a_validation_dependency_chain() -> None:
    deseq_rules = (ROOT / "workflow" / "rules" / "deseq2.smk").read_text(encoding="utf-8")
    check_rules = (ROOT / "workflow" / "rules" / "checks.smk").read_text(encoding="utf-8")
    assert 'validated="checks/01_input_validation.json"' in deseq_rules
    assert 'prev="checks/00_project_setup.json"' in check_rules
    assert "rule validate_project:" in check_rules
    assert "rule input_check:" in check_rules


def _r_runtime(harness: Path) -> tuple[list[str], Callable[[Path], str]]:
    native = shutil.which("Rscript")
    if native:
        return [native, "--vanilla", str(harness)], lambda path: str(path)

    wsl = shutil.which("wsl.exe")
    if not wsl:
        pytest.skip("Rscript is not available")
    probe = subprocess.run(
        [wsl, "bash", "-lc", (
            "Rscript --vanilla -e 'quit(status=if "
            "(requireNamespace(\"jsonlite\",quietly=TRUE)) 0 else 1)'"
        )],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip("Rscript with jsonlite is not available")

    def wsl_path(path: Path) -> str:
        converted = subprocess.run(
            [wsl, "bash", "-lc", f"wslpath -a {shlex.quote(str(path))}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return converted.stdout.strip()

    linux_harness = wsl_path(harness)
    return [wsl, "bash", "-lc", f"Rscript --vanilla {shlex.quote(linux_harness)}"], wsl_path


def _r_quote(value: str) -> str:
    # JSON strings and R character literals share the escapes used by these paths/labels.
    return json.dumps(value)


def _run_ingest(
    tmp_path: Path,
    table_text: str | bytes,
    *,
    numerator: str = 'case "A"',
    denominator: str = "control\\source",
    p_adjustment_method: str = "unknown",
) -> tuple[subprocess.CompletedProcess[str], dict[str, Path]]:
    table = tmp_path / "input.csv"
    if isinstance(table_text, bytes):
        table.write_bytes(table_text)
    else:
        table.write_text(table_text, encoding="utf-8")
    harness = tmp_path / "run_ingest.R"
    outputs = {
        "results": tmp_path / "results.csv",
        "up": tmp_path / "up.csv",
        "down": tmp_path / "down.csv",
        "rds": tmp_path / "objects.rds",
        "session": tmp_path / "session.txt",
        "design_check": tmp_path / "design.json",
        "deseq_check": tmp_path / "deseq.json",
    }
    command, convert = _r_runtime(harness)
    input_expr = f"list(table={_r_quote(convert(table))})"
    output_expr = "list(" + ",".join(
        f"{key}={_r_quote(convert(path))}" for key, path in outputs.items()
    ) + ")"
    params_expr = (
        "list(alpha=0.05,lfc_threshold=1.0,"
        f"numerator={_r_quote(numerator)},denominator={_r_quote(denominator)},"
        "upstream_method='unknown',lfc_shrinkage='unknown',"
        f"p_adjustment_method={_r_quote(p_adjustment_method)})"
    )
    harness.write_text(
        "setClass('SnakemakeStub', slots=c(input='list', output='list', params='list', log='list'))\n"
        f"snakemake <- new('SnakemakeStub', input={input_expr}, output={output_expr}, "
        f"params={params_expr}, log=list({_r_quote(convert(tmp_path / 'ingest.log'))}))\n"
        f"source({_r_quote(convert(INGEST))}, chdir=FALSE)\n",
        encoding="utf-8",
    )
    # _r_runtime translated a path before the harness existed; the path itself is stable.
    return subprocess.run(command, capture_output=True, text=True, timeout=60), outputs


def test_r_ingest_preserves_valid_signs_and_writes_escape_safe_json(tmp_path: Path) -> None:
    proc, outputs = _run_ingest(
        tmp_path,
        "gene_id,log2FoldChange,padj,stat,pvalue\n"
        "g_up,2.5,0.01,4.2,0.001\n"
        "g_down,-1.5,0.02,-3.1,0.002\n",
    )
    assert proc.returncode == 0, proc.stderr + (tmp_path / "ingest.log").read_text(
        encoding="utf-8", errors="replace"
    )
    with outputs["results"].open(encoding="utf-8", newline="") as handle:
        rows = {row["gene_id"]: row for row in csv.DictReader(handle)}
    assert float(rows["g_up"]["log2FoldChange"]) == 2.5
    assert float(rows["g_down"]["log2FoldChange"]) == -1.5
    payload = json.loads(outputs["deseq_check"].read_text(encoding="utf-8"))
    assert payload["check"] == "09_deseq2_qc"
    message = payload["messages"][0]["message"]
    assert 'case "A"' in message and "control\\source" in message
    assert "adjusted p-value" in message
    assert "adjustment method not recorded" in message


def test_r_ingest_reports_a_known_adjustment_method_without_calling_it_bh_by_default(
    tmp_path: Path,
) -> None:
    proc, outputs = _run_ingest(
        tmp_path,
        "gene_id,log2FoldChange,padj\ng1,1.25,0.02\n",
        p_adjustment_method="Benjamini-Hochberg",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(outputs["deseq_check"].read_text(encoding="utf-8"))
    message = payload["messages"][0]["message"]
    assert "adjusted p-value (Benjamini-Hochberg)" in message
    assert "adjustment method not recorded" not in message


def test_r_ingest_allows_canonical_missing_lfc_and_padj_when_each_column_has_finite_data(
    tmp_path: Path,
) -> None:
    proc, outputs = _run_ingest(
        tmp_path,
        "gene_id,log2FoldChange,padj\n"
        "g_complete,1.25,0.02\n"
        "g_missing_lfc,NA,0.50\n"
        "g_missing_padj,-0.75,\n",
    )
    assert proc.returncode == 0, proc.stderr
    with outputs["results"].open(encoding="utf-8", newline="") as handle:
        rows = {row["gene_id"]: row for row in csv.DictReader(handle)}
    assert rows["g_missing_lfc"]["log2FoldChange"] == "NA"
    assert rows["g_missing_padj"]["padj"] == "NA"


def test_r_ingest_accepts_standard_r_rowname_csv_without_guessing_a_measurement_column(
    tmp_path: Path,
) -> None:
    proc, outputs = _run_ingest(
        tmp_path,
        ",baseMean,log2FoldChange,padj\nENSG000001,25,1.25,0.02\n",
    )
    assert proc.returncode == 0, proc.stderr
    with outputs["results"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["gene_id"] == "ENSG000001"
    assert rows[0]["baseMean"] == "25"


def test_r_ingest_accepts_pandas_unnamed_index_column(tmp_path: Path) -> None:
    proc, outputs = _run_ingest(
        tmp_path,
        "Unnamed: 0,log2FoldChange,padj\nENSG000001,1.25,0.02\n",
    )
    assert proc.returncode == 0, proc.stderr
    with outputs["results"].open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["gene_id"] == "ENSG000001"


@pytest.mark.parametrize(
    "table_bytes, expected_symbol",
    [
        ("\ufeffgene_id,log2FoldChange,padj,symbol\ng1,1.0,0.01,café\n".encode("utf-8"), "café"),
        ("gene_id,log2FoldChange,padj,symbol\ng1,1.0,0.01,case–gene\n".encode("cp1252"), "case–gene"),
    ],
)
def test_r_ingest_matches_gui_supported_encodings(
    tmp_path: Path, table_bytes: bytes, expected_symbol: str
) -> None:
    proc, outputs = _run_ingest(tmp_path, table_bytes)
    assert proc.returncode == 0, proc.stderr + (tmp_path / "ingest.log").read_text(
        encoding="utf-8", errors="replace"
    )
    with outputs["results"].open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["gene_id"] == "g1"
    assert row["symbol"] == expected_symbol


@pytest.mark.parametrize(
    "table_text, expected",
    [
        ("gene_id,log2FoldChange,padj\ng1,not-a-number,0.01\n", "non-numeric token"),
        ("gene_id,log2FoldChange,padj\ng1,1.0,not-a-number\n", "non-numeric token"),
        ("gene_id,log2FoldChange,padj,stat\ng1,1.0,0.01,not-a-number\n", "non-numeric token"),
        ("gene_id,log2FoldChange,padj\ng1,Inf,0.01\n", "non-finite"),
        ("gene_id,log2FoldChange,padj\ng1,NaN,0.01\n", "non-finite"),
        ("gene_id,log2FoldChange,padj\ng1,1.0,Inf\n", "non-finite"),
        ("gene_id,log2FoldChange,padj,stat\ng1,1.0,0.01,Inf\n", "non-finite"),
        ("gene_id,log2FoldChange,padj\ng1,1.0,1.2\n", "outside [0, 1]"),
        ("gene_id,log2FoldChange,padj,stat\ng1,1.0,0.01,-2.0\n", "contradicts"),
        ("gene_id,log2FoldChange,padj\n,1.0,0.01\n", "empty / NA"),
        ("gene_id,log2FoldChange,padj\n'bad id',1.0,0.01\n", "whitespace"),
        ("gene_id,log2FoldChange,padj\ng1,NA,0.01\n", "no numeric values"),
        ("gene_id,log2FoldChange,padj\ng1,1.0,NA\n", "no numeric values"),
        ("gene_id,log2FoldChange,padj\ng1,1,25,0.01\n", "field count does not match"),
        ("gene_id,log2FoldChange,padj\n", "empty or unreadable"),
    ],
)
def test_r_ingest_rejects_invalid_external_tables_before_outputs(
    tmp_path: Path, table_text: str, expected: str
) -> None:
    proc, outputs = _run_ingest(tmp_path, table_text)
    log = (tmp_path / "ingest.log").read_text(encoding="utf-8", errors="replace")
    assert proc.returncode != 0
    assert expected in (proc.stderr + log)
    assert not any(path.exists() for path in outputs.values()), [
        str(path) for path in outputs.values() if path.exists()
    ]


def test_results_only_rank_export_uses_confirmed_log2fc_not_source_stat(tmp_path: Path) -> None:
    results = tmp_path / "results.csv"
    results.write_text(
        "gene_id,log2FoldChange,stat\n"
        "g_negative,-2,100\n"
        "g_positive,3,-100\n",
        encoding="utf-8",
    )
    rds = tmp_path / "objects.rds"
    harness = tmp_path / "run_export.R"
    command, convert = _r_runtime(harness)
    harness.write_text(
        "setClass('SnakemakeStub', slots=c(input='list', output='list', params='list', log='list'))\n"
        f"saveRDS(list(vsd=NULL, assay_kind='results_only'), {_r_quote(convert(rds))})\n"
        "snakemake <- new('SnakemakeStub', "
        f"input=list(rds={_r_quote(convert(rds))},results={_r_quote(convert(results))}),"
        f"output=list(vst={_r_quote(convert(tmp_path / 'matrix.csv'))},"
        f"rnk={_r_quote(convert(tmp_path / 'ranked.rnk'))}),params=list(),"
        f"log=list({_r_quote(convert(tmp_path / 'export.log'))}))\n"
        f"source({_r_quote(convert(EXPORT))}, chdir=FALSE)\n",
        encoding="utf-8",
    )
    proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    ranked = (tmp_path / "ranked.rnk").read_text(encoding="utf-8").strip().splitlines()
    assert ranked == ["g_positive\t3", "g_negative\t-2"]


def _wsl_bulkseq_snakemake(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    if os.name != "nt":
        snakemake = shutil.which("snakemake")
        if not snakemake:
            pytest.skip("The native Linux environment does not provide Snakemake")
        return subprocess.run(
            [snakemake, *arguments],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=180,
        )

    wsl = shutil.which("wsl.exe")
    if not wsl:
        pytest.skip("WSL is not available for the bundled Snakemake integration gate")
    probe = subprocess.run(
        [wsl, "bash", "-lc", (
            "command -v micromamba >/dev/null && "
            "micromamba run -n bulkseq snakemake --version >/dev/null"
        )],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode != 0:
        pytest.skip("The WSL bulkseq environment does not provide Snakemake")
    linux_project = subprocess.run(
        [wsl, "bash", "-lc", f"wslpath -a {shlex.quote(str(project))}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    args = " ".join(shlex.quote(argument) for argument in arguments)
    shell_command = (
        f"cd {shlex.quote(linux_project)} && "
        f"micromamba run -n bulkseq snakemake {args}"
    )
    return subprocess.run(
        [wsl, "bash", "-lc", shell_command],
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_result_only_snakemake_dry_run_and_negative_provenance_gate(tmp_path: Path) -> None:
    from app.core.config_models import (
        Deseq2ResultsDirectionProvenance,
        Deseq2ResultsFileProvenance,
    )
    from app.core.project import ProjectManager

    manager = ProjectManager()
    project = manager.create_project("external_gate", tmp_path)
    (project / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\tlayout\tfastq_1\n", encoding="utf-8"
    )
    (project / "config" / "uploaded.csv").write_text(
        "gene_id,log2FoldChange,padj\ng1,1.5,0.01\n", encoding="utf-8"
    )
    cfg = manager.load_config(project)
    cfg.input.type = "deseq2_results"
    cfg.input.deseq2_results = "config/uploaded.csv"
    cfg.input.deseq2_results_direction = Deseq2ResultsDirectionProvenance(
        numerator="case", denominator="control", confirmed=True,
        confirmed_at="2026-08-10T12:00:00+03:00",
    )
    cfg.workflow.enrichment = False
    cfg.workflow.figures = False
    cfg.ppi.enabled = False
    manager.save_config(project, cfg)

    failed = _wsl_bulkseq_snakemake(
        project,
        "--snakefile", "workflow/Snakefile", "--cores", "1",
        "results/deseq2/deseq2_results.csv",
    )
    failure_text = failed.stdout + failed.stderr
    assert failed.returncode != 0
    assert "no complete import-time provenance record" in failure_text
    assert not (project / "results" / "deseq2" / "deseq2_results.csv").exists()
    assert not (project / "checks" / "08_metadata_design_qc.json").exists()
    assert not (project / "checks" / "09_deseq2_qc.json").exists()

    project_copy = project / "config" / "uploaded.csv"
    cfg.input.deseq2_results_provenance = Deseq2ResultsFileProvenance(
        original_basename="source.csv",
        imported_at="2026-08-10T12:00:00+03:00",
        project_copy="config/uploaded.csv",
        sha256=hashlib.sha256(project_copy.read_bytes()).hexdigest(),
        byte_size=project_copy.stat().st_size,
        row_count=1,
        column_names=["gene_id", "log2FoldChange", "padj"],
        gene_id_column="gene_id",
        log2fc_column="log2FoldChange",
        adjusted_p_column="padj",
        upstream_method="unknown",
        lfc_shrinkage="unknown",
        p_adjustment_method="unknown",
    )
    manager.save_config(project, cfg)
    dry_run = _wsl_bulkseq_snakemake(
        project,
        "--dry-run", "--snakefile", "workflow/Snakefile", "--cores", "1",
        "results/deseq2/deseq2_results.csv",
    )
    dry_text = dry_run.stdout + dry_run.stderr
    assert dry_run.returncode == 0, dry_text
    for job in ("validate_project", "input_check", "ingest_deseq2_results"):
        assert job in dry_text
