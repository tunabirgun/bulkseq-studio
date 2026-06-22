# Sample-sample correlation matrix + hierarchical clustering (Pearson and
# Spearman) from the normalized expression matrix in deseq2_objects.rds. Both
# backends (VST counts / log2 intensity); organism-agnostic; no new dependency.

suppressMessages({
  library(SummarizedExperiment)
  library(pheatmap)
  library(svglite)
  library(RColorBrewer)
  library(ggplot2)
  library(scales)
})

# Shared palette/theme/getp helpers (sourced; resolved via scriptdir).
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
vsd <- obj$vsd
out <- snakemake@output
m <- SummarizedExperiment::assay(vsd)

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style)
fig_w <- as.numeric(getp("width_in", 6))
fig_h <- as.numeric(getp("height_in", 5))
fig_dpi <- as.integer(getp("dpi", 300))
base_size <- as.numeric(getp("base_font_size", 12))
palette_name <- as.character(getp("palette", "Blue-Red"))
number_fmt <- as.character(getp("heatmap_number_format", "%.2f"))
number_fs <- as.integer(getp("heatmap_number_fontsize", 0))  # 0 = auto (0.6x base)
pal_spec <- palette_spec(palette_name)

# Annotate columns by the contrast factor (falls back to the first colData column).
group_var <- "condition"
de_cfg <- tryCatch(snakemake@config[["deseq2"]], error = function(e) NULL)
if (is.list(de_cfg)) {
  cons <- de_cfg[["contrasts"]]
  if (is.list(cons) && length(cons) >= 1 && !is.null(cons[[1]][["factor"]])) {
    group_var <- as.character(cons[[1]][["factor"]])
  }
}
cd <- as.data.frame(SummarizedExperiment::colData(vsd))
if (!(group_var %in% colnames(cd))) group_var <- colnames(cd)[1]
ann <- cd[, group_var, drop = FALSE]

placeholder <- function(png_path, svg_path, msg) {
  draw <- function() { plot.new(); text(0.5, 0.5, msg, cex = 1.1) }
  png(png_path, width = fig_w, height = fig_h, units = "in", res = fig_dpi); draw(); dev.off()
  svglite(svg_path, width = fig_w, height = fig_h); draw(); dev.off()
}
save_corr <- function(method, png_path, svg_path, csv_path) {
  # Best-effort: an intensity matrix with NA (e.g. microarray log2 of non-positive
  # values) must not abort the whole run. Use pairwise-complete correlation, skip
  # hclust when the matrix still has NA, and degrade to a placeholder on any error.
  ok <- tryCatch({
    cm <- cor(m, method = method, use = "pairwise.complete.obs")
    write.csv(cm, csv_path)
    cluster <- !anyNA(cm)  # hclust cannot handle NA distances
    # Correlations here are all positive (no zero crossover), so a sequential ramp
    # over the observed range is honest; RdBu would imply a false zero-correlation
    # midpoint. Annotation track colours come from the shared discrete palette.
    rng <- range(cm[is.finite(cm)])
    # NA breaks -> pheatmap auto-bins; avoids non-increasing breaks on a constant matrix.
    brks <- if (is.finite(rng[1]) && rng[2] > rng[1]) seq(rng[1], rng[2], length.out = 256) else NA
    # In-cell numbers get unreadable past ~12 samples; suppress them then.
    show_num <- ncol(cm) <= 12
    num_fs <- if (number_fs > 0) number_fs else max(5, round(0.6 * base_size))
    # Annotation track colours: map each level of the grouping factor onto the
    # shared discrete palette, keyed by the annotation column name (pheatmap form).
    ann_lvls <- unique(as.character(ann[[1]]))
    ann_colmap <- setNames(pal_spec$discrete[((seq_along(ann_lvls) - 1) %% length(pal_spec$discrete)) + 1], ann_lvls)
    ann_colors <- setNames(list(ann_colmap), colnames(ann))
    ph <- pheatmap(cm, clustering_method = "ward.D2",
                   cluster_rows = cluster, cluster_cols = cluster,
                   display_numbers = show_num, number_format = number_fmt,
                   fontsize_number = num_fs, angle_col = 45,
                   annotation_col = ann, annotation_colors = ann_colors,
                   fontsize = base_size, breaks = brks,
                   color = pal_spec$seq(255), main = NA, silent = TRUE)
    png(png_path, width = fig_w, height = fig_h, units = "in", res = fig_dpi)
    grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
    svglite(svg_path, width = fig_w, height = fig_h)
    grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
    TRUE
  }, error = function(e) { message("sample_correlation (", method, ") failed: ", conditionMessage(e)); FALSE })
  if (!isTRUE(ok)) {
    if (!file.exists(csv_path)) tryCatch(writeLines("", csv_path), error = function(e) NULL)
    placeholder(png_path, svg_path, paste("Correlation unavailable:", method))
  }
}

save_corr("pearson", out[["pearson_png"]], out[["pearson_svg"]], out[["pearson_csv"]])
save_corr("spearman", out[["spearman_png"]], out[["spearman_svg"]], out[["spearman_csv"]])

sink(type = "message")
close(log_con)
