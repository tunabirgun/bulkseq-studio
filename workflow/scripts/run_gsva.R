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
# Figure style. The rule declares no `style` param, so the values arrive through the config;
# read the param first anyway so the script matches the other figure scripts if one is added.
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- tryCatch(snakemake@config[["figures_style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style)
# A sample x gene-set heatmap belongs to the 'core' figure group, so a per-group override
# (palette / font / base font / canvas) applies here as it does in make_figures.R.
gp <- getp_for(style, "core")
pal_spec <- palette_spec(as.character(gp("palette", "Blue-Red")))
# resolve_font maps a Windows font name onto one installed in the pipeline environment.
base_family <- resolve_font(as.character(gp("font_family", "")))
base_size <- as.numeric(gp("base_font_size", 12))
# Pathway names are the long labels here, so they follow make_figures.R's heatmap
# convention (row labels four points under the base font) instead of the base size.
fs_row <- max(4, base_size - 4)
fig_w <- as.numeric(gp("width_in", 8)); fig_h <- as.numeric(gp("height_in", 5))
fig_dpi <- as.integer(getp("dpi", 300))
# Per-sample column labels on the GSVA (gene-set x sample) heatmap; default TRUE. Off (the Figure
# Style "Show per-sample labels" toggle) declutters a many-sample run, like the other heatmaps.
sample_labels <- { sl <- style[["sample_labels"]]
                   if (is.null(sl)) TRUE else isTRUE(as.logical(sl)) }
# pheatmap's text grobs carry no font family of their own, so draw the gtable inside a
# viewport that sets one (the same mechanism make_figures.R uses for its heatmaps).
draw_grid <- function(gtable) {
  grid::grid.newpage()
  if (!is.null(base_family)) {
    grid::pushViewport(grid::viewport(gp = grid::gpar(fontfamily = base_family)))
    grid::grid.draw(gtable); grid::popViewport()
  } else grid::grid.draw(gtable)
}

dir.create(dirname(out_csv), showWarnings = FALSE, recursive = TRUE)
dir.create(dirname(png_path), showWarnings = FALSE, recursive = TRUE)

draw_message <- function(msg) {
  for (dev_open in list(function() png(png_path, width = fig_w, height = fig_h, units = "in", res = fig_dpi),
                        function() svglite(svg_path, width = fig_w, height = fig_h))) {
    dev_open(); plot.new(); text(0.5, 0.5, msg, cex = 1.1); dev.off()
  }
}
placeholder <- function(msg) {
  write.csv(data.frame(gene_set = character(0)), out_csv, row.names = FALSE)
  draw_message(msg)
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
  draw_message("GSVA heatmap skipped: no gene set varies across samples.")
} else {
  top <- names(sort(v, decreasing = TRUE))[seq_len(min(40, length(v)))]
  mat <- scores[top, , drop = FALSE]
  # Row pitch follows the row font (>= 1.6 line heights per row) so a larger base font
  # cannot make the pathway labels collide; 0.22 in reproduces the previous 9 pt layout.
  row_in <- max(0.22, 1.6 * fs_row / 72)
  h <- max(fig_h, min(14, row_in * nrow(mat) + 1.5))
  # cluster_rows needs >= 2 rows; a single surviving pathway would otherwise crash pheatmap's hclust.
  draw <- function() pheatmap(mat, scale = "row", show_rownames = TRUE,
                              show_colnames = sample_labels,  # honor the declutter toggle
                              cluster_rows = nrow(mat) >= 2,
                              annotation_col = ann, angle_col = 45,
                              color = pal_spec$div(255),  # project diverging ramp (was hardcoded)
                              fontsize = base_size, fontsize_row = fs_row, silent = TRUE)
  png(png_path, width = fig_w, height = h, units = "in", res = fig_dpi)
  draw_grid(draw()$gtable); dev.off()
  svglite(svg_path, width = fig_w, height = h)
  draw_grid(draw()$gtable); dev.off()
}

writeLines(capture.output(sessionInfo()), sub("gsva_scores\\.csv$", "gsva_sessionInfo.txt", out_csv))
sink(type = "message")
close(log_con)
