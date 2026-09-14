from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "workflow" / "scripts" / "check_strandedness.py"
_HTML_REPORT = _ROOT / "workflow" / "scripts" / "make_html_report.py"

_STATUS_PREFIX = re.compile(r"^(PASS|WARNING|FAIL|REVIEW_REQUIRED):")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cs = _load("check_strandedness_messages", _SCRIPT)
mhr = _load("check_strandedness_html_report", _HTML_REPORT)


def _samples(path: Path, rows: list[tuple[str, str]], with_dataset: bool = True) -> None:
    header = "sample_id\tcondition" + ("\tdataset" if with_dataset else "")
    lines = [header]
    for sid, dataset in rows:
        lines.append(f"{sid}\tA" + (f"\t{dataset}" if with_dataset else ""))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summary(path: Path, assigned: dict[str, tuple[int, int]]) -> None:
    bams = list(assigned)
    lines = ["Status\t" + "\t".join(f"results/aligned/{b}_Aligned.sortedByCoord.out.bam" for b in bams)]
    lines.append("Assigned\t" + "\t".join(str(assigned[b][0]) for b in bams))
    lines.append("Unassigned_NoFeatures\t" + "\t".join(str(assigned[b][1]) for b in bams))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _strand_tsv(path: Path, codes: dict[str, tuple[int, float]]) -> None:
    path.write_text("".join(f"{sid}\t{c}\t{r:.4f}\n" for sid, (c, r) in codes.items()), encoding="utf-8")


def _run(tmp_path: Path, *, summary: Path | None, samples: Path | None,
         strand: Path | None) -> dict:
    out = tmp_path / f"21_{abs(hash((str(summary), str(samples), str(strand))))}.json"
    cmd = [sys.executable, str(_SCRIPT),
           "--summary", str(summary or tmp_path / "absent.summary"),
           "--samples", str(samples or tmp_path / "absent.tsv"),
           "--out", str(out)]
    if strand is not None:
        cmd += ["--strand-per-sample", str(strand)]
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_mixed_strandedness_within_a_study_leads_with_the_finding_and_names_every_sample(tmp_path):
    samples = tmp_path / "samples.tsv"
    strand = tmp_path / "strandedness_per_sample.tsv"
    _samples(samples, [("s1", "d1"), ("s2", "d1"), ("s3", "d1")])
    codes = {"s1": (0, 0.49), "s2": (2, 0.91), "s3": (0, 0.48)}
    _strand_tsv(strand, codes)
    payload = _run(tmp_path, summary=None, samples=samples, strand=strand)
    assert payload["status"] == "REVIEW_REQUIRED"
    review = [m for m in payload["messages"] if m["status"] == "REVIEW_REQUIRED"]
    assert len(review) == 1
    message = review[0]["message"]
    # Finding and action first, identifiers trailing.
    assert message.startswith("Samples within one study disagree on inferred strandedness:")
    assert message.index("check those") < message.index("Study 'd1'")
    for sid, (code, ratio) in codes.items():
        assert f"{sid}={code} (ratio={ratio:.2f})" in message


def test_agreeing_strandedness_within_a_study_is_pass(tmp_path):
    samples = tmp_path / "samples.tsv"
    strand = tmp_path / "strandedness_per_sample.tsv"
    _samples(samples, [("s1", "d1"), ("s2", "d1")])
    _strand_tsv(strand, {"s1": (0, 0.49), "s2": (0, 0.50)})
    payload = _run(tmp_path, summary=None, samples=samples, strand=strand)
    assert payload["status"] == "PASS"
    assert not any(m["status"] == "REVIEW_REQUIRED" for m in payload["messages"])


def test_divergent_per_study_assigned_fraction_leads_with_the_finding(tmp_path):
    samples = tmp_path / "samples.tsv"
    summary = tmp_path / "counts.txt.summary"
    _samples(samples, [("s1", "d1"), ("s2", "d1"), ("s3", "d2"), ("s4", "d2")])
    _summary(summary, {"s1": (800, 200), "s2": (820, 180), "s3": (50, 950), "s4": (60, 940)})
    payload = _run(tmp_path, summary=summary, samples=samples, strand=None)
    assert payload["status"] == "REVIEW_REQUIRED"
    message = [m for m in payload["messages"] if m["status"] == "REVIEW_REQUIRED"][0]["message"]
    assert message.startswith("One or more studies assign far fewer reads to features")
    assert message.index("check their GTF/genome match") < message.index("Per-study median")
    assert "d1=0.81" in message and "d2=0.06" in message


def test_consistent_per_study_assigned_fraction_is_pass(tmp_path):
    samples = tmp_path / "samples.tsv"
    summary = tmp_path / "counts.txt.summary"
    _samples(samples, [("s1", "d1"), ("s2", "d1"), ("s3", "d2"), ("s4", "d2")])
    _summary(summary, {"s1": (800, 200), "s2": (820, 180), "s3": (790, 210), "s4": (810, 190)})
    payload = _run(tmp_path, summary=summary, samples=samples, strand=None)
    assert payload["status"] == "PASS"


def _every_check21_message(tmp_path: Path) -> list[dict[str, str]]:
    samples = tmp_path / "samples.tsv"
    single = tmp_path / "single.tsv"
    summary_div = tmp_path / "div.summary"
    summary_ok = tmp_path / "ok.summary"
    strand_mixed = tmp_path / "mixed.tsv"
    strand_same = tmp_path / "same.tsv"
    _samples(samples, [("s1", "d1"), ("s2", "d1"), ("s3", "d2"), ("s4", "d2")])
    _samples(single, [("s1", "d1"), ("s2", "d1")])
    _summary(summary_div, {"s1": (800, 200), "s2": (820, 180), "s3": (50, 950), "s4": (60, 940)})
    _summary(summary_ok, {"s1": (800, 200), "s2": (820, 180), "s3": (790, 210), "s4": (810, 190)})
    _strand_tsv(strand_mixed, {"s1": (0, 0.49), "s2": (2, 0.93), "s3": (0, 0.48), "s4": (0, 0.51)})
    _strand_tsv(strand_same, {"s1": (0, 0.49), "s2": (0, 0.50)})
    payloads = [
        _run(tmp_path, summary=summary_div, samples=samples, strand=strand_mixed),
        _run(tmp_path, summary=summary_ok, samples=samples, strand=None),
        _run(tmp_path, summary=None, samples=single, strand=strand_same),
        _run(tmp_path, summary=summary_ok, samples=None, strand=None),
    ]
    return [m for p in payloads for m in p["messages"]]


def test_check21_messages_survive_the_sanity_parser(tmp_path):
    messages = _every_check21_message(tmp_path)
    assert {m["status"] for m in messages} == {"PASS", "REVIEW_REQUIRED"}
    assert sum(m["status"] == "REVIEW_REQUIRED" for m in messages) == 2
    for m in messages:
        # aggregate_sanity_checks.py writes one line per message: "  - {status}: {message}".
        assert "\n" not in m["message"] and "\r" not in m["message"]
        assert not _STATUS_PREFIX.match(m["message"])
        text = "\n".join([
            "BulkSeq Studio validation checks", "=" * 31, "Overall: REVIEW_REQUIRED", "",
            f"21_strandedness_qc: {m['status']}",
            f"  - {m['status']}: {m['message']}", "",
        ])
        overall, checks = mhr._parse_sanity(text)
        assert overall == "REVIEW_REQUIRED"
        assert len(checks) == 1 and checks[0]["name"] == "21_strandedness_qc"
        assert checks[0]["status"] == m["status"]
        assert checks[0]["messages"] == [m["message"]]
