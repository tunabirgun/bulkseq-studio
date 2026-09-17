"""Synthetic fits for alternate-engine condition and coefficient names."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from _runtime import rscript_runtime

ROOT = Path(__file__).resolve().parents[1]
DE_COMMON = ROOT / "workflow" / "scripts" / "de_common.R"
DE_RULES = ROOT / "workflow" / "rules" / "deseq2.smk"
ENGINE_SCRIPTS = tuple(
    ROOT / "workflow" / "scripts" / name
    for name in ("run_edger.R", "run_voom.R", "run_limma.R")
)


def _r_runtime(harness: Path, *paths: Path):
    runtime = rscript_runtime("edgeR", "limma")
    if runtime is None:
        reason = "R packages edgeR and limma are unavailable for alternate-engine fits"
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R_FULL") else pytest.skip)(reason)
    command, convert = runtime
    return [*command, convert(harness), *(convert(path) for path in paths)]


_HARNESS = r'''
args <- commandArgs(trailingOnly = TRUE)
source(args[[1]])

set.seed(260917)
n <- 18L
raw_group <- rep(c("A B", "A-B", "A.B"), each = n / 3L)
clean_group <- c("A B" = "other", "A-B" = "treated", "A.B" = "control")[raw_group]
counts <- matrix(rnbinom(80L * n, mu = 100, size = 20), nrow = 80L)
counts[1:8, raw_group == "A-B"] <- counts[1:8, raw_group == "A-B"] + 120L
rownames(counts) <- paste0("g", seq_len(nrow(counts)))

# Both metadata names are hazardous in the previous construction: `grp` shadows the local
# contrast factor through data-first lookup, while A.B collides with all three condition
# levels after their space, hyphen and dot spellings sanitize to the same coefficient name.
coldata <- data.frame(sample_id = paste0("s", seq_len(n)), check.names = FALSE)
rownames(coldata) <- coldata$sample_id
coldata[["grp"]] <- rep(c(-1, 1, 0), length.out = n)
coldata[["A.B"]] <- rep(c(0, 1), length.out = n)
covariates <- c("grp", "A.B")

production_dc <- function(group, numerator, denominator) {
  grp <- factor(group, levels = unique(group))
  group_means_design_contrast(grp, coldata, covariates, numerator, denominator)
}

# Independent construction: clean syntactic names and an explicitly positional contrast.
oracle_dc <- function(group, numerator, denominator) {
  oracle_data <- data.frame(group = factor(group, levels = unique(group)),
                            x1 = coldata[["grp"]], x2 = coldata[["A.B"]],
                            row.names = rownames(coldata))
  design <- model.matrix(~ 0 + group + x1 + x2, data = oracle_data)
  contrast <- numeric(ncol(design))
  contrast[match(numerator, levels(oracle_data$group))] <- 1
  contrast[match(denominator, levels(oracle_data$group))] <- -1
  list(design = design, contrast = contrast)
}

fit_edger <- function(dc, group) {
  grp <- factor(group, levels = unique(group))
  dge <- edgeR::DGEList(counts = counts, group = grp)
  dge <- edgeR::calcNormFactors(dge)
  dge <- edgeR::estimateDisp(dge, dc$design)
  fit <- edgeR::glmQLFit(dge, dc$design)
  edgeR::glmQLFTest(fit, contrast = dc$contrast)$table$logFC
}

fit_voom <- function(dc, group) {
  grp <- factor(group, levels = unique(group))
  dge <- edgeR::calcNormFactors(edgeR::DGEList(counts = counts, group = grp))
  v <- limma::voom(dge, dc$design, plot = FALSE)
  fit <- limma::contrasts.fit(limma::lmFit(v, dc$design), dc$contrast)
  fit$coefficients[, 1]
}

fit_limma <- function(dc, group) {
  expr <- log2(counts + 0.5)
  fit <- limma::contrasts.fit(limma::lmFit(expr, dc$design), dc$contrast)
  fit$coefficients[, 1]
}

raw_dc <- production_dc(raw_group, "A-B", "A.B")
clean_dc <- oracle_dc(clean_group, "treated", "control")
stopifnot(length(unique(colnames(raw_dc$design))) == ncol(raw_dc$design),
          identical(rownames(raw_dc$design), rownames(coldata)),
          identical(unname(raw_dc$contrast), unname(clean_dc$contrast)),
          isTRUE(all.equal(unname(raw_dc$design), unname(clean_dc$design), tolerance = 0,
                           check.attributes = FALSE)))

for (engine in list(fit_edger, fit_voom, fit_limma)) {
  observed <- engine(raw_dc, raw_group)
  oracle <- engine(clean_dc, clean_group)
  stopifnot(stats::median(observed[1:8]) > 0,
            isTRUE(all.equal(observed, oracle, tolerance = 1e-10)))
}

# Ordinary valid labels retain the previous design values and numerator-minus-denominator
# orientation; this is a separate positive control from the punctuation collision.
ordinary_group <- rep(c("control", "treated"), each = n / 2L)
ordinary <- production_dc(ordinary_group, "treated", "control")
ordinary_oracle <- oracle_dc(ordinary_group, "treated", "control")
stopifnot(isTRUE(all.equal(unname(ordinary$design), unname(ordinary_oracle$design), tolerance = 0,
                           check.attributes = FALSE)),
          identical(unname(ordinary$contrast), unname(ordinary_oracle$contrast)))
'''


def test_colliding_condition_labels_match_a_clean_bijective_fit(tmp_path) -> None:
    harness = tmp_path / "de_contrast_fit.R"
    harness.write_text(_HARNESS, encoding="utf-8", newline="\n")
    result = subprocess.run(_r_runtime(harness, DE_COMMON), capture_output=True, text=True,
                            timeout=180)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_missing_engine_packages_fail_when_full_r_is_mandatory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("BULKSEQ_REQUIRE_R_FULL", "1")
    monkeypatch.setattr("test_de_contrasts.rscript_runtime", lambda *packages: None)
    with pytest.raises(pytest.fail.Exception, match="alternate-engine fits"):
        _r_runtime(tmp_path / "missing.R")


def _assert_de_helper_dependencies(source: str) -> None:
    for rule_name in ("limma_de", "voom_de", "edger_de"):
        match = re.search(
            rf"(?ms)^\s*rule {rule_name}:\n(?P<body>.*?)(?=^\s*(?:rule |elif |else:))", source)
        assert match is not None, rule_name
        input_block = match.group("body").split("output:", 1)[0]
        assert 'de_helper="workflow/scripts/de_common.R"' in input_block, rule_name


def test_each_alternate_engine_tracks_the_shared_design_helper() -> None:
    _assert_de_helper_dependencies(DE_RULES.read_text(encoding="utf-8"))


def test_removing_a_design_helper_dependency_fails_the_gate() -> None:
    source = DE_RULES.read_text(encoding="utf-8")
    injected = source.replace('            de_helper="workflow/scripts/de_common.R",\n', "", 1)
    assert injected != source
    with pytest.raises(AssertionError, match="limma_de"):
        _assert_de_helper_dependencies(injected)


def test_all_alternate_engines_use_the_positional_shared_contrast() -> None:
    for path in ENGINE_SCRIPTS:
        source = path.read_text(encoding="utf-8")
        assert "group_means_design_contrast(" in source
        assert "makeContrasts(" not in source
        assert "make.names(numerator)" not in source
