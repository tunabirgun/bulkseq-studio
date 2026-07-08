# limma-voom differential expression (optional engine, count-based routes only).
# Reads the RAW featureCounts matrix and applies the standard limma-voom recipe
# (filterByExpr -> TMM normalization -> voom -> lmFit -> eBayes robust). voom models
# the count mean-variance relationship, so it consumes raw counts, NOT normalized
# values. Emits the SAME artifacts as run_deseq2.R (results CSV, up/down,
# deseq2_objects.rds, normalized, checks 08/09) so figures/enrichment/GOI stay
# backend-agnostic. DESeq2 remains the default engine; this is an opt-in
# cross-check. The equivalence/no-change (TOST) test is DESeq2-specific and is not
# produced here (check 13 / unchanged_genes are gated off for this engine).
#
# The RDS carries assay_kind = "log2_cpm" so the figure scripts treat the voom
# logCPM matrix as a log-scale expression matrix (linear MA x-axis, no count-scale
# transforms) and skip the DESeq2-only model diagnostics.

suppressMessages({
  library(limma)
  library(edgeR)
  library(DESeq2)  # DESeqTransform/SummarizedExperiment wrappers for the shared figure scripts
})

set.seed(42)

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

counts_file <- snakemake@input[["counts"]]
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

# Parse gene_id -> (gene_name, gene_biotype) from a GTF attribute column and align
# to `gene_ids` (NA where unknown). Identical to run_deseq2.R so the results CSV
# and figure/GOI labels match across engines; returns all-NA when the GTF is absent
# (e.g. count-matrix mode has no reference).
annotate_from_gtf <- function(gtf_path, gene_ids) {
  na_vec <- setNames(rep(NA_character_, length(gene_ids)), gene_ids)
  if (is.null(gtf_path) || length(gtf_path) < 1 || !nzchar(gtf_path[[1]]) ||
      !file.exists(gtf_path[[1]])) {
    return(list(symbol = na_vec, biotype = na_vec))
  }
  gtf <- tryCatch(
    read.delim(gtf_path[[1]], header = FALSE, sep = "\t", quote = "", comment.char = "#",
               colClasses = c("NULL", "NULL", "character", "NULL", "NULL",
                              "NULL", "NULL", "NULL", "character")),
    error = function(e) NULL)
  if (is.null(gtf) || ncol(gtf) < 2) return(list(symbol = na_vec, biotype = na_vec))
  names(gtf) <- c("feature", "attr")
  g <- gtf[gtf$feature == "gene", , drop = FALSE]
  if (nrow(g) == 0) g <- gtf  # some GTFs (e.g. minimal RefSeq) lack a gene feature
  a <- g$attr
  pull <- function(key) ifelse(grepl(paste0(key, ' "'), a),
                               sub(paste0('.*', key, ' "([^"]+)".*'), "\\1", a), NA_character_)
  gid <- pull("gene_id")
  sym <- pull("gene_name")
  bt <- pull("gene_biotype")
  gt <- pull("gene_type")           # GENCODE uses gene_type; Ensembl gene_biotype
  bt[is.na(bt)] <- gt[is.na(bt)]
  keep <- !is.na(gid) & !duplicated(gid)
  gid <- gid[keep]; sym <- sym[keep]; bt <- bt[keep]
  idx <- match(gene_ids, gid)
  list(symbol = setNames(sym[idx], gene_ids), biotype = setNames(bt[idx], gene_ids))
}

# ---- Import featureCounts matrix (RAW counts; voom models the count mean-variance) ----
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
if (!nzchar(con_factor) || !(con_factor %in% colnames(coldata))) {
  stop(sprintf("Contrast factor '%s' is not a column in the sample sheet (columns: %s).",
               con_factor, paste(colnames(coldata), collapse = ", ")))
}
coldata[[con_factor]] <- factor(coldata[[con_factor]])
grp <- coldata[[con_factor]]
lv <- levels(grp)

# Contrast guards (mirror run_deseq2.R / run_limma.R).
if (!nzchar(numerator) || !nzchar(denominator)) stop("Contrast numerator and denominator must both be set.")
if (identical(numerator, denominator)) stop("Contrast numerator and denominator must differ.")
if (!(numerator %in% lv) || !(denominator %in% lv)) {
  stop(sprintf("Contrast levels '%s'/'%s' not found in factor '%s' (levels: %s).",
               numerator, denominator, con_factor, paste(lv, collapse = ", ")))
}

