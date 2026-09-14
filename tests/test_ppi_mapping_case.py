from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import pytest

from _runtime import rscript_runtime


SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "build_string_network.R"


def _r_runtime(script: Path) -> tuple[list[str], str, Callable[[Path], str]]:
    # These harnesses evaluate sliced base-R helpers: any working R runs them.
    runtime = rscript_runtime()
    if runtime is None:
        pytest.skip("Rscript is not available for the PPI identifier-case regression")
    command, convert = runtime
    return command, convert(script), convert


def _assert_case_restoration_wiring(source: str) -> None:
    preserve = "seed_lookup <- build_string_seed_lookup(seed)"
    query = "data.frame(gene_id = seed_lookup$query_id"
    mapping = "mapped <- sdb$map("
    restore = "mapped <- restore_mapped_display_ids(mapped, seed_lookup)"
    labels = "id2sym <- tapply(mapped$gene_id, mapped$STRING_id"
    for fragment in (preserve, query, mapping, restore, labels):
        assert fragment in source
    assert source.index(preserve) < source.index(mapping)
    assert source.index(mapping) < source.index(query)
    assert source.index(query) < source.index(restore)
    assert source.index(restore) < source.index(labels)


def test_ppi_mapping_restores_exact_input_case_after_global_uppercase_mapper(
    tmp_path: Path,
) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
exprs <- parse(file={script_path!r})
wanted <- c("strip_loc", "normalize_string_query", "build_string_seed_lookup",
            "restore_mapped_display_ids")
