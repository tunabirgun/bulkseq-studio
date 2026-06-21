# Downstream-interoperability exports from the existing DE artifacts:
#   - vst_matrix.csv: the homoscedastic expression matrix (VST counts, or log2
#     intensities on the microarray backend) that most external tools expect,
#     currently locked inside deseq2_objects.rds.
#   - ranked_genes.rnk: a stat-ranked gene list for preranked GSEA / MSigDB.
# Both backends are supported (assay(vsd) and the `stat` column exist for each).

suppressMessages({
  library(SummarizedExperiment)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
vsd <- obj$vsd

# Normalized expression matrix -> CSV (gene_id + one column per sample).
mat <- as.data.frame(assay(vsd))
mat <- cbind(gene_id = rownames(mat), mat)
write.csv(mat, snakemake@output[["vst"]], row.names = FALSE)

# Preranked .rnk: gene_id <tab> stat, descending, NA dropped (no header per the
# GSEA .rnk spec).
res <- read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE, check.names = FALSE)
gene_id <- if ("gene_id" %in% names(res)) res$gene_id else res[[1]]
score <- if ("stat" %in% names(res)) res$stat else NA_real_
keep <- !is.na(gene_id) & !is.na(score)
rnk <- data.frame(gene = gene_id[keep], stat = score[keep])
rnk <- rnk[order(-rnk$stat), ]
write.table(rnk, snakemake@output[["rnk"]], sep = "\t", quote = FALSE,
            row.names = FALSE, col.names = FALSE)

sink(type = "message")
close(log_con)
