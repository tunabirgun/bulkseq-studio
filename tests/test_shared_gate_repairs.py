from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from app.core import readiness
from app.core.metadata import validate_metadata
from app.core.config_models import (
    Deseq2ResultsDirectionProvenance, Deseq2ResultsFileProvenance, default_config,
)
from app.core.de_results import provenance_payload, validate_de_results_table
from app.core.preflight_checks import input_validation_messages
from app.core.sanity_checks import write_check
from app.core.snakemake_runner import native_path_prefix
from workflow.scripts.check_contract import read_check, summarize_checks
from workflow.scripts.meta_readiness import assess_meta_readiness
from _runtime import rscript_runtime


ROOT = Path(__file__).resolve().parents[1]


def samples():
    return pd.DataFrame([
        {"dataset": study, "condition": "constant", "treatment": arm}
        for study in ("S1", "S2") for arm in ("A", "B") for _ in range(2)
    ])


def assess(df=None, **changes):
    settings = dict(enabled=True, input_type="count_matrix", contrast_factor="treatment",
                    numerator="A", denominator="B", design_formula="~ dataset + treatment")
    settings.update(changes)
    return assess_meta_readiness(samples() if df is None else df, **settings)


def _meta_runtime():
    runtime = rscript_runtime("DESeq2", "metaRNASeq", "metafor", "HTSFilter")
    if runtime is None:
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R_META") else pytest.skip)(
            "Pinned R meta-analysis runtime unavailable")
    return runtime


def test_meta_readiness_selected_factor_and_supported_design():
    ok = assess()
    assert ok["runnable"] and ok["eligible_studies"] == ["S1", "S2"]
    assert assess(design_formula="~ 1 + treatment")["runnable"]
    assert assess(design_formula="~ treatment + 1")["runnable"]
    assert assess(design_formula="~ treatment + dataset")["runnable"]
    assert not assess(design_formula="~ condition")["runnable"]
    assert not assess(design_formula="~ treatment + batch")["runnable"]
    assert not assess(design_formula="~ treatment:dataset")["runnable"]
    assert not assess(design_formula="~ 0 + treatment")["runnable"]
    assert not assess(numerator="C")["runnable"]
    assert not assess(samples().assign(dataset="S1"))["runnable"]
    assert not assess(samples().assign(dataset=""))["runnable"]
    assert not assess(samples().drop(columns="dataset"))["runnable"]
    assert not assess(samples().assign(treatment=""))["runnable"]
    partial = pd.concat([samples(), pd.DataFrame([{"dataset": "S3", "treatment": "A"}])],
                        ignore_index=True)
    assert assess(partial)["excluded_studies"] == ["S3"]
    assert not assess(input_type="microarray")["runnable"]
    assert assess(enabled=False, design_formula="~ treatment + batch")["status"] == "PASS"


