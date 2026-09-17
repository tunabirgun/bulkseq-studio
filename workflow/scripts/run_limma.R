# Microarray differential expression with limma (0.4.0). Reads a normalized
# log2 expression matrix (from ingest_geo.R) and emits the SAME artifacts as
# run_deseq2.R (results CSV, up/down, deseq2_objects.rds, normalized, checks
# 08/09) so figures/enrichment/GOI stay backend-agnostic. The RDS carries
# assay_kind = "log2_intensity" so the figure scripts skip count-scale transforms.

# Shared engine helpers: load-warning muffling, write_check, GTF annotation, the
# featureCounts reader, contrast guards and the design-term detectors. Sourced before any
# library() call so the muffling is installed first.
source(file.path(snakemake@scriptdir, "de_common.R"))

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
check_contrast(numerator, denominator, lv, con_factor)

# ---- Design: group-means + optional additive covariates from the formula ----
# limma here is fit as ~ 0 + grp + covariates; an interaction/nesting operator would be
# silently stripped by all.vars() below and refit as a plain additive term, so refuse it
# instead of reassuring the user about a model that was never fitted.
if (has_interaction(design_formula)) {
  stop(sprintf(paste0(
    "Design formula '%s' contains an interaction or nesting operator (':', '*', '^', or '/'). ",
    "The limma engine fits an additive group-means design (~ 0 + grp + covariates) and does not ",
    "expose interaction coefficients. Use DESeq2 with an explicit design to test this term, or ",
    "drop it from the design formula."), design_formula))
}
form_vars <- tryCatch(all.vars(as.formula(design_formula)), error = function(e) character(0))
covariates <- setdiff(form_vars, con_factor)
# A design covariate absent from the sample sheet (typo/renamed column) would otherwise be
# dropped silently, running an UNadjusted (confounded) model. Fail loudly instead.
missing_cov <- setdiff(covariates, colnames(coldata))
if (length(missing_cov)) {
  stop(sprintf("Design covariate(s) not found in the sample sheet: %s", paste(missing_cov, collapse = ", ")))
}
covariates <- covariates[covariates %in% colnames(coldata)]
design_contrast <- group_means_design_contrast(
  grp, coldata, covariates, numerator, denominator)
design <- design_contrast$design

# The formula actually fitted (additive group-means), not the typed one, so the check
# message never affirms a term (e.g. an interaction) that was not fitted.
fitted_formula <- if (length(covariates)) {
  paste("~ 0 +", con_factor, "+", paste(covariates, collapse = " + "))
} else paste("~ 0 +", con_factor)
full_rank <- qr(design)$rank == ncol(design)
design_checks <- list(list(
  status = if (full_rank) "PASS" else "FAIL",
  message = if (full_rank) sprintf("Design %s is full rank.", fitted_formula)
            else sprintf("Design %s is not full rank.", fitted_formula)))
if (min(table(grp)) < 2) {
  design_checks[[length(design_checks) + 1]] <- list(status = "WARNING",
    message = "At least one condition has fewer than two replicates.")
}
design_checks <- c(design_checks, numeric_covariate_checks(coldata, covariates))
design_status <- if (!full_rank) "FAIL" else if (any(vapply(design_checks, function(m)
  identical(m$status, "REVIEW_REQUIRED"), logical(1)))) "REVIEW_REQUIRED" else "PASS"
write_check(snakemake@output[["design_check"]], "08_metadata_design_qc", design_status, design_checks)

# ---- limma fit --------------------------------------------------------------
fit <- lmFit(expr_mat, design)
cmat <- design_contrast$contrast
fit2 <- contrasts.fit(fit, cmat)
# Informational companion to the primary padj/|log2FC| call: H0 |log2FC| <= L, so adj.P < alpha is
# positive evidence the effect EXCEEDS the threshold. treat() is limma's native form of DESeq2's
# altHypothesis="greaterAbs"; it re-moderates against the threshold, so it is computed from the
# contrast fit rather than from the eBayes'd object, with the same trend/robust settings. Does not
# feed the up/down split below.
L_eq <- require_lfc_threshold(if (lfc_thr > 0) lfc_thr else 1.0)
treat_tab <- topTreat(treat(fit2, lfc = L_eq, trend = TRUE, robust = TRUE),
                      number = Inf, sort.by = "none")
fit2 <- eBayes(fit2, trend = TRUE, robust = TRUE)
# sort.by="none" keeps topTable in matrix row order (figures index assay(vsd) by
# order(res$padj) positionally; a pre-sorted res would mis-index the heatmap).
tt <- topTable(fit2, number = Inf, sort.by = "none", adjust.method = "BH")

lfc_se <- moderated_lfc_se(fit2, rownames(tt))

# Map limma columns onto the DESeq2 results schema.
res <- data.frame(
  baseMean = tt$AveExpr,
  log2FoldChange = tt$logFC,
  lfcSE = lfc_se,
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

# PC1/PC2 for the covariate-structure screen (check 23), from the log2 intensity matrix
# (run_deseq2.R uses plotPCA on its VST instead).
write_pca_coordinates(expr_mat, snakemake@output[["pca_coordinates"]])

# ---- Outputs (match run_deseq2.R) -------------------------------------------
res_out <- res
res_out$gene_id <- rownames(res_out)
res_out$symbol <- rownames(res_out)   # microarray rows are already gene symbols
res_out$biotype <- NA_character_      # not available for probe-collapsed intensities
res_out$padj_lfc_ge_threshold <- treat_tab$adj.P.Val[match(res_out$gene_id, rownames(treat_tab))]
res_out <- res_out[order(res_out$padj), ]
write.csv(res_out, snakemake@output[["results"]], row.names = FALSE)
write.csv(as.data.frame(expr_mat), snakemake@output[["normalized"]])
saveRDS(list(dds = dds, res = res, resLFC = resLFC, vsd = vsd,
             assay_kind = "log2_intensity", lfc_threshold_test = "treat",
             symbol_map = setNames(rownames(res), rownames(res))),
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

writeLines(c(sprintf("Fold-change threshold test: treat (H0 |log2FC| <= %g)", L_eq), "",
             capture.output(sessionInfo())), snakemake@output[["session"]])
sink(type = "message")
close(log_con)