# ---- Design: group-means + optional additive covariates from the formula ----
# Same parameterization as run_limma.R: an explicit numerator-vs-denominator
# contrast (reference level is irrelevant here) with additive covariate adjustment.
form_vars <- tryCatch(all.vars(as.formula(design_formula)), error = function(e) character(0))
covariates <- setdiff(form_vars, con_factor)
# A design covariate absent from the sample sheet (typo/renamed column) would otherwise be
# dropped silently, running an UNadjusted (confounded) model. Fail loudly instead.
missing_cov <- setdiff(covariates, colnames(coldata))
if (length(missing_cov)) {
  stop(sprintf("Design covariate(s) not found in the sample sheet: %s", paste(missing_cov, collapse = ", ")))
}
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

# ---- limma-voom fit (standard recipe: filterByExpr -> TMM -> voom -> eBayes) ----
dge <- DGEList(counts = cts, group = grp)
keep <- filterByExpr(dge, design)          # method's standard low-expression filter
dge <- dge[keep, , keep.lib.sizes = FALSE]
dge <- calcNormFactors(dge)                # TMM between-sample normalization
v <- voom(dge, design)                     # logCPM + precision weights
fit <- lmFit(v, design)
contrast_str <- paste0(make.names(numerator), " - ", make.names(denominator))
cmat <- makeContrasts(contrasts = contrast_str, levels = design)
fit2 <- contrasts.fit(fit, cmat)
# voom already supplies the mean-variance weights, so eBayes uses trend = FALSE
# (the default); robust = TRUE guards against hyperparameter outliers.
fit2 <- eBayes(fit2, robust = TRUE)
# sort.by="none" keeps topTable in matrix row order (figures index assay(vsd) by
# order(res$padj) positionally; a pre-sorted res would mis-index the heatmap).
tt <- topTable(fit2, number = Inf, sort.by = "none")

# Map limma columns onto the DESeq2 results schema. baseMean = average log2-CPM.
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
resLFC <- res  # voom has no separate shrinkage step

# ---- DESeqTransform wrapper (voom logCPM) so make_figures/make_goi generics work ----
logcpm <- v$E  # genes x samples, log2-CPM
se <- SummarizedExperiment(assays = list(logcpm = logcpm),
                           colData = S4Vectors::DataFrame(coldata))
vsd <- DESeqTransform(se)
dds <- vsd  # same object; count-model diagnostics degrade to placeholders (assay_kind log2_cpm)
# Invariant the figure scripts rely on: res is in the same row order as the
# expression matrix (sort.by="none"). Fail loudly here if a refactor breaks it.
stopifnot(identical(rownames(SummarizedExperiment::assay(vsd)), rownames(res)))

# ---- Gene annotation (symbol + biotype from the GTF; NA when absent) --------
gtf_path <- tryCatch(snakemake@params[["gtf"]], error = function(e) NULL)
annot <- annotate_from_gtf(gtf_path, rownames(res))

# ---- Outputs (match run_deseq2.R / run_limma.R) -----------------------------
res_out <- res
res_out$gene_id <- rownames(res_out)
res_out$symbol <- unname(annot$symbol[rownames(res_out)])
res_out$biotype <- unname(annot$biotype[rownames(res_out)])
res_out <- res_out[order(res_out$padj), ]
write.csv(res_out, snakemake@output[["results"]], row.names = FALSE)
write.csv(as.data.frame(logcpm), snakemake@output[["normalized"]])
saveRDS(list(dds = dds, res = res, resLFC = resLFC, vsd = vsd,
             assay_kind = "log2_cpm",
             symbol_map = annot$symbol),
        snakemake@output[["rds"]])

# Up- and down-regulated sets: padj < alpha AND a raw-LFC effect-size cut (same
# guardrail cut as run_deseq2.R; voom's logFC is already the reported effect size).
sig <- !is.na(res_out$padj) & res_out$padj < alpha
up <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange >= lfc_thr, ]
down <- res_out[sig & !is.na(res_out$log2FoldChange) & res_out$log2FoldChange <= -lfc_thr, ]
up <- up[order(-up$log2FoldChange), ]
down <- down[order(down$log2FoldChange), ]
write.csv(up, snakemake@output[["up"]], row.names = FALSE)
write.csv(down, snakemake@output[["down"]], row.names = FALSE)

n_sig <- sum(sig)
deseq_checks <- list(list(status = if (n_sig > 0) "PASS" else "REVIEW_REQUIRED",
  message = sprintf("%d genes adj.P < %.3g (%s_%s_vs_%s, limma-voom); %d up / %d down at |log2FC| >= %.2g.",
                    n_sig, alpha, con_factor, numerator, denominator, nrow(up), nrow(down), lfc_thr)))
write_check(snakemake@output[["deseq_check"]], "09_deseq2_qc",
            if (n_sig > 0) "PASS" else "REVIEW_REQUIRED", deseq_checks)

writeLines(capture.output(sessionInfo()), snakemake@output[["session"]])
sink(type = "message")
close(log_con)
