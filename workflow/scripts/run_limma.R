# Microarray differential expression with limma (0.4.0). Reads a normalized
# log2 expression matrix (from ingest_geo.R) and emits the SAME artifacts as
# run_deseq2.R (results CSV, up/down, deseq2_objects.rds, normalized, checks
# 08/09) so figures/enrichment/GOI stay backend-agnostic. The RDS carries
# assay_kind = "log2_intensity" so the figure scripts skip count-scale transforms.

suppressMessages({
  library(limma)
  library(DESeq2)  # only for DESeqTransform/SummarizedExperiment wrappers
})

set.seed(42)

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

expr_file <- snakemake@input[["expression"]]
samples_file <- snakemake@input[["samples"]]
design_formula <- snakemake@params[["design"]]
con_factor <- snakemake@params[["contrast_factor"]]
numerator <- snakemake@params[["numerator"]]
denominator <- snakemake@params[["denominator"]]
alpha <- as.numeric(snakemake@params[["alpha"]])
lfc_thr <- as.numeric(snakemake@params[["lfc_threshold"]])
if (is.na(lfc_thr) || lfc_thr < 0) {
  stop("deseq2.lfc_threshold must be a number >= 0 (0 disables the fold-change filter).")
}

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

# ---- Expression matrix (genes x samples, log2 intensities) ------------------
expr <- read.delim(expr_file, check.names = FALSE)
rownames(expr) <- as.character(expr[[1]])
expr_mat <- as.matrix(expr[, -1, drop = FALSE])
mode(expr_mat) <- "numeric"

# ---- Sample metadata --------------------------------------------------------
samples <- read.delim(samples_file, stringsAsFactors = FALSE)
rownames(samples) <- samples$sample_id
common <- intersect(colnames(expr_mat), rownames(samples))
if (length(common) < 2) {
  stop("Fewer than two samples match between the expression matrix and samples.tsv.")
}
expr_mat <- expr_mat[, common, drop = FALSE]
coldata <- samples[common, , drop = FALSE]
stopifnot(all(rownames(coldata) == colnames(expr_mat)))

if (!nzchar(con_factor) || !(con_factor %in% colnames(coldata))) {
  stop(sprintf("Contrast factor '%s' is not a column in the sample sheet (columns: %s).",
               con_factor, paste(colnames(coldata), collapse = ", ")))
}
coldata[[con_factor]] <- factor(coldata[[con_factor]])
grp <- coldata[[con_factor]]
lv <- levels(grp)

# Contrast guards (mirror run_deseq2.R).
if (!nzchar(numerator) || !nzchar(denominator)) stop("Contrast numerator and denominator must both be set.")
if (identical(numerator, denominator)) stop("Contrast numerator and denominator must differ.")
if (!(numerator %in% lv) || !(denominator %in% lv)) {
  stop(sprintf("Contrast levels '%s'/'%s' not found in factor '%s' (levels: %s).",
               numerator, denominator, con_factor, paste(lv, collapse = ", ")))
}

# ---- Design: group-means + optional additive covariates from the formula ----
form_vars <- tryCatch(all.vars(as.formula(design_formula)), error = function(e) character(0))
covariates <- setdiff(form_vars, con_factor)
covariates <- covariates[covariates %in% colnames(coldata)]
level_names <- make.names(lv)
if (length(covariates)) {
  cov_terms <- paste(covariates, collapse = " + ")
  design <- model.matrix(as.formula(paste("~ 0 + grp +", cov_terms)), data = coldata)
} else {
  design <- model.matrix(~ 0 + grp)
}
colnames(design)[seq_along(lv)] <- level_names

full_rank <- qr(design)$rank == ncol(design)
design_checks <- list(list(
  status = if (full_rank) "PASS" else "FAIL",
  message = if (full_rank) sprintf("Design %s is full rank.", design_formula)
            else sprintf("Design %s is not full rank.", design_formula)))
if (min(table(grp)) < 2) {
  design_checks[[length(design_checks) + 1]] <- list(status = "WARNING",
    message = "At least one condition has fewer than two replicates.")
}
write_check(snakemake@output[["design_check"]], "08_metadata_design_qc",
            if (full_rank) "PASS" else "FAIL", design_checks)

# ---- limma fit --------------------------------------------------------------
fit <- lmFit(expr_mat, design)
contrast_str <- paste0(make.names(numerator), " - ", make.names(denominator))
cmat <- makeContrasts(contrasts = contrast_str, levels = design)
fit2 <- contrasts.fit(fit, cmat)
fit2 <- eBayes(fit2, trend = TRUE, robust = TRUE)
# sort.by="none" keeps topTable in matrix row order (figures index assay(vsd) by
# order(res$padj) positionally; a pre-sorted res would mis-index the heatmap).
tt <- topTable(fit2, number = Inf, sort.by = "none")

# Map limma columns onto the DESeq2 results schema.
res <- data.frame(
  baseMean = tt$AveExpr,
  log2FoldChange = tt$logFC,
  lfcSE = NA_real_,
  stat = tt$t,
  pvalue = tt$P.Value,
  padj = tt$adj.P.Val,
  row.names = rownames(tt),
  check.names = FALSE,
  stringsAsFactors = FALSE
)
resLFC <- res  # limma has no separate shrinkage step

# ---- DESeqTransform wrapper so make_figures/make_goi generics work -----------
se <- SummarizedExperiment(assays = list(intensity = expr_mat),
                           colData = S4Vectors::DataFrame(coldata))
vsd <- DESeqTransform(se)
dds <- vsd  # same object; make_goi guards counts(dds) -> assay(vsd) for microarray
# Invariant the figure scripts rely on: res is in the same row order as the
# expression matrix (sort.by="none"). Fail loudly here if a refactor breaks it.
stopifnot(identical(rownames(SummarizedExperiment::assay(vsd)), rownames(res)))

# ---- Outputs (match run_deseq2.R) -------------------------------------------
res_out <- res
res_out$gene_id <- rownames(res_out)
res_out <- res_out[order(res_out$padj), ]
write.csv(res_out, snakemake@output[["results"]], row.names = FALSE)
write.csv(as.data.frame(expr_mat), snakemake@output[["normalized"]])
saveRDS(list(dds = dds, res = res, resLFC = resLFC, vsd = vsd,
             assay_kind = "log2_intensity"),
        snakemake@output[["rds"]])

sig <- !is.na(res_out$padj) & res_out$padj < alpha
up <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange >= lfc_thr, ]
down <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange <= -lfc_thr, ]
up <- up[order(-up$log2FoldChange), ]
down <- down[order(down$log2FoldChange), ]
write.csv(up, snakemake@output[["up"]], row.names = FALSE)
write.csv(down, snakemake@output[["down"]], row.names = FALSE)

n_sig <- sum(sig)
deseq_checks <- list(list(status = if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
  message = sprintf("%d genes adj.P < %.3g (%s_%s_vs_%s, limma); %d up / %d down at |log2FC| >= %.2g.",
                    n_sig, alpha, con_factor, numerator, denominator, nrow(up), nrow(down), lfc_thr)))
write_check(snakemake@output[["deseq_check"]], "09_deseq2_qc",
            if (n_sig > 0) "PASS" else "REVIEW_REQUIRED", deseq_checks)

writeLines(capture.output(sessionInfo()), snakemake@output[["session"]])
sink(type = "message")
close(log_con)
