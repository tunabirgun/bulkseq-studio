from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _runtime import rscript_runtime


SCRIPTS = Path(__file__).resolve().parents[1] / "workflow" / "scripts"


def _run(tmp_path: Path, route: str, mode: str) -> subprocess.CompletedProcess[str]:
    runtime = rscript_runtime("jsonlite")
    if runtime is None:
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R") else pytest.skip)(
            "Rscript with jsonlite is required for enrichment status tests")
    command, convert = runtime
    harness = tmp_path / "enrichment_status.R"
    harness.write_text(HARNESS, encoding="utf-8")
    return subprocess.run(
        [*command, convert(harness), convert(SCRIPTS), convert(tmp_path), route, mode],
        capture_output=True, text=True, timeout=60, check=False,
    )


HARNESS = r'''
args <- commandArgs(trailingOnly=TRUE)
scripts <- args[[1]]
work <- args[[2]]
route <- args[[3]]
mode <- args[[4]]
setClass("EnrichmentRun", slots=c(input="list", output="list", params="list",
                                  log="list", scriptdir="character"))
output <- as.list(setNames(file.path(work, c("ora.csv", "gsea.csv", "objects.rds",
                                           "summary.txt", "check.json", "mapping.tsv")),
                           c("ora", "gsea", "objects", "summary", "check", "mapping")))
res <- data.frame(gene_id=paste0("g", 1:12), log2FoldChange=rep(c(2, -2), each=6),
                  pvalue=0.001, padj=0.01, study_a_log2FC=rep(c(2, -2), each=6),
                  study_a_padj=0.01, meta_sig=TRUE,
                  common_direction=rep(c("up", "down"), each=6))
results <- file.path(work, "results.csv")
up <- file.path(work, "up.csv")
down <- file.path(work, "down.csv")
annotation <- file.path(work, "annotation.tsv")
write.csv(res, results, row.names=FALSE)
write.csv(res[1:6, "gene_id", drop=FALSE], up, row.names=FALSE)
write.csv(res[7:12, "gene_id", drop=FALSE], down, row.names=FALSE)
write.table(data.frame(gene=res$gene_id, term="set1"), annotation,
            sep="\t", row.names=FALSE, quote=FALSE)
snakemake <- new("EnrichmentRun", input=list(results=results, up=up, down=down),
                 output=output, params=list(gmt="", annot=annotation, background="",
                   alpha=0.05, keytype="SYMBOL", orgdb="synthetic_orgdb", ont="BP",
                   lfc_threshold=1), log=list(file.path(work, "run.log")), scriptdir=scripts)
library <- require <- function(...) TRUE
synthetic_orgdb <- list()
calls <- character(0)
engine_result <- function(stage) {
  calls <<- c(calls, stage)
  if (mode %in% c(stage, "both")) stop(paste("injected", stage, 'failure: "quoted"\\path\nnext line'))
  if (identical(mode, paste0(stage, "_conversion")))
    return(structure(list(), class="broken_enrichment"))
  if (mode %in% c("empty", "null")) {
    if (mode == "null") return(NULL)
    return(data.frame())
  }
  data.frame(Cluster="a_up", ID="set1", Description="Synthetic set", p.adjust=0.01)
}
as.data.frame.broken_enrichment <- function(...) stop("injected result conversion failure")
enricher <- function(...) engine_result("ORA")
GSEA <- function(...) engine_result("GSEA")
compareCluster <- function(...) engine_result("compareCluster")
setReadable <- function(x, ...) x
quit <- function(...) stop(structure(list(message="completed skip", call=NULL),
                                     class=c("completed_skip", "error", "condition")))
path <- file.path(scripts, paste0("run_", if (route == "custom") "custom" else "meta", "_enrichment.R"))
exprs <- parse(file=path)
tryCatch({
  for (expr in exprs[-1]) {
    eval(expr, .GlobalEnv)
    if (is.call(expr) && identical(expr[[1]], as.name("source"))) {
      map_ids_with_routing <- function(ids, ...) list(
        map=data.frame(input_id=ids, ENTREZID=as.character(seq_along(ids)),
                       keytype="SYMBOL", resolution="direct"),
        exclusions=data.frame(input_id=character(), reason=character(),
                              routed_keytype=character(), candidate_entrez=character()),
        mapped_inputs=length(ids), total_inputs=length(ids), ambiguous_excluded=0L,
        unmapped_inputs=0L, requested_keytype="SYMBOL", effective_keytype="SYMBOL")
    }
  }
}, completed_skip=function(e) NULL)
check <- jsonlite::read_json(output$check, simplifyVector=FALSE)
text <- paste(unlist(check), collapse=" ")
failed <- mode %in% c("ORA", "GSEA", "both", "compareCluster") || grepl("_conversion$", mode)
stopifnot(identical(check$status, if (failed) "REVIEW_REQUIRED" else "PASS"))
if (failed) {
  stopifnot(grepl("unavailable", text, fixed=TRUE),
            grepl("injected", text, fixed=TRUE),
            !grepl("no GO terms passed", text, fixed=TRUE))
}
if (route == "custom") {
  stopifnot(identical(calls, c("ORA", "GSEA")))
  summary <- readLines(output$summary)
  for (stage in c("ORA", "GSEA")) {
    label <- if (stage == "ORA") "Custom ORA terms: " else "Custom GSEA sets: "
    expected <- if (mode %in% c(stage, paste0(stage, "_conversion"), "both")) "unavailable" else
      if (mode %in% c("empty", "null")) "0" else "1"
    stopifnot(paste0(label, expected) %in% summary)
    if (expected == "1") stopifnot(nrow(read.csv(output[[tolower(stage)]])) == 1L)
  }
} else {
  stopifnot(identical(calls, "compareCluster"))
  if (mode == "success") stopifnot(nrow(read.csv(output$ora)) == 1L)
}
cat(route, mode, check$status, "\n")
'''


@pytest.mark.parametrize("mode", ["ORA", "GSEA", "both", "ORA_conversion", "GSEA_conversion",
                                  "empty", "null", "success"])
def test_custom_enrichment_execution_status(tmp_path: Path, mode: str) -> None:
    result = _run(tmp_path, "custom", mode)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["compareCluster", "compareCluster_conversion", "empty", "null", "success"])
def test_meta_enrichment_execution_status(tmp_path: Path, mode: str) -> None:
    result = _run(tmp_path, "meta", mode)
    assert result.returncode == 0, result.stdout + result.stderr
