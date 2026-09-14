"""Positive/negative control for per-sample strandedness: flipping one sample's inferred
code must move the counts and the DESeq2 result, and restoring it must bring both back to
the pinned pasilla evidence hashes.

Needs a completed STAR + featureCounts project (BULKSEQ_RECOUNT_PROJECT) and the
bioinformatics environment on PATH; skips with a visible reason otherwise.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# pasilla evidence project (DESeq2, STAR + featureCounts), 7,532 rows, padj<0.05 = 467.
PINNED_COUNTS_BODY_SHA256 = "f68d2786a6ace38d99700b66ece964ed1799d3df26faf5f987093edbd3ca4dfc"
PINNED_DESEQ2_SHA256 = "2b040052ab25ada544f2acc2bb6f91e9a84eb149f2295dd2f3378c654eb23dc6"
FLIPPED_CODE = "2"

STRAND_TABLE = Path("results/aligned/strandedness_per_sample.tsv")
COUNTS = Path("results/counts/counts.txt")
DESEQ2 = Path("results/deseq2/deseq2_results.csv")
CHECK_21 = Path("checks/21_strandedness_qc.json")

REGENERATED_CHECKS = [
    "checks/07_quantification_qc.json",
    "checks/09_deseq2_qc.json",
    "checks/19_orientation_qc.json",
    "checks/21_strandedness_qc.json",
    "checks/22_sample_structure_qc.json",
    "checks/23_covariate_structure_qc.json",
]
TARGETS = [str(COUNTS), str(DESEQ2)] + REGENERATED_CHECKS
REQUIRED_TOOLS = ("snakemake", "featureCounts", "Rscript")


def _skip_reason() -> str | None:
    raw = os.environ.get("BULKSEQ_RECOUNT_PROJECT", "")
    if not raw:
        return ("BULKSEQ_RECOUNT_PROJECT is not set: point it at a completed STAR + "
                "featureCounts project to run the strandedness re-count control")
    project = Path(raw)
    for rel in (STRAND_TABLE, COUNTS, DESEQ2):
        if not (project / rel).exists():
            return f"BULKSEQ_RECOUNT_PROJECT={project} has no {rel}; the run must be complete"
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        return f"not on PATH: {', '.join(missing)}; prefix PATH with the bulkseq environment"
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")


def _counts_body_sha256(path: Path) -> str:
    # Same body as `tail -n +3 counts.txt | cut -f1,7-`: drop the provenance comment and the
    # header, then keep the gene id and the per-sample counts, not the coordinate columns.
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for index, raw in enumerate(handle):
            if index < 2:
                continue
            fields = raw.rstrip(b"\n").split(b"\t")
            digest.update(b"\t".join([fields[0]] + fields[6:]) + b"\n")
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snakemake(project: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, LC_NUMERIC="C")
    # --rerun-incomplete closes the nargs="+" of --configfile, so targets are parsed as targets.
    cmd = ["snakemake", "--snakefile", "workflow/Snakefile", "--cores", "8",
           "--resources", "mem_mb=24000", "downloads=3", "--configfile", "config/config.yaml",
           "--rerun-incomplete", *args]
    return subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True)


def _write_strand_table(project: Path, rows: list[tuple[str, str, str]]) -> None:
    (project / STRAND_TABLE).write_text(
        "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")


def _read_strand_table(project: Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in (project / STRAND_TABLE).read_text(encoding="utf-8").splitlines():
        if line.strip():
            parts = line.split("\t")
            rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return rows


def _recount(project: Path) -> None:
    shutil.rmtree(project / "results" / "counts", ignore_errors=True)
    shutil.rmtree(project / "results" / "deseq2", ignore_errors=True)
    for rel in REGENERATED_CHECKS:
        (project / rel).unlink(missing_ok=True)
    done = _snakemake(project, *TARGETS)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    for rel in TARGETS:
        assert (project / rel).exists(), rel


def _check21(project: Path) -> dict:
    return json.loads((project / CHECK_21).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    source = Path(os.environ["BULKSEQ_RECOUNT_PROJECT"])
    scratch = os.environ.get("BULKSEQ_RECOUNT_SCRATCH")
    target = Path(scratch) if scratch else tmp_path_factory.mktemp("recount") / source.name
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    assert subprocess.run(["cp", "-a", str(source), str(target)]).returncode == 0
    return target


def test_the_per_sample_table_is_a_real_rerun_trigger(project: Path) -> None:
    # Negative control first: an untouched table older than counts.txt schedules nothing, so
    # the positive case below is the edit doing the work and not a deleted output.
    uniform = _read_strand_table(project)
    os.utime(project / STRAND_TABLE, (0, (project / COUNTS).stat().st_mtime - 3600))
    idle = _snakemake(project, "-n", str(COUNTS))
    assert idle.returncode == 0, idle.stderr[-2000:]
    assert "Nothing to be done" in idle.stdout

    flipped = [(sid, FLIPPED_CODE if i == len(uniform) - 1 else code, ratio)
               for i, (sid, code, ratio) in enumerate(uniform)]
    _write_strand_table(project, flipped)
    triggered = _snakemake(project, "-n", str(COUNTS))
    assert triggered.returncode == 0, triggered.stderr[-2000:]
    assert "featurecounts" in triggered.stdout
    assert str(STRAND_TABLE) in triggered.stdout
    _write_strand_table(project, uniform)


def test_flipping_one_sample_moves_the_counts_and_the_de_result_and_restoring_returns_them(
        project: Path) -> None:
    uniform = _read_strand_table(project)
    assert len({code for _, code, _ in uniform}) == 1, uniform
    flipped_id, _, flipped_ratio = uniform[-1]

    _recount(project)
    assert _counts_body_sha256(project / COUNTS) == PINNED_COUNTS_BODY_SHA256
    assert _sha256(project / DESEQ2) == PINNED_DESEQ2_SHA256
    assert _check21(project)["status"] == "PASS"

    _write_strand_table(project, [(sid, FLIPPED_CODE if sid == flipped_id else code, ratio)
                                  for sid, code, ratio in uniform])
    _recount(project)
    assert _counts_body_sha256(project / COUNTS) != PINNED_COUNTS_BODY_SHA256
    assert _sha256(project / DESEQ2) != PINNED_DESEQ2_SHA256
    payload = _check21(project)
    assert payload["status"] == "REVIEW_REQUIRED"
    review = [m["message"] for m in payload["messages"] if m["status"] == "REVIEW_REQUIRED"]
    assert len(review) == 1
    assert f"{flipped_id}={FLIPPED_CODE}" in review[0]
    assert f"ratio={float(flipped_ratio):.2f}" in review[0]

    _write_strand_table(project, uniform)
    _recount(project)
    assert _counts_body_sha256(project / COUNTS) == PINNED_COUNTS_BODY_SHA256
    assert _sha256(project / DESEQ2) == PINNED_DESEQ2_SHA256
    assert _check21(project)["status"] == "PASS"
