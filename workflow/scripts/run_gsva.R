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

# GSVA sample-level gene-set activity (optional). Scores each sample against the user-supplied
# custom gene sets (a GMT), so it is organism-agnostic and valid for non-model organisms — it
# never uses a bundled human collection. Descriptive/exploratory only: the scores are per-sample
# enrichment values, NOT a per-gene-set significance test. Reads the normalized expression matrix
# (VST counts / logCPM / log2 intensity) and writes a pathway x sample score matrix + a heatmap.

suppressMessages({
  library(GSVA)
  library(pheatmap)
  library(svglite)
  library(RColorBrewer)
})
# Shared palette helper (sourced; resolved via scriptdir) so the GSVA heatmap uses the project
# diverging ramp like the other heatmaps instead of a hardcoded Blue-Red.
source(file.path(snakemake@scriptdir, "figure_style.R"))

set.seed(42)
log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

expr_file <- snakemake@input[["normalized"]]
gmt_file <- snakemake@input[["gmt"]]
samples_file <- snakemake@input[["samples"]]
out_csv <- snakemake@output[["scores"]]
png_path <- snakemake@output[["heatmap_png"]]
svg_path <- snakemake@output[["heatmap_svg"]]
# Per-sample column labels on the GSVA (gene-set x sample) heatmap; default TRUE. Off (the Figure
# Style "Show per-sample labels" toggle) declutters a many-sample run, like the other heatmaps.
sample_labels <- { sl <- tryCatch(snakemake@config[["figures_style"]][["sample_labels"]],
                                  error = function(e) NULL)
                   if (is.null(sl)) TRUE else isTRUE(as.logical(sl)) }
# Project palette for the diverging heatmap ramp (was hardcoded); read from the same style config.
fig_style <- tryCatch(snakemake@config[["figures_style"]], error = function(e) NULL)
if (!is.list(fig_style)) fig_style <- list()
pal_spec <- palette_spec(as.character(make_getp(fig_style)("palette", "Blue-Red")))

dir.create(dirname(out_csv), showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(png_path), showWarnings = FALSE, recursive = TRUE)

placeholder <- function(msg) {
  write.csv(data.frame(gene_set = character(0)), out_csv, row.names = FALSE)
  for (dev_open in list(function() png(png_path, width = 1800, height = 1200, res = 300),
                        function() svglite(svg_path, width = 6, height = 4))) {
    dev_open(); plot.new(); text(0.5, 0.5, msg, cex = 1.1); dev.off()
  }
  message(msg)
}

# ---- Expression matrix (genes x samples) ------------------------------------
expr <- read.csv(expr_file, row.names = 1, check.names = FALSE)
expr_mat <- as.matrix(expr)
mode(expr_mat) <- "numeric"
expr_mat <- expr_mat[stats::complete.cases(expr_mat), , drop = FALSE]

# ---- GMT -> named list of gene sets -----------------------------------------
lines <- readLines(gmt_file, warn = FALSE)
lines <- lines[nzchar(trimws(lines))]
gene_sets <- lapply(lines, function(l) {
  p <- strsplit(l, "\t")[[1]]
  g <- p[-(1:2)]
  unique(g[nzchar(g)])
})
names(gene_sets) <- vapply(lines, function(l) strsplit(l, "\t")[[1]][1], "")

# Namespace guard (like custom enrichment): keep sets with >= 2 genes present in the matrix.
present <- rownames(expr_mat)
ov <- vapply(gene_sets, function(g) sum(g %in% present), 0L)
gene_sets <- gene_sets[ov >= 2]

if (length(gene_sets) < 1 || ncol(expr_mat) < 2) {
  placeholder("GSVA skipped: no gene sets overlap the run's gene identifiers (check the GMT namespace).")
  sink(type = "message"); close(log_con); quit(save = "no")
}

# ---- GSVA (Gaussian kcdf for continuous log-scale expression) ---------------
gpar <- gsvaParam(expr_mat, gene_sets, kcdf = "Gaussian", minSize = 2, maxSize = 500)
scores <- gsva(gpar)
write.csv(scores, out_csv)

# ---- Heatmap (top-variable pathways, z-scored per pathway) ------------------
ann <- NULL
samples <- tryCatch(read.delim(samples_file, stringsAsFactors = FALSE), error = function(e) NULL)
if (!is.null(samples) && "condition" %in% colnames(samples)) {
  rownames(samples) <- samples$sample_id
  common <- intersect(colnames(scores), rownames(samples))
  if (length(common)) ann <- data.frame(condition = samples[common, "condition"], row.names = common)
}
# Drop pathways with non-finite variance (constant/degenerate scores) BEFORE ranking: sort() silently
# discards NA variances, so indexing by min(40, nrow) would otherwise pull NA row names and crash
# scores[top, ] with 'subscript out of bounds'. The gsva scores CSV is already written above, so a
# degenerate heatmap must not abort the rule — degrade to a text placeholder instead (keeping the CSV).
v <- apply(scores, 1, stats::var)
# Drop finite ZERO-variance pathways too, not just NA/NaN/Inf: a constant-score gene set (var==0,
# which is finite) would survive into the top-40 and pheatmap(scale="row") z-scores it to an all-NaN
# row, crashing hclust (NaN distances) after gsva_scores.csv is already written. A constant pathway
# carries no cross-sample signal, so excluding it from a top-variable heatmap is also correct.
v <- v[is.finite(v) & v > 0]
if (length(v) < 1) {
  for (dev_open in list(function() png(png_path, width = 1800, height = 1200, res = 300),
                        function() svglite(svg_path, width = 6, height = 4))) {
    dev_open(); plot.new()
    text(0.5, 0.5, "GSVA heatmap skipped: no gene set varies across samples.", cex = 1.1)
    dev.off()
  }
} else {
  top <- names(sort(v, decreasing = TRUE))[seq_len(min(40, length(v)))]
  mat <- scores[top, , drop = FALSE]
  h <- max(3.5, min(14, 0.22 * nrow(mat) + 1.5))
  # cluster_rows needs >= 2 rows; a single surviving pathway would otherwise crash pheatmap's hclust.
  draw <- function() pheatmap(mat, scale = "row", show_rownames = TRUE,
                              show_colnames = sample_labels,  # honor the declutter toggle
                              cluster_rows = nrow(mat) >= 2,
                              annotation_col = ann, angle_col = 45,
                              color = pal_spec$div(255),  # project diverging ramp (was hardcoded)
                              fontsize = 9, silent = FALSE)
  png(png_path, width = 8, height = h, units = "in", res = 300); draw(); dev.off()
  svglite(svg_path, width = 8, height = h); draw(); dev.off()
}

writeLines(capture.output(sessionInfo()), sub("gsva_scores\\.csv$", "gsva_sessionInfo.txt", out_csv))
sink(type = "message")
close(log_con)
