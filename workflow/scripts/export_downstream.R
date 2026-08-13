# Muffle only the benign "package X was built under R version 4.5.3" load warning: the r45 ABI
# is stable, so the 4.5.3-built conda packages run correctly under the pinned r-base 4.5.2;
# real warnings still surface. Shadow library()/require() so it works under Snakemake's
# script runner at any call-stack depth (a top-level globalCallingHandlers does not).
# Aligning r-base to 4.5.3 would force salmon off 1.10.3 onto the 2.x Rust rewrite, so we
# muffle the harmless warning instead of changing the benchmarked environment.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

# Downstream-interoperability exports from the existing DE artifacts:
#   - vst_matrix.csv: the homoscedastic expression matrix (VST counts, or log2
#     intensities on the microarray backend) that most external tools expect,
#     currently locked inside deseq2_objects.rds.
#   - ranked_genes.rnk: a signed gene list for preranked GSEA / MSigDB.
# Locally fitted models use their test statistic. Results-only imports instead use the supplied
# log2FC directly: its direction was explicitly confirmed, while a source `stat` may use an
# undocumented scale. ingest_deseq2_results.R rejects any supplied statistic with a contrary sign.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
vsd <- obj$vsd

# Normalized expression matrix -> CSV (gene_id + one column per sample). The
# bring-your-own DESeq2-results mode has no per-sample matrix (vsd is NULL), so a
# header-only stub keeps the declared output present; the matrix is gated out of
# final_targets in that mode. The .rnk below still works (it reads the results CSV).
if (is.null(vsd)) {
  writeLines("gene_id", snakemake@output[["vst"]])
} else {
  if (!requireNamespace("SummarizedExperiment", quietly = TRUE)) {
    stop("The required R package 'SummarizedExperiment' is unavailable for matrix export.")
  }
  mat <- as.data.frame(SummarizedExperiment::assay(vsd))
  mat <- cbind(gene_id = rownames(mat), mat)
  write.csv(mat, snakemake@output[["vst"]], row.names = FALSE)
}

# Preranked .rnk: gene_id <tab> signed score, descending, NA dropped (no header per the
# GSEA .rnk spec). External results use log2FC so the ranking cannot contradict the confirmed
# source direction; local DE routes retain their model statistic.
res <- read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE, check.names = FALSE)
gene_id <- if ("gene_id" %in% names(res)) res$gene_id else res[[1]]
results_only <- identical(obj$assay_kind, "results_only")
score <- if (results_only && "log2FoldChange" %in% names(res)) {
  res$log2FoldChange
} else if ("stat" %in% names(res)) {
  res$stat
} else {
  NA_real_
}
keep <- !is.na(gene_id) & !is.na(score)
rnk <- data.frame(gene = gene_id[keep], stat = score[keep])
rnk <- rnk[order(-rnk$stat), ]
write.table(rnk, snakemake@output[["rnk"]], sep = "\t", quote = FALSE,
            row.names = FALSE, col.names = FALSE)

sink(type = "message")
close(log_con)
