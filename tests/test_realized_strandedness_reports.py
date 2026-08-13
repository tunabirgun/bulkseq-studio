from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


_ROOT = Path(__file__).resolve().parents[1]
_RUN_SUMMARY = _ROOT / "workflow" / "scripts" / "make_run_summary.py"
_HTML_REPORT = _ROOT / "workflow" / "scripts" / "make_html_report.py"
_REPORT_RULES = _ROOT / "workflow" / "rules" / "reports.smk"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mrs():
    return _load_module("realized_strandedness_run_summary", _RUN_SUMMARY)


@pytest.fixture(scope="module")
def mhr():
    return _load_module("realized_strandedness_html_report", _HTML_REPORT)


def _config(*, input_type: str = "fastq", aligner: str = "STAR",
            quantifier: str = "featureCounts", configured: int = 0) -> dict:
    return {
        "project": {"name": "strand-test", "working_directory": "."},
        "input": {"type": input_type, "samples": "config/samples.tsv"},
        "reference": {},
        "workflow": {
            "aligner": aligner, "quantifier": quantifier, "de_engine": "DESeq2",
        },
        "featurecounts": {
            "feature_type": "exon", "attribute_type": "gene_id",
            "strandedness": configured,
        },
        "deseq2": {
            "design_formula": "~ condition",
            "reference_level": {"condition": "control"},
            "contrasts": [{
                "name": "treated_vs_control", "factor": "condition",
                "numerator": "treated", "denominator": "control",
            }],
            "alpha": 0.05, "lfc_threshold": 1.0,
            "lfc_shrinkage": True, "shrinkage_method": "apeglm",
        },
    }


def _write_realized_inputs(project: Path, *, strand: str = "2", header_strand: str = "2",
                           header: str | None = None) -> None:
    strand_path = project / "results" / "aligned" / "strandedness.txt"
    strand_path.parent.mkdir(parents=True, exist_ok=True)
    strand_path.write_text(strand, encoding="utf-8")
    counts_path = project / "results" / "counts" / "counts.txt"
    counts_path.parent.mkdir(parents=True, exist_ok=True)
    if header is None:
        header = (
            '# Program:featureCounts v2.1.1; Command:"featureCounts" "-a" "genes.gtf" '
            f'"-s" "{header_strand}" "sample.bam"'
        )
    counts_path.write_text(
        header + "\nGeneid\tChr\tStart\tEnd\tStrand\tLength\tsample\n",
        encoding="utf-8",
    )


def _patch_report_probes(monkeypatch: pytest.MonkeyPatch, mrs) -> None:
    monkeypatch.setattr(mrs, "run_version", lambda command: "test")
    monkeypatch.setattr(mrs, "r_package_versions", lambda extra=None: {})
    monkeypatch.setattr(mrs, "workflow_git_commit", lambda: None)
    monkeypatch.setattr(mrs, "env_lock_md5", lambda: None)


