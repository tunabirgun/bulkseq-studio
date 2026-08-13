from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from app.core.paths import windows_to_wsl_path


SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "run_custom_enrichment.R"


def _r_runtime(script: Path) -> tuple[list[str], str, Callable[[Path], str]]:
    rscript = shutil.which("Rscript")
    if rscript:
        return [rscript, "--vanilla"], script.as_posix(), lambda path: str(path)
    wsl = shutil.which("wsl.exe")
    if os.name == "nt" and wsl:
        prefix = subprocess.run(
            [wsl, "--", "bash", "-lc", (
                'if [ -x "$HOME/micromamba/envs/bulkseq/bin/Rscript" ]; then '
                'echo "$HOME/micromamba/envs/bulkseq/bin/Rscript"; '
                'elif [ -x "/root/micromamba/envs/bulkseq/bin/Rscript" ]; then '
                'echo "/root/micromamba/envs/bulkseq/bin/Rscript"; '
                'elif [ -x "$HOME/.local/share/mamba/envs/bulkseq/bin/Rscript" ]; then '
                'echo "$HOME/.local/share/mamba/envs/bulkseq/bin/Rscript"; '
                'else exit 1; fi'
            )],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if prefix.returncode == 0 and prefix.stdout.strip():
            return (
                [wsl, "--", prefix.stdout.strip(), "--vanilla"],
                windows_to_wsl_path(script),
                windows_to_wsl_path,
            )
    pytest.skip("Rscript is not available for the custom-enrichment regression")


def test_custom_gsea_rank_is_deterministic_and_reports_exact_ties(tmp_path: Path) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
exprs <- parse(file={script_path!r})
wanted <- c("build_custom_deterministic_rank", "custom_rank_evidence_lines",
            "with_deterministic_custom_gsea_ties")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}

# Negative gate: the old score-only sort is stable, so reversing source rows
# reverses exact ties instead of satisfying the canonical-id contract.
ids <- c("zeta", "Alpha", "beta", "solo")
stats <- c(5, 5, 5, 4)
old_forward <- sort(setNames(stats, ids), decreasing=TRUE)
old_reverse <- sort(setNames(rev(stats), rev(ids)), decreasing=TRUE)
stopifnot(!identical(old_forward, old_reverse))

rank_forward <- build_custom_deterministic_rank(stats, ids)
rank_reverse <- build_custom_deterministic_rank(rev(stats), rev(ids))
stopifnot(identical(rank_forward[["values"]], rank_reverse[["values"]]),
          identical(names(rank_forward[["values"]]),
                    c("Alpha", "beta", "zeta", "solo")),
          rank_forward[["tie_group_n"]] == 1L,
          rank_forward[["tie_pair_n"]] == 3,
          rank_forward[["tied_gene_n"]] == 3L,
          grepl("bytewise UTF-8/C-locale", rank_forward[["policy"]], fixed=TRUE))

accented_id <- enc2utf8("\u00e9")
utf8_rank <- build_custom_deterministic_rank(c(3, 3, 3),
                                              c(accented_id, "z", "Alpha"))
stopifnot(identical(names(utf8_rank[["values"]]),
                    c("Alpha", "z", accented_id)))

# Negative gates for the previous first-row-wins reducer. Finite duplicates
# select different scores after reversal; a non-finite first row can also erase
# an otherwise valid duplicate group before the finite-value filter runs.
old_first_rank <- function(statistic, canonical_id) {{
  statistic <- as.numeric(statistic)
  canonical_id <- as.character(canonical_id)
  valid_id <- !is.na(canonical_id) & nzchar(canonical_id)
  statistic <- statistic[valid_id]
  canonical_id <- canonical_id[valid_id]
  keep_first <- !duplicated(canonical_id)
  statistic <- statistic[keep_first]
  canonical_id <- canonical_id[keep_first]
  finite <- is.finite(statistic)
  statistic <- statistic[finite]
  canonical_id <- canonical_id[finite]
  ord <- order(-statistic, canonical_id, method="radix")
  setNames(statistic[ord], canonical_id[ord])
}}

duplicate_ids <- c("dup", "dup", "tie", "solo")
duplicate_stats <- c(2, 8, 5, 1)
old_duplicate_forward <- old_first_rank(duplicate_stats, duplicate_ids)
old_duplicate_reverse <- old_first_rank(rev(duplicate_stats), rev(duplicate_ids))
stopifnot(!identical(old_duplicate_forward, old_duplicate_reverse))
duplicate_forward <- build_custom_deterministic_rank(duplicate_stats, duplicate_ids)
duplicate_reverse <- build_custom_deterministic_rank(
  rev(duplicate_stats), rev(duplicate_ids))
stopifnot(identical(duplicate_forward[["values"]], duplicate_reverse[["values"]]),
          identical(names(duplicate_forward[["values"]]), c("dup", "tie", "solo")),
          identical(unname(duplicate_forward[["values"]]), c(5, 5, 1)),
          duplicate_forward[["duplicate_id_group_n"]] == 1L,
          duplicate_forward[["duplicate_source_row_n"]] == 2L,
          duplicate_forward[["duplicate_rows_collapsed"]] == 1L)

