from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from _runtime import rscript_runtime


ROOT = Path(__file__).resolve().parents[1]
META_SCRIPT = ROOT / "workflow" / "scripts" / "run_meta_enrichment.R"
TABLE_SCRIPT = ROOT / "workflow" / "scripts" / "make_meta_per_study_figures.R"
SELECTION_HELPER = ROOT / "workflow" / "scripts" / "meta_de_selection.R"
META_SMK = ROOT / "workflow" / "rules" / "meta.smk"


def _r_runtime(script: Path) -> tuple[list[str], str, Callable[[Path], str]]:
    runtime = rscript_runtime("jsonlite")
    if runtime is None:
        reason = "Rscript with jsonlite is unavailable for the meta foreground regression"
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R") else pytest.skip)(reason)
    command, convert = runtime
    return command, convert(script), convert


def test_meta_foregrounds_match_hand_declared_thresholded_table_sets(tmp_path: Path) -> None:
    command, script_path, runtime_path = _r_runtime(META_SCRIPT)
    helper_path = runtime_path(SELECTION_HELPER)
    code = f'''
source({helper_path!r})
exprs <- parse(file={script_path!r})
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      identical(as.character(expr[[2]]), "build_meta_enrichment_sets")) {{
    eval(expr, envir=.GlobalEnv)
  }}
}}

ids <- c("UP_STRONG", "UP_BOUND", "UP_SMALL", "DOWN_STRONG", "DOWN_BOUND",
         "DOWN_SMALL", "ZERO", "PADJ_BOUND", "PADJ_NA", "PADJ_NAN", "LFC_NA",
         "LFC_NAN", "LFC_INF", "SHARED_ONLY", "AMBIG", "CONVERGENT_UP")
lfc <- c(1.5, 1, 0.2, -2, -1, -0.2, 0, 2, 2, 2, NA, NaN, Inf, 0.8, 2, 0.2)
padj <- c(rep(0.01, 7), 0.05, NA, NaN, 0.01, 0.01, 0.01, 0.8, 0.01, 0.01)
res <- data.frame(
  gene_id=ids, meta_sig=ids == "CONVERGENT_UP",
  common_direction=ifelse(ids == "CONVERGENT_UP", "up", ""),
  study_A_log2FC=lfc, study_A_padj=padj,
  check.names=FALSE, stringsAsFactors=FALSE)
accepted_ids <- ids[ids != "AMBIG"]
accepted <- data.frame(
  input_id=accepted_ids, ENTREZID=as.character(seq_along(accepted_ids)),
  stringsAsFactors=FALSE)
resolved <- list(map=accepted)

run_case <- function(threshold) {{
  args <- list(res=res, ids=ids, resolved=resolved, alpha=0.05, min_size=1L)
  if ("lfc_threshold" %in% names(formals(build_meta_enrichment_sets)))
    args$lfc_threshold <- threshold
  do.call(build_meta_enrichment_sets, args)
}}
mapped <- function(names) unname(accepted$ENTREZID[match(names, accepted$input_id)])
table_ids <- c(ids, "OUTSIDE_SHARED")
table_lfc <- c(lfc, 1.2)
table_padj <- c(padj, 0.01)

positive <- run_case(1)
expected_positive_source <- list(
  A_up=c("UP_STRONG", "UP_BOUND", "AMBIG"),
  A_down=c("DOWN_STRONG", "DOWN_BOUND"),
  convergent_up="CONVERGENT_UP", convergent_down=character(0))
expected_positive_lists <- list(
  A_up=mapped(c("UP_STRONG", "UP_BOUND")),
  A_down=mapped(c("DOWN_STRONG", "DOWN_BOUND")),
  convergent_up=mapped("CONVERGENT_UP"))
published_positive <- meta_de_direction(table_padj, table_lfc, 0.05, 1)
published_positive_up <- table_ids[published_positive == "Up"]
expected_published_positive_up <- c("UP_STRONG", "UP_BOUND", "AMBIG", "OUTSIDE_SHARED")
published_positive_mapped <- mapped(intersect(published_positive_up, accepted$input_id))

zero <- run_case(0)
expected_zero_source <- list(
  A_up=c("UP_STRONG", "UP_BOUND", "UP_SMALL", "AMBIG", "CONVERGENT_UP"),
  A_down=c("DOWN_STRONG", "DOWN_BOUND", "DOWN_SMALL"),
  convergent_up="CONVERGENT_UP", convergent_down=character(0))
expected_zero_lists <- list(
  A_up=mapped(c("UP_STRONG", "UP_BOUND", "UP_SMALL", "CONVERGENT_UP")),
  A_down=mapped(c("DOWN_STRONG", "DOWN_BOUND", "DOWN_SMALL")),
  convergent_up=mapped("CONVERGENT_UP"))

stopifnot(
  identical(positive$source_sets, expected_positive_source),
  identical(positive$lists, expected_positive_lists),
  identical(published_positive_up, expected_published_positive_up),
  identical(published_positive_mapped, positive$lists$A_up),
  identical(zero$source_sets, expected_zero_source),
  identical(zero$lists, expected_zero_lists),
  "ZERO" %in% ids, !("ZERO" %in% unlist(zero$source_sets, use.names=FALSE)),
  "SHARED_ONLY" %in% accepted$input_id,
  accepted$ENTREZID[accepted$input_id == "SHARED_ONLY"] %in% positive$universe,
  !("OUTSIDE_SHARED" %in% ids), !("AMBIG" %in% accepted$input_id),
  !("CONVERGENT_UP" %in% positive$source_sets$A_up),
  "CONVERGENT_UP" %in% positive$source_sets$convergent_up)
cat("meta foreground threshold oracle OK\\n")
'''
    harness = tmp_path / "meta_foreground_threshold.R"
    harness.write_text(code, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [*command, runtime_path(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "meta foreground threshold oracle OK" in completed.stdout


def test_meta_table_and_enrichment_share_the_production_selector_and_rule_parameter() -> None:
    helper = SELECTION_HELPER.read_text(encoding="utf-8")
    meta = META_SCRIPT.read_text(encoding="utf-8")
    table = TABLE_SCRIPT.read_text(encoding="utf-8")
    rule = META_SMK.read_text(encoding="utf-8")

    assert "meta_de_direction <- function" in helper
    source_line = 'source(file.path(snakemake@scriptdir, "meta_de_selection.R"))'
    assert source_line in meta
    assert source_line in table
    assert "meta_de_direction(padj, lfc, alpha, lfc_threshold)" in meta
    assert "meta_de_direction(de$padj, de$log2FoldChange, alpha, lfc_thr)" in table
    assert rule.count('selection_helper="workflow/scripts/meta_de_selection.R"') == 2
    assert "lfc_threshold=_META_DE.get(\"lfc_threshold\", 1.0)" in rule


def test_missing_r_fails_when_meta_foreground_contract_is_mandatory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("BULKSEQ_REQUIRE_R", "1")
    monkeypatch.setattr("test_meta_enrichment_threshold.rscript_runtime", lambda *packages: None)
    with pytest.raises(pytest.fail.Exception, match="meta foreground regression"):
        _r_runtime(tmp_path / "missing.R")


def test_missing_r_skips_when_meta_foreground_contract_is_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("BULKSEQ_REQUIRE_R", raising=False)
    monkeypatch.delenv("BULKSEQ_REQUIRE_R_FULL", raising=False)
    monkeypatch.setattr("test_meta_enrichment_threshold.rscript_runtime", lambda *packages: None)
    with pytest.raises(pytest.skip.Exception, match="meta foreground regression"):
        _r_runtime(tmp_path / "missing.R")
