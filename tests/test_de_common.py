"""Rscript-backed tests for workflow/scripts/de_common.R, the shared DE-engine helpers.

The helpers are pure R with no snakemake object, so they are exercised directly: one R
script writes its results to a directory and every test reads them back. R lives in the
bulkseq micromamba environment, which on a Windows development box is reachable only
through WSL, so the runner resolves Rscript natively first and falls back to WSL.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DE_COMMON = ROOT / "workflow" / "scripts" / "de_common.R"

# Exit code the shell runner uses when it cannot resolve an R interpreter at all, kept
# clear of Rscript's own status codes so "no R here" is never read as "the script failed".
_NO_R = 97

_RUNNER = f"""\
if [ -x "$HOME/.local/bin/micromamba" ] && "$HOME/.local/bin/micromamba" run -n bulkseq Rscript --version >/dev/null 2>&1; then
  exec "$HOME/.local/bin/micromamba" run -n bulkseq Rscript "$@"
elif command -v Rscript >/dev/null 2>&1; then
  exec Rscript "$@"
else
  exit {_NO_R}
fi
"""

_HARNESS = r'''
args <- commandArgs(trailingOnly = TRUE)
source(args[[1]])
out <- args[[2]]

bslash <- intToUtf8(92)
dquote <- intToUtf8(34)

# A covariate name carrying a backslash and a double quote: escaping the quote before the
# backslash would double-escape the backslash and produce invalid JSON.
covariate <- paste0("batch", bslash, "run", dquote, "A", dquote)
escaped <- sprintf("Design term '%s' is numeric with 3 distinct values.", covariate)
write_check(file.path(out, "check_escaped.json"), "08_metadata_design_qc", "REVIEW_REQUIRED",
            list(list(status = "REVIEW_REQUIRED", message = escaped)))
writeBin(charToRaw(escaped), file.path(out, "escaped_raw.bin"))

# The shape of a real check message: no backslash, quote or control character, so the
# escape must be a no-op and existing check files keep their bytes.
plain <- "467 genes padj < 0.05 (condition_treated_vs_untreated); 200 up / 267 down at |log2FC| >= 1."
write_check(file.path(out, "check_plain.json"), "09_deseq2_qc", "PASS",
            list(list(status = "PASS", message = plain)))
writeBin(charToRaw(plain), file.path(out, "plain_raw.bin"))

formulas <- c("~ condition", "~ genotype*treatment", "~ batch + condition",
              "~ genotype:treatment", "~ condition + (1/batch)")
writeLines(paste(formulas, vapply(formulas, has_interaction, logical(1)), sep = "\t"),
           file.path(out, "interaction.tsv"))

gtf <- file.path(out, "synthetic.gtf")
writeLines(c(
  paste0('NC_003424.3\tRefSeq\tgene\t1\t100\t.\t-\t.\tgene_id "SPOM_SPAC212.11"; ',
         'gene_name "tlh1"; db_xref "GeneID:2541932"; gene_biotype "protein_coding";'),
  paste0('NC_003424.3\tRefSeq\tgene\t200\t300\t.\t+\t.\tgene_id "SPOM_SPNCRNA.2000"; ',
         'gene_biotype "lncRNA";'),
  paste0('NC_003424.3\tRefSeq\texon\t1\t100\t.\t-\t.\tgene_id "SPOM_SPAC212.11"; ',
         'db_xref "GeneID:9999999";')
), gtf)
ids <- c("SPOM_SPAC212.11", "SPOM_SPNCRNA.2000", "absent_from_gtf")
annot <- annotate_from_gtf(gtf, ids)
writeLines(paste(ids, annot$symbol[ids], annot$biotype[ids], annot$geneid[ids], sep = "\t"),
           file.path(out, "annot.tsv"))

# The |log2FC| companion test is undefined at L <= 0, so the shared guard must refuse it.
writeLines(vapply(list(0, -1, 1.0, 0.5), function(L)
  tryCatch(sprintf("ok %s", require_lfc_threshold(L)), error = function(e) conditionMessage(e)),
  ""), file.path(out, "lfc_threshold.txt"))

set.seed(1)
m <- matrix(rnorm(40 * 6), nrow = 40,
            dimnames = list(paste0("g", 1:40), paste0("s", 1:6)))
m[1:5, 4:6] <- m[1:5, 4:6] + 10        # the five highest-variance rows separate the two groups
write_pca_coordinates(m, file.path(out, "pca.csv"), ntop = 20)
m_na <- m
m_na[6, 2] <- NA_real_                  # an incomplete row must be dropped, not poison the run
write_pca_coordinates(m_na, file.path(out, "pca_na.csv"), ntop = 20)
writeLines(tryCatch({
  write_pca_coordinates(m[1, , drop = FALSE], file.path(out, "pca_degenerate.csv")); "no error" },
  error = function(e) conditionMessage(e)), file.path(out, "pca_degenerate.txt"))

# Same numbers as DESeq2::plotPCA on the same matrix, which is what the DESeq2 engine writes:
# the claim that the shared writer follows plotPCA's convention is checked, not asserted.
if (requireNamespace("DESeq2", quietly = TRUE)) {
  suppressMessages({ library(DESeq2); library(matrixStats) })
  cd <- S4Vectors::DataFrame(condition = factor(rep(c("a", "b"), each = 3)), row.names = colnames(m))
  dt <- DESeq2::DESeqTransform(
    SummarizedExperiment::SummarizedExperiment(assays = list(x = m), colData = cd))
  ref <- DESeq2::plotPCA(dt, intgroup = "condition", ntop = 20, returnData = TRUE)
  ours <- read.csv(file.path(out, "pca.csv"))
  # Relative, because our side has been through write.csv's decimal rounding (as the DESeq2
  # engine's own pca_coordinates.csv has); the computation itself is the same.
  rel <- function(a, b) max(abs(a - b)) / max(abs(a))
  writeLines(c(paste(rownames(ref), collapse = ","),
               format(rel(ref$PC1, ours$PC1), scientific = TRUE),
               format(rel(ref$PC2, ours$PC2), scientific = TRUE),
               paste(identical(order(apply(m, 1, stats::var), decreasing = TRUE)[1:20],
                               order(matrixStats::rowVars(m), decreasing = TRUE)[1:20]))),
             file.path(out, "pca_vs_plotpca.txt"))
}
'''


def _r_available_note() -> str | None:
    if shutil.which("Rscript"):
        return None
    if sys.platform.startswith("win"):
        if shutil.which("wsl") is None:
            return "no Rscript on PATH and wsl.exe is absent"
        return None
    return "no Rscript on PATH"


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> dict[str, Path]:
    note = _r_available_note()
    if note:
        pytest.skip(f"R runtime unavailable: {note}")
    work = tmp_path_factory.mktemp("de_common")
    out = work / "out"
    out.mkdir()
    script = work / "harness.R"
    script.write_text(_HARNESS, encoding="utf-8", newline="\n")
    runner = work / "run_r.sh"
    runner.write_text(_RUNNER, encoding="utf-8", newline="\n")

    on_windows = sys.platform.startswith("win")
    if on_windows:
        from app.core.paths import windows_to_wsl_path

        as_path = windows_to_wsl_path
        cmd = ["wsl", "bash", as_path(runner)]
    else:
        as_path = str
        cmd = ["bash", str(runner)]
    cmd += [as_path(script), as_path(DE_COMMON), as_path(out)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == _NO_R:
        pytest.skip("R runtime unavailable: neither Rscript nor the bulkseq micromamba env")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return {"out": out}


def _message_from(check_path: Path) -> str:
    payload = json.loads(check_path.read_text(encoding="utf-8"))
    assert len(payload["messages"]) == 1
    return payload["messages"][0]["message"]


def test_write_check_escapes_a_backslash_and_a_quote_into_parseable_json(harness) -> None:
    out = harness["out"]
    expected = (out / "escaped_raw.bin").read_bytes().decode("utf-8")
    assert "\\" in expected and '"' in expected
    assert _message_from(out / "check_escaped.json") == expected


def test_write_check_leaves_an_ordinary_message_byte_identical(harness) -> None:
    # The property that keeps existing checks/*.json bytes unchanged: a message with no
    # backslash, quote or control character must pass through the escape untouched.
    out = harness["out"]
    plain = (out / "plain_raw.bin").read_bytes().decode("utf-8")
    raw = (out / "check_plain.json").read_text(encoding="utf-8")
    assert f'"message": "{plain}"' in raw
    assert _message_from(out / "check_plain.json") == plain


@pytest.mark.parametrize(
    ("formula", "expected"),
    [("~ condition", False), ("~ genotype*treatment", True), ("~ batch + condition", False),
     ("~ genotype:treatment", True), ("~ condition + (1/batch)", True)],
)
def test_interaction_detector(harness, formula: str, expected: bool) -> None:
    rows = dict(
        line.split("\t") for line in
        (harness["out"] / "interaction.tsv").read_text(encoding="utf-8").splitlines()
    )
    assert rows[formula] == ("TRUE" if expected else "FALSE")


def test_annotate_from_gtf_reads_symbol_biotype_and_the_db_xref_geneid(harness) -> None:
    rows = {
        parts[0]: parts[1:]
        for parts in (line.split("\t") for line in
                      (harness["out"] / "annot.tsv").read_text(encoding="utf-8").splitlines())
    }
    assert rows["SPOM_SPAC212.11"] == ["tlh1", "protein_coding", "2541932"]
    # A gene record without db_xref stays NA rather than inheriting a neighbour's GeneID,
    # and the exon line carrying a different GeneID must not be read as a gene record.
    assert rows["SPOM_SPNCRNA.2000"] == ["NA", "lncRNA", "NA"]
    assert rows["absent_from_gtf"] == ["NA", "NA", "NA"]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_require_lfc_threshold_refuses_a_non_positive_threshold(harness) -> None:
    # H0 |log2FC| <= L is vacuous at L = 0, and greaterAbs/treat/glmTreat all reject it, so the
    # shared guard must stop rather than let a meaningless companion column be written.
    zero, negative, one, half = _lines(harness["out"] / "lfc_threshold.txt")
    assert zero == negative
    assert "greater than zero" in zero
    assert (one, half) == ("ok 1", "ok 0.5")


def test_pca_writer_emits_exactly_the_columns_check_23_reads(harness) -> None:
    rows = _lines(harness["out"] / "pca.csv")
    assert rows[0] == '"sample_id","PC1","PC2"'
    ids = [row.split(",")[0].strip('"') for row in rows[1:]]
    assert ids == [f"s{i}" for i in range(1, 7)]
    pc1 = [float(row.split(",")[1]) for row in rows[1:]]
    # The seeded matrix separates s1-s3 from s4-s6 on the leading component.
    assert max(pc1[:3]) < 0 < min(pc1[3:]) or max(pc1[3:]) < 0 < min(pc1[:3])


def test_pca_writer_drops_an_incomplete_row_instead_of_failing(harness) -> None:
    # A microarray intensity matrix can carry NA probes; var() on such a row is NA, which would
    # otherwise silently shorten the high-variance selection.
    with_na = _lines(harness["out"] / "pca_na.csv")
    assert [row.split(",")[0] for row in with_na] == \
        [row.split(",")[0] for row in _lines(harness["out"] / "pca.csv")]


def test_pca_writer_refuses_a_matrix_with_too_few_rows(harness) -> None:
    message = (harness["out"] / "pca_degenerate.txt").read_text(encoding="utf-8").strip()
    assert message != "no error"
    assert "Too few complete rows" in message


def test_pca_writer_reproduces_deseq2_plotpca_on_the_same_matrix(harness) -> None:
    # The DESeq2 engine writes plotPCA's coordinates; the shared writer must be the same
    # computation so check 23 screens comparable numbers on every engine.
    path = harness["out"] / "pca_vs_plotpca.txt"
    if not path.exists():
        pytest.skip("DESeq2 is not installed in this R environment")
    ids, d_pc1, d_pc2, same_selection = _lines(path)
    assert ids == "s1,s2,s3,s4,s5,s6"
    # base var() and matrixStats::rowVars() (what plotPCA uses) must pick the same ntop rows.
    assert same_selection == "TRUE"
    # The residual is write.csv's decimal precision, not a different computation.
    assert float(d_pc1) < 1e-12
    assert float(d_pc2) < 1e-12