nonfinite_ids <- c("dup", "dup", "dup", "solo", "", NA_character_)
nonfinite_stats <- c(Inf, 2, 4, 1, 9, 10)
old_nonfinite_forward <- old_first_rank(nonfinite_stats, nonfinite_ids)
old_nonfinite_reverse <- old_first_rank(rev(nonfinite_stats), rev(nonfinite_ids))
stopifnot(!identical(old_nonfinite_forward, old_nonfinite_reverse))
nonfinite_forward <- build_custom_deterministic_rank(nonfinite_stats, nonfinite_ids)
nonfinite_reverse <- build_custom_deterministic_rank(
  rev(nonfinite_stats), rev(nonfinite_ids))
stopifnot(identical(nonfinite_forward[["values"]], nonfinite_reverse[["values"]]),
          identical(names(nonfinite_forward[["values"]]), c("dup", "solo")),
          identical(unname(nonfinite_forward[["values"]]), c(3, 1)),
          nonfinite_forward[["duplicate_id_group_n"]] == 1L,
          nonfinite_forward[["duplicate_source_row_n"]] == 2L,
          nonfinite_forward[["duplicate_rows_collapsed"]] == 1L,
          nonfinite_forward[["invalid_id_removed"]] == 2L,
          nonfinite_forward[["nonfinite_removed"]] == 1L)

evidence <- custom_rank_evidence_lines(rank_forward)
stopifnot(any(grepl("3 pair(s) across 1 tie group(s)", evidence, fixed=TRUE)),
          any(grepl("involving 3/4 ranked genes", evidence, fixed=TRUE)))
duplicate_evidence <- custom_rank_evidence_lines(nonfinite_forward)
stopifnot(any(grepl("1 group(s) containing 2 finite source row(s)",
                    duplicate_evidence, fixed=TRUE)),
          any(grepl("1 row(s) collapsed by median", duplicate_evidence, fixed=TRUE)),
          any(grepl("2 invalid-ID and 1 non-finite-score", duplicate_evidence,
                    fixed=TRUE)))

captured <- capture.output({{
  value <- with_deterministic_custom_gsea_ties({{
    warning(paste0("There are ties in the preranked stats (75% of the list).\\n",
                   "The order of those tied genes will be arbitrary, which may produce unexpected results."))
    7L
  }}, rank_forward)
}}, type="message")
stopifnot(value == 7L,
          any(grepl("Custom GSEA deterministic tie handling", captured, fixed=TRUE)),
          !any(grepl("arbitrary", captured, fixed=TRUE)))

unrelated_seen <- FALSE
invisible(withCallingHandlers(
  with_deterministic_custom_gsea_ties({{ warning("unrelated warning"); 1L }}, rank_forward),
  warning=function(w) {{ unrelated_seen <<- TRUE; invokeRestart("muffleWarning") }}))
stopifnot(unrelated_seen)

# No-network integration probe against the installed enrichment engine. This
# exercises the pinned fgsea route when the workflow R environment is present,
# while keeping the pure ordering contract testable in a base-R-only runtime.
if (requireNamespace("clusterProfiler", quietly=TRUE)) {{
  engine_ids <- sprintf("gene%03d", seq_len(60))
  engine_stats <- rep(20:1, each=3)
  engine_forward <- build_custom_deterministic_rank(engine_stats, engine_ids)
  engine_reverse <- build_custom_deterministic_rank(rev(engine_stats), rev(engine_ids))
  stopifnot(identical(engine_forward[["values"]], engine_reverse[["values"]]))
  ranked_ids <- names(engine_forward[["values"]])
  term2gene <- data.frame(
    term=rep(c("TOP", "BOTTOM", "MIXED"), each=20),
    gene=c(ranked_ids[1:20], ranked_ids[41:60], ranked_ids[seq(1, 60, by=3)]),
    stringsAsFactors=FALSE)
  run_engine <- function(rank_info) {{
    set.seed(42)
    with_deterministic_custom_gsea_ties(
      clusterProfiler::GSEA(
        geneList=rank_info[["values"]], TERM2GENE=term2gene,
        pvalueCutoff=1, pAdjustMethod="BH", minGSSize=5, maxGSSize=100,
        eps=0, seed=TRUE, verbose=FALSE),
      rank_info)
  }}
  engine_warnings <- character(0)
  engine_messages <- capture.output({{
    withCallingHandlers({{
      engine_a <- run_engine(engine_forward)
      engine_b <- run_engine(engine_reverse)
    }}, warning=function(w) {{
      engine_warnings <<- c(engine_warnings, conditionMessage(w))
      invokeRestart("muffleWarning")
    }})
  }}, type="message")
  stopifnot(identical(as.data.frame(engine_a), as.data.frame(engine_b)),
            nrow(as.data.frame(engine_a)) > 0L,
            any(grepl("Custom GSEA deterministic tie handling", engine_messages,
                      fixed=TRUE)),
            !any(grepl("arbitrary", engine_messages, fixed=TRUE)),
            !any(grepl("arbitrary", engine_warnings, fixed=TRUE)))
  cat("custom GSEA engine probe OK\\n")
}}
cat("custom deterministic GSEA rank contract OK\\n")
'''
    harness = tmp_path / "custom_deterministic_gsea_rank.R"
    harness.write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [*command, runtime_path(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "custom deterministic GSEA rank contract OK" in completed.stdout

    source = SCRIPT.read_text(encoding="utf-8")
    assert "ranked <- sort" not in source
    assert "with_deterministic_custom_gsea_ties(" in source
    assert "rank_evidence," in source