def test_realized_strandedness_is_verified_and_rendered_in_every_report(
        mrs, mhr, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(configured=0)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (config_dir / "samples.tsv").write_text(
        "sample_id\tcondition\ns1\tcontrol\ns2\ttreated\n", encoding="utf-8")
    _write_realized_inputs(tmp_path, strand="2\n", header_strand="2")
    session = tmp_path / "results" / "reports" / "sessionInfo.txt"
    session.parent.mkdir(parents=True)
    session.write_text(
        "Shrinkage method used: ashr (requested 'apeglm' failed and fell back: test)\n",
        encoding="utf-8",
    )
    _patch_report_probes(monkeypatch, mrs)
    monkeypatch.setattr(sys, "argv", [str(_RUN_SUMMARY), "--project", str(tmp_path)])

    assert mrs.main() == 0

    reports = tmp_path / "results" / "reports"
    payload = json.loads((reports / "run_summary.json").read_text(encoding="utf-8"))
    assert payload["strandedness"] == {
        "configured": {"code": 0},
        "realized": {
            "code": 2, "label": "reverse",
            "path": "results/aligned/strandedness.txt",
        },
        "featurecounts_header": {
            "code": 2, "path": "results/counts/counts.txt",
        },
    }
    assert "strandedness" not in payload["featurecounts"]
    assert payload["effect_size_semantics"] == {
        "configured_absolute_log2fc_cutoff": 1.0,
        "threshold_estimate": (
            "raw, unshrunken DESeq2 maximum-likelihood log2FoldChange"
        ),
        "thresholded_outputs": [
            "up/down differential-expression gene sets",
            "enrichment input sets derived from those up/down sets",
        ],
        "shrinkage": {
            "realized_method": (
                "ashr (requested 'apeglm' failed and fell back: test)"
            ),
            "role": (
                "stabilized effect display/ranking where used; not up/down or enrichment-set "
                "cutoff classification"
            ),
        },
    }
    expected = "Realized strandedness: reverse (2; realized from results/aligned/strandedness.txt)"
    for filename in ("run_summary.txt", "tools_references.txt", "study_design.txt"):
        rendered = (reports / filename).read_text(encoding="utf-8")
        assert expected in rendered
        assert (
            "Configured effect cutoff: absolute raw, unshrunken DESeq2 "
            "maximum-likelihood log2FoldChange >= 1.0"
        ) in rendered
        assert "Realized LFC shrinkage: ashr (requested 'apeglm' failed" in rendered
        assert "not up/down or enrichment-set cutoff classification" in rendered
    html = mhr._study_design_section(payload)
    assert "Realized strandedness" in html
    assert "reverse (2; realized from results/aligned/strandedness.txt)" in html
    assert "Configured effect cutoff" in html
    assert "raw, unshrunken DESeq2 maximum-likelihood log2FoldChange" in html
    assert "Realized LFC shrinkage" in html
    assert "not up/down or enrichment-set cutoff classification" in html
    cards = mhr._meta_cards(payload, tmp_path)
    assert "Configured effect cutoff" in cards
    assert "raw, unshrunken DESeq2 maximum-likelihood log2FoldChange" in cards
    assert "Realized LFC shrinkage" in cards


@pytest.mark.parametrize("bad_value", ["", "2 1", "3", "reverse", "\ufeff2"])
def test_realized_strandedness_file_rejects_malformed_or_out_of_range_values(
        mrs, tmp_path: Path, bad_value: str) -> None:
    _write_realized_inputs(tmp_path, strand=bad_value, header_strand="2")
    with pytest.raises(ValueError, match="realized strandedness file"):
        mrs.load_realized_strandedness(tmp_path, _config())


def test_realized_strandedness_file_is_required(mrs, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing or unreadable"):
        mrs.load_realized_strandedness(tmp_path, _config())


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("Geneid\tChr", "lacks its program header"),
        ('# Program:featureCounts v2; Command:"featureCounts" "sample.bam"',
         "exactly one -s value"),
        ('# Program:featureCounts v2; Command:"featureCounts" "-s" "1" "-s" "2"',
         "exactly one -s value"),
        ('# Program:featureCounts v2; Command:"featureCounts" "-s" "3"',
         "must be 0, 1, or 2"),
    ],
)
def test_featurecounts_header_gate_rejects_missing_duplicate_or_invalid_strand(
        mrs, tmp_path: Path, header: str, message: str) -> None:
    _write_realized_inputs(tmp_path, strand="2", header=header)
    with pytest.raises(ValueError, match=message):
        mrs.load_realized_strandedness(tmp_path, _config())


def test_featurecounts_header_must_match_realized_sidecar(mrs, tmp_path: Path) -> None:
    _write_realized_inputs(tmp_path, strand="2", header_strand="1")
    with pytest.raises(ValueError, match="mismatch"):
        mrs.load_realized_strandedness(tmp_path, _config())


