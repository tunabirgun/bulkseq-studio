args <- commandArgs(trailingOnly = TRUE)
root <- if (length(args)) args[[1]] else "."
model <- jsonlite::fromJSON(file.path(root, "docs_src", "model-examples.json"), simplifyVector = FALSE)
report <- utils::read.csv(file.path(root, "docs", "examples", "standard", "results", "deseq2", "deseq2_results.csv"), check.names = FALSE)
ids <- vapply(model$models$deseq2, `[[`, character(1), "id")
counts <- do.call(rbind, lapply(model$counts, unlist))
rownames(counts) <- model$genes
counts <- counts[ids, , drop = FALSE]
factors <- DESeq2::estimateSizeFactorsForMatrix(counts)
expected <- rowMeans(sweep(counts, 2, factors, "/"))
observed <- report$baseMean[match(ids, report$gene_id)]
if (!(length(observed) == length(expected) && all(is.finite(observed)) && all(abs(observed - expected) < 1e-10))) {
  stop(sprintf("baseMean mismatch: maximum absolute difference %.16g", max(abs(observed - expected))), call. = FALSE)
}
cat("STANDARD_REPORT_BASEMEAN_PASS\n")
