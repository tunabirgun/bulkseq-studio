# DESeq2 differential expression (protocol sections 7.1-7.4).
# Driven by the Snakemake `script:` directive via the `snakemake` S4 object.

suppressMessages({
  library(DESeq2)
})

# Reproducibility: seed any stochastic step (e.g. the ashr shrinkage fallback).
set.seed(42)

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
min_count <- tryCatch(as.integer(snakemake@params[["min_count"]]), error = function(e) NA_integer_)
if (length(min_count) != 1 || is.na(min_count) || min_count < 0) min_count <- 10L

# Effect-size threshold: >= 0 is valid (0 means no fold-change filter).
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

# Parse gene_id -> (gene_name, gene_biotype) from a GTF attribute column and
# align to `gene_ids` (NA where unknown). Dependency-free regex parse; returns
# all-NA when the GTF is absent (e.g. count-matrix mode has no reference).
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
# Guard: the contrast factor must be a real column in the sample sheet.
if (!nzchar(con_factor) || !(con_factor %in% colnames(coldata))) {
  stop(sprintf("Contrast factor '%s' is not a column in the sample sheet (columns: %s).",
               con_factor, paste(colnames(coldata), collapse = ", ")))
}
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
# Prefilter: keep genes with >= min_count reads in at least the smallest group
# (default 10; a light, recommended DESeq2 prefilter).
keep <- rowSums(counts(dds) >= min_count) >= smallest_group
dds <- dds[keep, ]
if (nzchar(ref_level) && ref_factor %in% colnames(coldata)) {
  dds[[ref_factor]] <- relevel(factor(dds[[ref_factor]]), ref = ref_level)
}
dds <- DESeq(dds)

# Guard: contrast levels must be set, distinct, and present in the factor.
.lv <- levels(coldata[[con_factor]])
if (!nzchar(numerator) || !nzchar(denominator)) {
  stop("Contrast numerator and denominator must both be set.")
}
if (identical(numerator, denominator)) {
  stop("Contrast numerator and denominator must differ.")
}
if (!(numerator %in% .lv) || !(denominator %in% .lv)) {
  stop(sprintf("Contrast levels '%s'/'%s' not found in factor '%s' (levels: %s).",
               numerator, denominator, con_factor, paste(.lv, collapse = ", ")))
}
res <- results(dds, contrast = c(con_factor, numerator, denominator), alpha = alpha)
coef_name <- paste0(con_factor, "_", numerator, "_vs_", denominator)
resLFC <- tryCatch(
  lfcShrink(dds, coef = coef_name, type = shrink_type),
  error = function(e) {
    warning(sprintf("lfcShrink type='%s' failed (%s); falling back to ashr.",
                    shrink_type, conditionMessage(e)))
    lfcShrink(dds, contrast = c(con_factor, numerator, denominator), type = "ashr")
  })

vsd <- tryCatch(vst(dds, blind = FALSE), error = function(e) rlog(dds, blind = FALSE))

# ---- Gene annotation (symbol + biotype from the GTF) ------------------------
# Adds human-readable columns to the results CSV and a gene_id->symbol map the
# figure/GOI scripts use for labels. Behaviour-preserving: DE statistics, row
# order, and the up/down cutoffs below are unchanged.
gtf_path <- tryCatch(snakemake@params[["gtf"]], error = function(e) NULL)
annot <- annotate_from_gtf(gtf_path, rownames(res))

# ---- Outputs ----------------------------------------------------------------
res_out <- as.data.frame(res)
res_out$gene_id <- rownames(res_out)
res_out$symbol <- unname(annot$symbol[rownames(res_out)])
res_out$biotype <- unname(annot$biotype[rownames(res_out)])
res_out <- res_out[order(res_out$padj), ]
write.csv(res_out, snakemake@output[["results"]], row.names = FALSE)
write.csv(as.data.frame(counts(dds, normalized = TRUE)), snakemake@output[["normalized"]])
saveRDS(list(dds = dds, res = res, resLFC = resLFC, vsd = vsd,
             symbol_map = annot$symbol),
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

# ---- Equivalence / no-change test (TOST-style) ------------------------------
# results(altHypothesis="lessAbs") tests H0: |LFC| >= L; padj < alpha is positive
# evidence the gene's effect is SMALLER than L (not differentially expressed),
# complementing the usual "is it different" test. lessAbs needs a POSITIVE
# threshold; lfc_thr may be 0 (the filter is disabled), so fall back to 1.0.
L_eq <- if (lfc_thr > 0) lfc_thr else 1.0
res_eq <- results(dds, contrast = c(con_factor, numerator, denominator),
                  lfcThreshold = L_eq, altHypothesis = "lessAbs", alpha = alpha)
eq_out <- as.data.frame(res_eq)
eq_out$gene_id <- rownames(eq_out)
eq_out$symbol <- unname(annot$symbol[rownames(eq_out)])
eq_out <- eq_out[!is.na(eq_out$padj) & eq_out$padj < alpha, ]
eq_out <- eq_out[order(eq_out$padj), ]
write.csv(eq_out, snakemake@output[["unchanged"]], row.names = FALSE)
write_check(snakemake@output[["equivalence_check"]], "13_equivalence_qc", "PASS",
            list(list(status = "PASS",
              message = sprintf("%d genes equivalent to no change (|log2FC| < %.2g at padj < %.3g, TOST lessAbs).",
                                nrow(eq_out), L_eq, alpha))))

writeLines(capture.output(sessionInfo()), snakemake@output[["session"]])
sink(type = "message")
close(log_con)
