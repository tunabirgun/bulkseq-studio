from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _runtime import rscript_runtime


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "workflow" / "scripts"


def _run(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    runtime = rscript_runtime()
    if runtime is None:
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R") else pytest.skip)(
            "Rscript is required for the additive formula contract")
    command, convert = runtime
    harness = tmp_path / "additive_formula.R"
    harness.write_text(code, encoding="utf-8")
    return subprocess.run(
        [*command, convert(harness), convert(SCRIPTS)], capture_output=True,
        text=True, timeout=60, check=False,
    )


SETUP = r'''
scripts <- commandArgs(trailingOnly=TRUE)[[1]]
source(file.path(scripts, "de_common.R"))
coldata <- data.frame(condition=factor(rep(c("A B", "A-B", "A.B"), each=4)),
                      age=c(20, 33, 29, 54, 27, 38, 48, 41, 22, 46, 37, 61),
                      batch=factor(rep(c("b1", "b2"), 6)))
con_factor <- "condition"
grp <- coldata[[con_factor]]
numerator <- "A-B"
denominator <- "A.B"
engines <- c("run_edger.R", "run_voom.R", "run_limma.R")
blocks <- lapply(engines, function(engine) {
  lines <- readLines(file.path(scripts, engine), warn=FALSE)
  start <- grep("^# ---- Design: group-means", lines)
  end <- grep("^# The formula actually fitted", lines)
  parse(text=lines[seq.int(start + 1L, end - 1L)])
})
'''

REJECTION = r'''
bad <- c("~ condition + log(age)", "~ condition + age - age", "~ age",
         "~ condition + batch %in% age", "~ condition + I(age^2)",
         "~ condition + offset(age)", "~ condition * batch", "~ .",
         "age ~ condition", "~ condition + unknown", "~ condition - condition")
rejected <- vapply(blocks, function(block) {
  vapply(bad, function(formula) {
    env <- new.env(parent=.GlobalEnv)
    env$design_formula <- formula
    tryCatch({eval(block, env); FALSE}, error=function(e) TRUE)
  }, logical(1))
}, logical(length(bad)))
rownames(rejected) <- bad
colnames(rejected) <- engines
print(rejected)
stopifnot(all(rejected))
'''


def test_engines_reject_formulas_they_cannot_preserve(tmp_path: Path) -> None:
    result = _run(tmp_path, SETUP + REJECTION)
    assert result.returncode == 0, result.stdout + result.stderr


def test_formula_gate_detects_permissive_parser_mutation(tmp_path: Path) -> None:
    mutation = r'''
additive_design_covariates <- function(design_formula, con_factor, columns) {
  setdiff(all.vars(as.formula(design_formula)), con_factor)
}
'''
    result = _run(tmp_path, SETUP + mutation + REJECTION)
    assert result.returncode != 0, "The formula gate accepted the permissive parser mutation"
    assert "all(rejected) is not TRUE" in result.stderr


def test_accepted_formulas_preserve_fitted_contrast(tmp_path: Path) -> None:
    code = r'''
formulas <- c("~ condition + age + batch", "~ 1 + condition + age + batch",
              "~ 0 + condition + age + batch", "~ condition + age + batch - 1",
              "~ -1 + condition + age + batch", "~ condition + age + batch + 0",
              "~ (condition + age) + batch", "~ batch + age + condition",
              "~ condition + condition + age + batch")
y <- c(2, 4, 1, 8, 6, 9, 5, 10, 3, 7, 6, 11)
for (formula in formulas) {
  oracle <- model.matrix(as.formula(formula), coldata)
  high <- low <- coldata[1, , drop=FALSE]
  high$condition <- factor(numerator, levels=levels(grp))
  low$condition <- factor(denominator, levels=levels(grp))
  oracle_contrast <- model.matrix(as.formula(formula), high) -
    model.matrix(as.formula(formula), low)
  oracle_fit <- lm.fit(oracle, y)
  expected <- sum(oracle_fit$coefficients * oracle_contrast)
  expected_var <- drop(oracle_contrast %*% solve(crossprod(oracle)) %*% t(oracle_contrast))
  for (block in blocks) {
    env <- new.env(parent=.GlobalEnv)
    env$design_formula <- formula
    eval(block, env)
    actual <- lm.fit(env$design, y)
    contrast <- env$design_contrast$contrast
    observed <- sum(actual$coefficients * contrast)
    observed_var <- drop(t(contrast) %*% solve(crossprod(env$design)) %*% contrast)
    stopifnot(isTRUE(all.equal(actual$fitted.values, oracle_fit$fitted.values, tolerance=1e-10)),
              abs(observed - expected) < 1e-10, abs(observed_var - expected_var) < 1e-10)
  }
}
cat("accepted formula fitted values, contrast and variance agree\n")
'''
    result = _run(tmp_path, SETUP + code)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("formula", [
    "{probe <<- TRUE; ~ condition}", "~ condition; probe <<- TRUE",
    "local({probe <<- TRUE; ~ condition})", "condition ~ age",
])
def test_formula_text_is_rejected_without_evaluation(tmp_path: Path, formula: str) -> None:
    code = SETUP + f'''
probe <- FALSE
formula <- {formula!r}
for (block in blocks) {{
  env <- new.env(parent=.GlobalEnv)
  env$design_formula <- formula
  rejected <- tryCatch({{eval(block, env); FALSE}}, error=function(e) TRUE)
  stopifnot(rejected, identical(probe, FALSE))
}}
deseq <- parse(file=file.path(scripts, "run_deseq2.R"))
assignment <- Filter(function(e) is.call(e) && identical(e[[1]], as.name("<-")) &&
  identical(e[[2]], as.name("design")), as.list(deseq))
stopifnot(length(assignment) == 1L)
design_formula <- formula
rejected <- tryCatch({{eval(assignment[[1]]); FALSE}}, error=function(e) TRUE)
stopifnot(rejected, identical(probe, FALSE))
parse_design_formula(formula)
'''
    result = _run(tmp_path, code)
    assert result.returncode != 0
    assert "Design must be one one-sided formula" in result.stderr
    assert "is not TRUE" not in result.stderr


def test_deseq_formula_parser_preserves_supported_transformations(tmp_path: Path) -> None:
    code = SETUP + r'''
for (text in c("~ condition + log(age)", "~ condition + I(age^2)",
               "~ condition * batch", "~ condition + age - age",
               "~ condition + scale(age)", "~ condition + poly(age, 2, raw=TRUE)")) {
  actual <- model.matrix(parse_design_formula(text), coldata)
  expected <- model.matrix(as.formula(text), coldata)
  stopifnot(identical(actual, expected))
}
'''
    result = _run(tmp_path, code)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("rhs", [
    "I({probe <<- TRUE; age})", "I(system('echo unsafe'))",
    "I(assign('probe', TRUE, .GlobalEnv))", "base::log(age)",
    "(function(x) {probe <<- TRUE; x})(age)", "get('log')(age)",
])
def test_formula_rhs_rejects_arbitrary_calls_without_side_effects(tmp_path: Path, rhs: str) -> None:
    code = SETUP + f'''
probe <- FALSE
error <- tryCatch({{parse_design_formula({('~ condition + ' + rhs)!r}); NULL}},
                  error=function(e) conditionMessage(e))
stopifnot(identical(probe, FALSE), grepl("Unsupported design expression", error, fixed=TRUE))
stop(error)
'''
    result = _run(tmp_path, code)
    assert result.returncode != 0
    assert "Unsupported design expression" in result.stderr
    assert "is not TRUE" not in result.stderr