def test_raw_factor_whitespace_matches_production_r_subset():
    assert assess()["runnable"]
    assert assess(samples().assign(treatment=lambda d: " " + d["treatment"]))["status"] == "FAIL"
    assert assess(samples().assign(treatment=lambda d: d["treatment"] + " "))["status"] == "FAIL"
    assert assess(numerator=" A")["status"] == "FAIL"
    assert assess(denominator="B ")["status"] == "FAIL"
    assert assess(samples().assign(treatment=" A "), enabled=False)["status"] == "PASS"
    default = samples().assign(condition=lambda d: d["treatment"])
    assert assess(default, contrast_factor="condition", design_formula="~ condition")["runnable"]
    assert not assess(default.assign(condition=lambda d: " " + d["condition"]),
                      contrast_factor="condition", design_formula="~ condition")["runnable"]
    command, convert = _meta_runtime()
    probe = subprocess.run([*command, convert(ROOT / "tests/meta_factor_subset_probe.R")],
                           cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "META_FACTOR_SUBSET_PASS" in probe.stdout


def test_saved_metadata_and_direct_workflow_use_selected_factor(tmp_path):
    df = samples().assign(sample_id=[f"s{i}" for i in range(8)], layout="single", fastq_1="pending")
    app_messages = validate_metadata(df, allow_pending_sra=True, contrast=("A", "B"),
                                     contrast_factor="treatment", meta_enabled=True,
                                     input_type="count_matrix", design_formula="~ treatment")
    assert not any(m["status"] == "FAIL" for m in app_messages)
    sheet = tmp_path / "samples.tsv"
    df.to_csv(sheet, sep="\t", index=False)
    out = tmp_path / "01_input_validation.json"
    cmd = [sys.executable, str(ROOT / "workflow/scripts/validate_metadata.py"),
           "--samples", str(sheet), "--out", str(out), "--numerator", "A",
           "--denominator", "B", "--contrast-factor", "treatment",
           "--design-formula", "~ treatment", "--input-type", "count_matrix", "--meta-analysis"]
    assert subprocess.run(cmd, cwd=tmp_path, capture_output=True).returncode == 0
    assert read_check(out)["status"] != "FAIL"
    df.loc[df["dataset"] == "S2", "treatment"] = "A"
    df.to_csv(sheet, sep="\t", index=False)
    assert subprocess.run(cmd, cwd=tmp_path, capture_output=True).returncode == 0
    assert read_check(out)["status"] == "FAIL"
    assert subprocess.run([sys.executable, str(ROOT / "workflow/scripts/require_check.py"),
                           "--check", str(out), "--out", str(tmp_path / "gate.ok")],
                          cwd=tmp_path, capture_output=True).returncode != 0
    df = samples().assign(sample_id=[f"s{i}" for i in range(8)], layout="single", fastq_1="pending")
    df.loc[df.index[0], "treatment"] = " A"
    df.to_csv(sheet, sep="\t", index=False)
    assert subprocess.run(cmd, cwd=tmp_path, capture_output=True).returncode == 0
    assert read_check(out)["status"] == "FAIL"
    assert any("whitespace" in m["message"] for m in read_check(out)["messages"])
    assert subprocess.run([sys.executable, str(ROOT / "workflow/scripts/require_check.py"),
                           "--check", str(out), "--out", str(tmp_path / "gate.ok")],
                          cwd=tmp_path, capture_output=True).returncode != 0
    app_messages = validate_metadata(df, allow_pending_sra=True, contrast=("A", "B"),
                                     contrast_factor="treatment", meta_enabled=True,
                                     input_type="count_matrix", design_formula="~ treatment")
    assert any("whitespace" in m["message"] for m in app_messages)
    clean = df.assign(treatment=lambda d: d["treatment"].str.strip())
    assert any("contrast labels" in m["message"] for m in validate_metadata(
        clean, allow_pending_sra=True, contrast=("A", "B"), contrast_factor="treatment",
        meta_enabled=True, input_type="count_matrix", design_formula="~ treatment",
        meta_contrast_labels=(" A", "B")))
    cfg = default_config("synthetic", tmp_path)
    cfg.input.type = "count_matrix"
    cfg.workflow.meta_analysis = True
    cfg.deseq2.design_formula = "~ treatment"
    cfg.deseq2.contrasts[0].factor = "treatment"
    cfg.deseq2.contrasts[0].numerator = " A"
    cfg.deseq2.contrasts[0].denominator = "B"
    assert any("contrast labels" in m["message"] for m in
               input_validation_messages(cfg, tmp_path, clean))


def test_valid_imported_results_refuse_requested_meta_but_allow_meta_off(tmp_path):
    copy = tmp_path / "config" / "imported.csv"
    copy.parent.mkdir()
    copy.write_text("gene_id,log2FoldChange,padj\ng1,1.5,0.01\n", encoding="utf-8")
    validated = validate_de_results_table(copy)
    cfg = default_config("synthetic", tmp_path)
    cfg.input.type = "deseq2_results"
    cfg.input.deseq2_results = "config/imported.csv"
    cfg.input.deseq2_results_direction = Deseq2ResultsDirectionProvenance(
        numerator="case", denominator="control", confirmed=True,
        confirmed_at="2026-08-10T12:00:00+03:00",
    )
    cfg.input.deseq2_results_provenance = Deseq2ResultsFileProvenance.model_validate(
        provenance_payload(validated, original_basename="source.csv",
                           imported_at="2026-08-10T12:00:00+03:00",
                           project_copy="config/imported.csv"))
    cfg.workflow.enrichment = False
    cfg.ppi.enabled = False
    sheet = pd.DataFrame(columns=["sample_id"])
    baseline = input_validation_messages(cfg, tmp_path, sheet)
    assert baseline and all(m["status"] == "PASS" for m in baseline)
    cfg.workflow.meta_analysis = True
    requested = input_validation_messages(cfg, tmp_path, sheet)
    assert [m for m in requested if m["status"] == "PASS"] == baseline
    refusal = [m for m in requested if m["status"] == "FAIL"]
    assert len(refusal) == 1
    assert "unavailable for deseq2_results input" in refusal[0]["message"]
    check = write_check(tmp_path, "01_input_validation", requested)
    assert read_check(check)["status"] == "FAIL"
    gate = tmp_path / "checks" / "meta_input_gate.ok"
    result = subprocess.run([sys.executable, str(ROOT / "workflow/scripts/require_check.py"),
                             "--check", str(check), "--out", str(gate)],
                            cwd=tmp_path, capture_output=True)
    assert result.returncode != 0 and not gate.exists()


def test_check_contract_preserves_child_failure_and_missing_evidence(tmp_path):
    valid = tmp_path / "01_input_validation.json"
    valid.write_text(json.dumps({"check": valid.stem, "status": "PASS",
                                 "messages": [{"status": "FAIL", "message": "bad input"}],
                                 "coverage": {"status": "partial"}}), encoding="utf-8")
    check = read_check(valid)
    assert check["status"] == "FAIL" and check["coverage"]["status"] == "partial"
    absent = tmp_path / "02_missing.json"
    summary = summarize_checks([valid, absent])
    assert summary["status"] == "FAIL" and not summary["complete"]
    for payload in ([], {"check": valid.stem, "status": "SURPRISE", "messages": []},
                    {"check": valid.stem, "status": {"bad": 1}, "messages": []},
                    {"check": valid.stem, "status": "PASS", "messages": ["bad"]}):
        valid.write_text(json.dumps(payload), encoding="utf-8")
        assert read_check(valid)["status"] == "FAIL"


def test_strict_summary_and_meta_input_gate_fail_on_injected_defect(tmp_path):
    check = tmp_path / "01_input_validation.json"
    out = tmp_path / "summary.txt"
    gate = tmp_path / "gate.ok"
    check.write_text(json.dumps({"check": check.stem, "status": "PASS",
                                 "messages": [{"status": "PASS", "message": "ok"}]}), encoding="utf-8")
    summary_cmd = [sys.executable, str(ROOT / "workflow/scripts/aggregate_sanity_checks.py"),
                   "--checks", str(check), "--out", str(out), "--strict"]
    gate_cmd = [sys.executable, str(ROOT / "workflow/scripts/require_check.py"),
                "--check", str(check), "--out", str(gate)]
    assert subprocess.run(summary_cmd, cwd=tmp_path, capture_output=True).returncode == 0
    assert subprocess.run(gate_cmd, cwd=tmp_path, capture_output=True).returncode == 0
    assert gate.exists()
    check.write_text(json.dumps({"check": check.stem, "status": "PASS",
                                 "messages": [{"status": "FAIL", "message": "injected defect"}]}), encoding="utf-8")
    assert subprocess.run(summary_cmd, cwd=tmp_path, capture_output=True).returncode != 0
    assert subprocess.run(gate_cmd, cwd=tmp_path, capture_output=True).returncode != 0
    assert not gate.exists()
    assert "Overall: FAIL" in out.read_text(encoding="utf-8")
    for payload in (
        {"check": check.stem, "status": "REVIEW_REQUIRED",
         "messages": [{"status": "REVIEW_REQUIRED", "message": "review"}]},
        {"check": check.stem, "status": "UNKNOWN",
         "messages": [{"status": "PASS", "message": "ok"}]},
        ["malformed"],
    ):
        check.write_text(json.dumps({"check": check.stem, "status": "PASS",
                                     "messages": [{"status": "PASS", "message": "ok"}]}), encoding="utf-8")
        assert subprocess.run(gate_cmd, cwd=tmp_path, capture_output=True).returncode == 0
        assert gate.exists()
        check.write_text(json.dumps(payload), encoding="utf-8")
        assert subprocess.run(summary_cmd, cwd=tmp_path, capture_output=True).returncode != 0
        assert subprocess.run(gate_cmd, cwd=tmp_path, capture_output=True).returncode != 0
        assert not gate.exists()
        expected = "REVIEW_REQUIRED" if isinstance(payload, dict) and payload.get("status") == "REVIEW_REQUIRED" else "FAIL"
        assert f"Overall: {expected}" in out.read_text(encoding="utf-8")
    check.write_text(json.dumps({"check": check.stem, "status": "PASS",
                                 "messages": [{"status": "PASS", "message": "ok"}]}), encoding="utf-8")
    assert subprocess.run(gate_cmd, cwd=tmp_path, capture_output=True).returncode == 0
    assert gate.exists()
    check.unlink()
    assert subprocess.run(summary_cmd, cwd=tmp_path, capture_output=True).returncode != 0
    assert subprocess.run(gate_cmd, cwd=tmp_path, capture_output=True).returncode != 0
    assert not gate.exists()
    assert "01_input_validation: FAIL" in out.read_text(encoding="utf-8")


def test_duplicate_screen_reports_unassessed_coverage(tmp_path):
    sheet = tmp_path / "samples.tsv"
    samples().to_csv(sheet, sep="\t", index=False)
    out = tmp_path / "20_duplicate_study_qc.json"
    cmd = [sys.executable, str(ROOT / "workflow/scripts/check_duplicate_studies.py"),
           "--samples", str(sheet), "--meta-dir", str(tmp_path / "missing"), "--out", str(out)]
    assert subprocess.run(cmd, cwd=tmp_path, capture_output=True).returncode == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "REVIEW_REQUIRED"
    assert payload["coverage"]["status"] == "unassessed"
    complete = samples().assign(read_count=[str(100 + i) for i in range(8)],
                                base_count=[str(1000 + i) for i in range(8)])
    complete.to_csv(sheet, sep="\t", index=False)
    assert subprocess.run(cmd, cwd=tmp_path, capture_output=True).returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["coverage"]["status"] == "partial"
    meta_dir = tmp_path / "missing"
    meta_dir.mkdir()
    for study in ("S1", "S2"):
        pd.DataFrame({"gene_id": [f"g{i}" for i in range(10)],
                      "log2FoldChange": range(10)}).to_csv(meta_dir / f"per_study_{study}.csv", index=False)
    assert subprocess.run(cmd, cwd=tmp_path, capture_output=True).returncode == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["coverage"]["status"] == "assessed"
    assert payload["status"] == "REVIEW_REQUIRED"


def test_duplicate_screen_refreshes_unavailable_and_undefined_evidence(tmp_path):
    sheet = tmp_path / "samples.tsv"
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    out = tmp_path / "20_duplicate_study_qc.json"
    cmd = [sys.executable, str(ROOT / "workflow/scripts/check_duplicate_studies.py"),
           "--samples", str(sheet), "--meta-dir", str(meta_dir), "--out", str(out)]

    def run():
        result = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = read_check(out)
        assert payload["status"] == "REVIEW_REQUIRED"
        return payload["coverage"]

    out.write_text(json.dumps({"check": out.stem, "status": "PASS",
                               "messages": [{"status": "PASS", "message": "stale"}]}), encoding="utf-8")
    sheet.write_bytes(b"")
    coverage = run()
    assert coverage["status"] == "unassessed"
    assert any("unreadable" in reason for reason in coverage["unavailable_reasons"])
    assert "stale" not in out.read_text(encoding="utf-8")
    for bad in (b'"unterminated\n', b"\xff"):
        sheet.write_bytes(bad)
        assert run()["status"] == "unassessed"

    complete = samples().assign(read_count=[str(100 + i) for i in range(8)],
                                base_count=[str(1000 + i) for i in range(8)])
    complete.to_csv(sheet, sep="\t", index=False)
    left = meta_dir / "per_study_S1.csv"
    right = meta_dir / "per_study_S2.csv"
    genes = [f"g{i}" for i in range(10)]

    def write_vector(path, values, ids=genes):
        pd.DataFrame({"gene_id": ids, "log2FoldChange": values}).to_csv(path, index=False)

    write_vector(left, range(10))
    for bad in (b"", b'"unterminated\n', b"\xff"):
        right.write_bytes(bad)
        coverage = run()
        assert coverage["status"] == "partial" and coverage["de_pairs"] == []
        assert any("unreadable" in reason for reason in coverage["unavailable_reasons"])
    for values in ([float("inf")] * 10, [*range(9), float("-inf")], [2] * 10):
        write_vector(right, values)
        coverage = run()
        assert coverage["status"] == "partial" and coverage["de_pairs"] == []
        assert coverage["unavailable_reasons"]
    write_vector(right, range(10), ids=[f"other{i}" for i in range(9)] + ["g9"])
    coverage = run()
    assert coverage["status"] == "partial" and coverage["de_pairs"] == []
    assert any("fewer than 10" in reason for reason in coverage["unavailable_reasons"])
    write_vector(right, range(10))
    coverage = run()
    assert coverage["status"] == "assessed" and coverage["de_pairs"] == [["S1", "S2"]]
    strict = subprocess.run([sys.executable, str(ROOT / "workflow/scripts/require_check.py"),
                             "--check", str(out), "--out", str(tmp_path / "review.ok")],
                            cwd=tmp_path, capture_output=True)
    assert strict.returncode != 0


def test_native_environment_resolver_and_bounded_wsl_probe(monkeypatch, tmp_path):
    env_bin = tmp_path / "envs" / "bulkseq" / "bin"
    env_bin.mkdir(parents=True)
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path))
    assert native_path_prefix() == [str(env_bin)]
    path_seen = {}

    def resolve(command, path=None):
        path_seen.update(command=command, path=path)
        return "/resolved/snakemake"

    monkeypatch.setattr(readiness.shutil, "which", resolve)
    assert readiness.native_tool_path("snakemake") == "/resolved/snakemake"
    assert path_seen["path"].split(";" if sys.platform.startswith("win") else ":")[0] == str(env_bin)
    monkeypatch.setattr(readiness.sys, "platform", "win32")
    monkeypatch.setattr(readiness.shutil, "which", lambda *args, **kwargs: "wsl.exe")
    observed = {}

    def dead(*, timeout, distro):
        observed.update(timeout=timeout, distro=distro)
        return False

    monkeypatch.setattr(readiness, "wsl_has_working_distro", dead)
    assert readiness.local_wsl_health(timeout=3, distro="TestDistro")["status"] == "REVIEW_REQUIRED"
    assert observed == {"timeout": 3, "distro": "TestDistro"}