@pytest.mark.parametrize(
    ("code", "label"), [("0", "unstranded"), ("1", "forward"), ("2", "reverse")],
)
def test_star_genecounts_records_each_realized_label_without_featurecounts_header(
        mrs, tmp_path: Path, code: str, label: str) -> None:
    strand_path = tmp_path / "results" / "aligned" / "strandedness.txt"
    strand_path.parent.mkdir(parents=True)
    strand_path.write_text(code + "\n", encoding="utf-8")

    provenance = mrs.load_realized_strandedness(
        tmp_path, _config(quantifier="STAR_GeneCounts"))

    assert provenance["realized"] == {
        "code": int(code), "label": label,
        "path": "results/aligned/strandedness.txt",
    }
    assert "featurecounts_header" not in provenance


def test_provenance_failure_happens_before_any_report_write(
        mrs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(_config()), encoding="utf-8")
    _write_realized_inputs(tmp_path, strand="2 1", header_strand="2")
    reports = tmp_path / "results" / "reports"
    reports.mkdir(parents=True)
    sentinel = reports / "run_summary.json"
    sentinel.write_text("previous-run-sentinel", encoding="utf-8")
    _patch_report_probes(monkeypatch, mrs)
    monkeypatch.setattr(sys, "argv", [str(_RUN_SUMMARY), "--project", str(tmp_path)])

    with pytest.raises(ValueError, match="exactly one token"):
        mrs.main()

    assert sentinel.read_text(encoding="utf-8") == "previous-run-sentinel"
    assert not (reports / "run_summary.txt").exists()
    assert not (reports / "software_versions.txt").exists()


@pytest.mark.parametrize(
    ("input_type", "aligner"),
    [
        ("count_matrix", "STAR"),
        ("microarray", "STAR"),
        ("deseq2_results", "STAR"),
        ("fastq", "SALMON"),
        ("sra", "salmon"),
        ("mixed", "Salmon"),
    ],
)
def test_inactive_routes_omit_realized_strandedness_without_reading_files(
        mrs, tmp_path: Path, input_type: str, aligner: str) -> None:
    assert mrs.load_realized_strandedness(
        tmp_path, _config(input_type=input_type, aligner=aligner)) is None


def test_renderers_never_fall_back_to_configured_strandedness(mrs, mhr) -> None:
    payload = {
        **_config(),
        "run_date": "2026-08-11T00:00:00",
        "app_version": "0.28.0", "workflow_version": "0.28.0",
        "workflow_git_commit": None, "environment_lock_md5": None,
        "snakemake_version": "test", "customized_parameters": {},
        "warnings": [], "output_paths": [], "software_versions": {},
        "r_packages": {}, "session_info": {}, "platform": {},
    }
    payload["featurecounts"]["strandedness"] = 1
    for rendered in (
        mrs.render_text(payload),
        mrs.render_tools_references(payload),
        mrs.render_study_design(payload, "sample_id\tcondition\n"),
        mhr._study_design_section(payload),
    ):
        assert "Realized strandedness" not in rendered
        assert "forward (1" not in rendered


def _assert_report_rule_contract(source: str) -> None:
    assert '_REPORT_REALIZED_STRANDEDNESS = (' in source
    assert '{"fastq", "sra", "mixed"}' in source
    assert "and not USE_SALMON" in source
    assert ('**({"strandedness": "results/aligned/strandedness.txt"} '
            'if _REPORT_REALIZED_STRANDEDNESS else {}),') in source


def test_final_reports_realized_strandedness_dependency_and_negative_control() -> None:
    source = _REPORT_RULES.read_text(encoding="utf-8")
    _assert_report_rule_contract(source)

    broken = source.replace(
        '**({"strandedness": "results/aligned/strandedness.txt"} '
        'if _REPORT_REALIZED_STRANDEDNESS else {}),\n',
        "",
        1,
    )
    assert broken != source
    with pytest.raises(AssertionError):
        _assert_report_rule_contract(broken)