for (expr in exprs) {{
  if (is.call(expr) && length(expr) >= 3L && is.symbol(expr[[2]]) &&
      identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}

original <- c("sesB", "Hml", "DOR")
expected_expanded <- c("sesB", "Hml", "DOR", "DOR")
mapped_upper <- data.frame(
  gene_id=c("SESB", "HML", "DOR", "DOR"),
  STRING_id=c("7227.sesB", "7227.Hml", "7227.DOR.a", "7227.DOR.b"),
  stringsAsFactors=FALSE
)

# Negative gate: this is the legacy mapper output. One DOR query expands to two
# STRING rows and the mapper uppercases every gene_id, losing canonical display case.
legacy_error <- tryCatch({{
  if (!identical(mapped_upper$gene_id, expected_expanded))
    stop("legacy mapper lost canonical display case")
  NA_character_
}}, error=function(e) conditionMessage(e))
stopifnot(identical(legacy_error, "legacy mapper lost canonical display case"))

seed_lookup <- build_string_seed_lookup(original)
restored <- restore_mapped_display_ids(mapped_upper, seed_lookup)
stopifnot(identical(restored$gene_id, expected_expanded))
stopifnot(sum(restored$gene_id == "DOR") == 2L)

# STRING-id topology and the case-insensitive DE join are invariant; only display
# identity changes. The realized mapped-seed and STRING-id counts also stay fixed.
inter <- data.frame(
  from=c("7227.sesB", "7227.DOR.a", "7227.DOR.b"),
  to=c("7227.DOR.a", "7227.Hml", "7227.sesB"),
  stringsAsFactors=FALSE
)
label_edges <- function(mapped) {{
  id2sym <- tapply(mapped$gene_id, mapped$STRING_id, function(x) x[1])
  data.frame(from=unname(id2sym[inter$from]), to=unname(id2sym[inter$to]),
             stringsAsFactors=FALSE)
}}
legacy_edges <- label_edges(mapped_upper)
restored_edges <- label_edges(restored)
stopifnot(identical(toupper(restored_edges$from), legacy_edges$from),
          identical(toupper(restored_edges$to), legacy_edges$to))
lfc_map <- setNames(c(-2.25, 1.5, 0.75), toupper(original))
stopifnot(identical(unname(lfc_map[toupper(mapped_upper$gene_id)]),
                    unname(lfc_map[toupper(restored$gene_id)])))
stopifnot(length(unique(mapped_upper$gene_id)) == length(unique(restored$gene_id)),
          length(unique(mapped_upper$STRING_id)) == length(unique(restored$STRING_id)))

# LOC query normalization must not replace the original display identifier.
loc_lookup <- build_string_seed_lookup(c("LOC123", "Hml"))
stopifnot(identical(loc_lookup$query_id, c("123", "Hml")))
loc_mapped <- data.frame(gene_id=c("123", "HML"), STRING_id=c("x", "y"),
                         stringsAsFactors=FALSE)
stopifnot(identical(restore_mapped_display_ids(loc_mapped, loc_lookup)$gene_id,
                    c("LOC123", "Hml")))

ambiguous_error <- tryCatch({{
  build_string_seed_lookup(c("sesB", "SESB")); NA_character_
}}, error=function(e) conditionMessage(e))
stopifnot(grepl("ambiguous STRING query collision", ambiguous_error, fixed=TRUE))

unknown <- mapped_upper
unknown$gene_id[1] <- "NOT_A_SEED"
restore_error <- tryCatch({{
  restore_mapped_display_ids(unknown, seed_lookup); NA_character_
}}, error=function(e) conditionMessage(e))
stopifnot(grepl("could not be restored", restore_error, fixed=TRUE))
'''
    harness = tmp_path / "ppi_case_regression.R"
    harness.write_text(code, encoding="utf-8")
    result = subprocess.run(
        [*command, runtime_path(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_ppi_duplicate_symbol_rule_matches_the_interactive_viewer(tmp_path: Path) -> None:
    # Same fixture as tests/test_ppi_graph.py's duplicate-symbol tests: the static
    # figure's de_value_map() must pick the row the viewer picks (lowest padj; a
    # missing padj never wins; a tie keeps the first row).
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = f'''
exprs <- parse(file={script_path!r})
for (expr in exprs) {{
  if (is.call(expr) && length(expr) >= 3L && is.symbol(expr[[2]]) &&
      identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% c("strip_loc", "de_value_map")) eval(expr, envir=.GlobalEnv)
}}

res <- data.frame(
  gene_id=c("g_high", "g_sig", "no_padj", "has_padj", "first", "second"),
  symbol=c("DUP", "DUP", "NA1", "NA1", "TIE", "TIE"),
  log2FoldChange=c(0.2, -3.5, 5.0, -1.0, 1.0, 2.0),
  padj=c(0.40, 1e-8, NA_real_, 0.4, 0.01, 0.01),
  stringsAsFactors=FALSE)
lfc <- de_value_map(res$symbol, res$log2FoldChange, res$padj)
stopifnot(identical(unname(lfc[["DUP"]]), -3.5))   # lowest padj, not the larger effect
stopifnot(identical(unname(lfc[["NA1"]]), -1.0))   # NA padj never beats a real one
stopifnot(identical(unname(lfc[["TIE"]]), 1.0))    # tie keeps the first table row

# Negative gate: the previous first-row rule disagrees on the duplicated symbol.
legacy <- setNames(res$log2FoldChange, toupper(res$symbol))
stopifnot(identical(unname(legacy[["DUP"]]), 0.2),
          !identical(unname(legacy[["DUP"]]), unname(lfc[["DUP"]])))

# A results table without padj keeps the first-row behaviour rather than failing.
stopifnot(identical(unname(de_value_map(res$symbol, res$log2FoldChange)[["DUP"]]), 0.2))
# The join is case-insensitive on both sides.
stopifnot(identical(unname(de_value_map(c("sesB"), c(1.5), c(0.01))[["SESB"]]), 1.5))
'''
    harness = tmp_path / "ppi_duplicate_rule.R"
    harness.write_text(code, encoding="utf-8")
    result = subprocess.run(
        [*command, runtime_path(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_ppi_case_restoration_is_wired_between_mapping_and_graph_labels() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    _assert_case_restoration_wiring(source)

    broken = source.replace(
        "mapped <- restore_mapped_display_ids(mapped, seed_lookup)", "", 1
    )
    assert broken != source
    with pytest.raises(AssertionError):
        _assert_case_restoration_wiring(broken)
