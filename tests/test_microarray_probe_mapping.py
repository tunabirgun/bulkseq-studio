from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from _runtime import rscript_runtime


SCRIPT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "ingest_geo.R"


def _r_runtime(
    script: Path, *packages: str
) -> tuple[list[str], str, Callable[[Path], str]]:
    runtime = rscript_runtime(*packages)
    if runtime is None:
        reason = "Rscript with the required packages is unavailable for probe-mapping regressions"
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R") else pytest.skip)(reason)
    command, convert = runtime
    return command, convert(script), convert


def test_missing_r_fails_when_the_r_contract_is_mandatory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BULKSEQ_REQUIRE_R", "1")
    monkeypatch.setattr("test_microarray_probe_mapping.rscript_runtime", lambda *packages: None)
    with pytest.raises(pytest.fail.Exception, match="probe-mapping regressions"):
        _r_runtime(tmp_path / "missing.R")


def _run_mapping_harness(tmp_path: Path, assertions: str) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT)
    code = rf'''
source_lines <- readLines({script_path!r}, warn=FALSE)
start <- grep("# ---- 3. Probe", source_lines, fixed=TRUE)[1]
end <- grep("# ---- 5. Validate", source_lines, fixed=TRUE)[1] - 1L
stopifnot(!is.na(start), !is.na(end), end > start)
mapping_code <- parse(text=paste(source_lines[start:end], collapse="\n"))

run_case <- function(exprs_mat, fdata=NULL, source_kind="geo_series_matrix") {{
  env <- new.env(parent=globalenv())
  env$exprs_mat <- exprs_mat
  env$fdata <- fdata
  env$source_kind <- source_kind
  env$out_map <- tempfile(fileext=".tsv")
  env$out_map_check <- tempfile(fileext=".json")
  env$write_check <- function(path, name, status, messages) {{
    env$check_record <- list(name=name, status=status, messages=messages)
  }}
  eval(mapping_code, envir=env)
  list(symbols=env$symbols, gene_mat=env$gene_mat,
       probe_map=utils::read.delim(env$out_map, check.names=FALSE,
                                  stringsAsFactors=FALSE, quote="\""),
       map_lines=readLines(env$out_map, warn=FALSE), check=env$check_record)
}}

{assertions}
'''
    harness = tmp_path / "probe_mapping_regression.R"
    harness.write_text(code, encoding="utf-8")
    result = subprocess.run(
        [*command, runtime_path(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_symbol_annotations_classify_ambiguous_unknown_and_missing_probes(
    tmp_path: Path,
) -> None:
    _run_mapping_harness(
        tmp_path,
        r'''
expr <- matrix(seq_len(8), nrow=4,
               dimnames=list(c("p_multi", "p_unique", "p_unknown", "p_missing"),
                             c("s1", "s2")))
ann <- data.frame(ID=c("p_multi", "p_unique", "p_unknown"),
                  `Gene Symbol`=c("GENEB /// GENEA", "SOLO", "---"),
                  check.names=FALSE, stringsAsFactors=FALSE)
got <- run_case(expr, ann)
stopifnot(identical(got$symbols, c(NA_character_, "SOLO", NA_character_, NA_character_)))
classes <- setNames(got$probe_map$mapping_class, got$probe_map$probe)
stopifnot(identical(classes[c("p_multi", "p_unique", "p_unknown", "p_missing")],
                    c(p_multi="ambiguous", p_unique="unique", p_unknown="unknown",
                      p_missing="missing_annotation")))
stopifnot(identical(got$probe_map$candidate_gene_ids[got$probe_map$probe == "p_multi"],
                    "GENEA | GENEB"))
''',
    )


def test_candidate_and_repeated_annotation_row_order_do_not_change_resolution(
    tmp_path: Path,
) -> None:
    _run_mapping_harness(
        tmp_path,
        r'''
expr <- matrix(c(1, 3, 2, 4), nrow=2,
               dimnames=list(c("p_amb", "p_same"), c("s1", "s2")))
forward <- data.frame(
  ID=c("p_amb", "p_amb", "p_same", "p_same"),
  `Gene Symbol`=c("ALPHA", "BETA", "KEEP", "KEEP // KEEP"),
  check.names=FALSE, stringsAsFactors=FALSE)
reverse <- forward[c(2, 1, 4, 3), , drop=FALSE]
a <- run_case(expr, forward)
b <- run_case(expr, reverse)
stopifnot(identical(a$symbols, b$symbols),
          identical(a$symbols, c(NA_character_, "KEEP")),
          identical(a$gene_mat, b$gene_mat),
          identical(a$probe_map[, setdiff(names(a$probe_map), "raw_annotation")],
                    b$probe_map[, setdiff(names(b$probe_map), "raw_annotation")]))
''',
    )


def test_affymetrix_gene_assignment_parses_records_before_fields(tmp_path: Path) -> None:
    _run_mapping_harness(
        tmp_path,
        r'''
expr <- matrix(seq_len(6), nrow=3,
               dimnames=list(c("p_repeat", "p_amb", "p_unknown"), c("s1", "s2")))
ann <- data.frame(
  ID=c("p_repeat", "p_amb", "p_unknown"),
  gene_assignment=c(
    "NM_1 // KEEP // description A /// NM_2 // KEEP // description B",
    "NM_3 // ALPHA // description C /// NM_4 // BETA // description D",
    "--- // --- // unknown"),
  stringsAsFactors=FALSE)
got <- run_case(expr, ann)
stopifnot(identical(got$symbols, c("KEEP", NA_character_, NA_character_)))
classes <- setNames(got$probe_map$mapping_class, got$probe_map$probe)
stopifnot(identical(classes, c(p_repeat="unique", p_amb="ambiguous", p_unknown="unknown")))
candidates <- setNames(got$probe_map$candidate_gene_ids, got$probe_map$probe)
stopifnot(identical(candidates[["p_repeat"]], "KEEP"),
          identical(candidates[["p_amb"]], "ALPHA | BETA"),
          !grepl("description", paste(candidates, collapse=" "), fixed=TRUE))
''',
    )


def test_unique_probe_maxmean_and_mapping_coverage_match_independent_oracle(
    tmp_path: Path,
) -> None:
    _run_mapping_harness(
        tmp_path,
        r'''
expr <- rbind(
  p_a_low=c(2, 4), p_a_high=c(8, 10), p_amb=c(100, 100),
  p_b=c(20, 22), p_unknown=c(30, 32), p_missing=c(40, 42))
ann <- data.frame(
  ID=c("p_a_low", "p_a_high", "p_amb", "p_b", "p_unknown"),
  `Gene Symbol`=c("GENE_A", "GENE_A // GENE_A", "GENE_A /// GENE_B",
                  "GENE_B", "---\t///\nNA"),
  check.names=FALSE, stringsAsFactors=FALSE)
got <- run_case(expr, ann)
expected <- rbind(GENE_A=c(8, 10), GENE_B=c(20, 22))
colnames(expected) <- colnames(expr)
stopifnot(identical(got$gene_mat, expected))
stopifnot(length(got$map_lines) == nrow(expr) + 1L)
raw_unknown <- got$probe_map$raw_annotation[got$probe_map$probe == "p_unknown"]
stopifnot(grepl("\\\\t", raw_unknown), grepl("\\\\n", raw_unknown))
message <- got$check$messages[[1]]$message
stopifnot(identical(got$check$status, "PASS"),
          grepl("50.0% of probes", message, fixed=TRUE),
          grepl("2 unique genes", message, fixed=TRUE),
          grepl("1 ambiguous", message, fixed=TRUE),
          grepl("1 unknown", message, fixed=TRUE),
          grepl("1 missing", message, fixed=TRUE))
''',
    )


def test_local_gene_matrix_keeps_direct_identifiers(tmp_path: Path) -> None:
    _run_mapping_harness(
        tmp_path,
        r'''
expr <- rbind(GENE_2=c(3, 4), GENE_1=c(1, 2))
got <- run_case(expr, source_kind="local_matrix")
stopifnot(identical(got$symbols, rownames(expr)),
          identical(rownames(got$gene_mat), c("GENE_1", "GENE_2")),
          identical(unname(got$gene_mat["GENE_1", ]), c(1, 2)))
''',
    )


def test_local_gene_matrix_preserves_the_existing_nonempty_id_gate(tmp_path: Path) -> None:
    _run_mapping_harness(
        tmp_path,
        r'''
expr <- matrix(seq_len(8), nrow=4,
               dimnames=list(c("", NA_character_, "NA", "GENE_1"), c("s1", "s2")))
got <- run_case(expr, source_kind="local_matrix")
stopifnot(identical(got$symbols, c(NA_character_, NA_character_, "NA", "GENE_1")),
          identical(rownames(got$gene_mat), c("GENE_1", "NA")),
          identical(unname(got$gene_mat["NA", ]), c(3L, 7L)))
classes <- got$probe_map$mapping_class
stopifnot(identical(classes, c("unknown", "unknown", "direct_gene_id", "direct_gene_id")))
message <- got$check$messages[[1]]$message
stopifnot(grepl("50.0% of probes", message, fixed=TRUE),
          grepl("2 unknown", message, fixed=TRUE))
''',
    )


def test_all_unresolved_error_handler_retains_exact_exclusion_counts(tmp_path: Path) -> None:
    command, script_path, runtime_path = _r_runtime(SCRIPT, "jsonlite")
    map_check = tmp_path / "map_check.json"
    norm_check = tmp_path / "norm_check.json"
    map_output = tmp_path / "probe_map.tsv"
    code = rf'''
source_lines <- readLines({script_path!r}, warn=FALSE)
write_start <- grep("write_check <- function", source_lines, fixed=TRUE)[1]
write_end <- grep("# Progress markers", source_lines, fixed=TRUE)[1] - 1L
handler_start <- grep("options(error = function", source_lines, fixed=TRUE)[1]
handler_end <- grep("step(sprintf", source_lines, fixed=TRUE)[1] - 1L
mapping_start <- grep("# ---- 3. Probe", source_lines, fixed=TRUE)[1]
mapping_end <- grep("# ---- 5. Validate", source_lines, fixed=TRUE)[1] - 1L
stopifnot(all(!is.na(c(write_start, write_end, handler_start, handler_end,
                       mapping_start, mapping_end))))
eval(parse(text=paste(source_lines[write_start:write_end], collapse="\n")),
     envir=.GlobalEnv)
out_map <- {runtime_path(map_output)!r}
out_map_check <- {runtime_path(map_check)!r}
out_norm_check <- {runtime_path(norm_check)!r}
log_con <- file(tempfile(), open="wt")
sink(log_con, type="message")
eval(parse(text=paste(source_lines[handler_start:handler_end], collapse="\n")),
     envir=.GlobalEnv)
source_kind <- "geo_series_matrix"
exprs_mat <- matrix(seq_len(6), nrow=3,
                    dimnames=list(c("amb", "unknown", "missing"), c("s1", "s2")))
fdata <- data.frame(ID=c("amb", "unknown"),
                    `Gene Symbol`=c("ALPHA /// BETA", "---"),
                    check.names=FALSE, stringsAsFactors=FALSE)
eval(parse(text=paste(source_lines[mapping_start:mapping_end], collapse="\n")),
     envir=.GlobalEnv)
'''
    harness = tmp_path / "probe_mapping_failure.R"
    harness.write_text(code, encoding="utf-8")
    result = subprocess.run(
        [*command, runtime_path(harness)], capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert result.returncode != 0
    check = json.loads(map_check.read_text(encoding="utf-8"))
    assert check["status"] == "FAIL"
    message = check["messages"][0]["message"]
    assert "1 ambiguous" in message
    assert "1 unknown" in message
    assert "1 missing-annotation" in message
