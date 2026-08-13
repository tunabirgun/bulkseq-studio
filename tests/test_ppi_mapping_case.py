from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from app.core.paths import windows_to_wsl_path


SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "build_string_network.R"


def _r_runtime(script: Path) -> tuple[list[str], str, Callable[[Path], str]]:
    rscript = shutil.which("Rscript")
    if rscript:
        return [rscript, "--vanilla"], script.as_posix(), lambda path: str(path)
    wsl = shutil.which("wsl.exe")
    if os.name == "nt" and wsl:
        prefix = subprocess.run(
            [wsl, "--", "bash", "-lc", (
                'if command -v Rscript >/dev/null 2>&1; then command -v Rscript; '
                'elif [ -x "$HOME/micromamba/envs/bulkseq/bin/Rscript" ]; then '
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
    pytest.skip("Rscript is not available for the PPI identifier-case regression")


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


def test_ppi_case_restoration_is_wired_between_mapping_and_graph_labels() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    _assert_case_restoration_wiring(source)

    broken = source.replace(
        "mapped <- restore_mapped_display_ids(mapped, seed_lookup)", "", 1
    )
    assert broken != source
    with pytest.raises(AssertionError):
        _assert_case_restoration_wiring(broken)
