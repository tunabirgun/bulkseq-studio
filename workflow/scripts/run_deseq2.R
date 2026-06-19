# DESeq2 differential expression (protocol sections 7.1-7.4).
# Driven by the Snakemake `script:` directive via the `snakemake` S4 object.

suppressMessages({
  library(DESeq2)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

counts_file <- snakemake@input[["counts"]]
samples_file <- snakemake@input[["samples"]]
design_formula <- snakemake@params[["design"]]
ref_factor <- snakemake@params[["ref_factor"]]
ref_level <- snakemake@params[["ref_level"]]
con_factor <- snakemake@params[["contrast_factor"]]
numerator <- snakemake@params[["numerator"]]
denominator <- snakemake@params[["denominator"]]
alpha <- as.numeric(snakemake@params[["alpha"]])
lfc_thr <- as.numeric(snakemake@params[["lfc_threshold"]])
shrink_type <- snakemake@params[["shrink"]]

write_check <- function(path, name, status, messages) {
  esc <- function(s) gsub('"', '\\\\"', s)
  msg_json <- paste0(
    sprintf('    {"status": "%s", "message": "%s"}', vapply(messages, `[[`, "", "status"),
            vapply(lapply(messages, `[[`, "message"), esc, "")),
    collapse = ",\n")
  json <- sprintf('{\n  "check": "%s",\n  "status": "%s",\n  "messages": [\n%s\n  ]\n}',
                  name, status, msg_json)
  writeLines(json, path)
}

# ---- Import featureCounts matrix --------------------------------------------
fc <- read.delim(counts_file, comment.char = "#", check.names = FALSE)
rownames(fc) <- fc$Geneid
cts <- as.matrix(fc[, -(1:6)])
mode(cts) <- "integer"
# featureCounts names columns by BAM path; reduce to sample_id.
colnames(cts) <- sub("_Aligned.sortedByCoord.out.bam$", "", basename(colnames(cts)))

# ---- Sample metadata --------------------------------------------------------
samples <- read.delim(samples_file, stringsAsFactors = FALSE)
rownames(samples) <- samples$sample_id
coldata <- samples[colnames(cts), , drop = FALSE]
stopifnot(all(rownames(coldata) == colnames(cts)))
coldata[[con_factor]] <- factor(coldata[[con_factor]])

design_checks <- list()
full_rank <- TRUE
tryCatch({
  mm <- model.matrix(as.formula(design_formula), data = coldata)
  if (qr(mm)$rank < ncol(mm)) full_rank <- FALSE
}, error = function(e) { full_rank <<- FALSE })
design_checks[[1]] <- list(status = if (full_rank) "PASS" else "FAIL",
                           message = if (full_rank) sprintf("Design %s is full rank.", design_formula)
                                     else sprintf("Design %s is not full rank.", design_formula))
n_per_group <- table(coldata[[con_factor]])
if (min(n_per_group) < 2) {
  design_checks[[length(design_checks) + 1]] <- list(status = "WARNING",
    message = "At least one condition has fewer than two replicates.")
}
write_check(snakemake@output[["design_check"]], "08_metadata_design_qc",
            if (full_rank) "PASS" else "FAIL", design_checks)

# ---- DESeq2 -----------------------------------------------------------------
dds <- DESeqDataSetFromMatrix(countData = cts, colData = coldata,
                              design = as.formula(design_formula))
smallest_group <- min(table(coldata[[con_factor]]))
keep <- rowSums(counts(dds) >= 10) >= smallest_group
dds <- dds[keep, ]
if (nzchar(ref_level) && ref_factor %in% colnames(coldata)) {
  dds[[ref_factor]] <- relevel(factor(dds[[ref_factor]]), ref = ref_level)
}
dds <- DESeq(dds)

res <- results(dds, contrast = c(con_factor, numerator, denominator), alpha = alpha)
coef_name <- paste0(con_factor, "_", numerator, "_vs_", denominator)
resLFC <- tryCatch(
  lfcShrink(dds, coef = coef_name, type = shrink_type),
  error = function(e) lfcShrink(dds, contrast = c(con_factor, numerator, denominator), type = "ashr"))

vsd <- tryCatch(vst(dds, blind = FALSE), error = function(e) rlog(dds, blind = FALSE))

# ---- Outputs ----------------------------------------------------------------
res_out <- as.data.frame(res)
res_out$gene_id <- rownames(res_out)
res_out <- res_out[order(res_out$padj), ]
write.csv(res_out, snakemake@output[["results"]], row.names = FALSE)
write.csv(as.data.frame(counts(dds, normalized = TRUE)), snakemake@output[["normalized"]])
saveRDS(list(dds = dds, res = res, resLFC = resLFC, vsd = vsd),
        snakemake@output[["rds"]])

# Up- and down-regulated sets: padj < alpha AND a raw-LFC effect-size cut
# (protocol: threshold on padj + res$log2FoldChange, not the shrunken values).
sig <- !is.na(res_out$padj) & res_out$padj < alpha
up <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange >= lfc_thr, ]
down <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange <= -lfc_thr, ]
up <- up[order(-up$log2FoldChange), ]
down <- down[order(down$log2FoldChange), ]
write.csv(up, snakemake@output[["up"]], row.names = FALSE)
write.csv(down, snakemake@output[["down"]], row.names = FALSE)

n_sig <- sum(sig)
deseq_checks <- list(list(status = if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
                          message = sprintf("%d genes padj < %.3g (%s); %d up / %d down at |log2FC| >= %.2g.",
                                            n_sig, alpha, coef_name, nrow(up), nrow(down), lfc_thr)))
write_check(snakemake@output[["deseq_check"]], "09_deseq2_qc",
            if (n_sig > 0) "PASS" else "REVIEW_REQUIRED", deseq_checks)

writeLines(capture.output(sessionInfo()), snakemake@output[["session"]])
sink(type = "message")
close(log_con)
