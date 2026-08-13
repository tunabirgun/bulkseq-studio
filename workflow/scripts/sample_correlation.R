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
gp <- getp_for(style, "correlation")  # per-group palette/font/point/base-font/scaling override
fig_w <- as.numeric(gp("width_in", 6))
fig_h <- as.numeric(gp("height_in", 5))
fig_dpi <- as.integer(getp("dpi", 300))
base_size <- as.numeric(gp("base_font_size", 12))
font_family <- as.character(gp("font_family", ""))
base_family <- resolve_font(font_family)
palette_name <- as.character(gp("palette", "Blue-Red"))
number_fmt <- as.character(getp("heatmap_number_format", "%.2f"))
number_fs <- as.integer(getp("heatmap_number_fontsize", 0))  # 0 = auto (0.6x base)
# Per-sample axis labels; default TRUE (unchanged). Off declutters a many-sample run.
sample_labels <- isTRUE(as.logical(getp("sample_labels", TRUE)))
pal_spec <- palette_spec(palette_name)

measure_correlation_text <- function(labels, fontsize, fontfamily = NULL) {
  labels <- as.character(labels)
  labels <- labels[!is.na(labels) & nzchar(labels)]
  if (!length(labels)) return(list(width_pt = numeric(0), height_pt = numeric(0)))
  grDevices::pdf(NULL, width = 7, height = 7)
  on.exit(grDevices::dev.off(), add = TRUE)
  grid::grid.newpage()
  if (!is.null(fontfamily) && nzchar(fontfamily)) {
    grid::pushViewport(grid::viewport(gp = grid::gpar(fontfamily = fontfamily)))
  }
  gp_text <- grid::gpar(
    fontsize = fontsize,
    fontfamily = if (is.null(fontfamily)) "" else fontfamily
  )
  grobs <- lapply(labels, function(x) grid::textGrob(x, gp = gp_text))
  list(
    width_pt = vapply(grobs, function(g) grid::convertWidth(
      grid::grobWidth(g), "pt", valueOnly = TRUE), numeric(1)),
    height_pt = vapply(grobs, function(g) grid::convertHeight(
      grid::grobHeight(g), "pt", valueOnly = TRUE), numeric(1))
  )
}

correlation_minimum_cell_size <- function(number_labels, show_numbers,
                                           number_fontsize, fontsize,
                                           fontfamily = NULL,
                                           padding_pt = 4) {
  font_floor <- max(20, 1.8 * as.numeric(fontsize))
  if (!isTRUE(show_numbers)) {
    return(c(width = font_floor, height = font_floor))
  }
  number_metrics <- measure_correlation_text(
    as.vector(number_labels), number_fontsize, fontfamily
  )
  c(
    width = max(font_floor, max(number_metrics$width_pt, 0) + 2 * padding_pt),
    height = max(font_floor, max(number_metrics$height_pt, 0) + 2 * padding_pt)
  )
}

correlation_label_layout <- function(labels, cell_width_pt, fontsize,
                                     fontfamily = NULL, gap_pt = 2) {
  labels <- as.character(labels)
  if (!length(labels)) return(list(angle = 0, projected_width_pt = 0))
  metrics <- measure_correlation_text(labels, fontsize, fontfamily)
  candidate_angles <- c(0, 45, 90)
  projected <- vapply(candidate_angles, function(angle) {
    theta <- angle * pi / 180
    max(metrics$width_pt * cos(theta) + metrics$height_pt * sin(theta))
  }, numeric(1))
  fits <- projected + as.numeric(gap_pt) <= as.numeric(cell_width_pt)
  angle <- if (any(fits)) candidate_angles[which(fits)[1]] else 90
  list(angle = angle,
       projected_width_pt = projected[match(angle, candidate_angles)])
}

fit_correlation_heatmap <- function(make_heatmap, labels, minimum_cell,
                                    min_dim, fontsize, fontfamily = NULL,
                                    iterations = 3L) {
  n <- max(length(labels), 1L)
  cell_width_pt <- as.numeric(minimum_cell[["width"]])
  cell_height_pt <- as.numeric(minimum_cell[["height"]])
  for (i in seq_len(max(1L, as.integer(iterations)))) {
    label_layout <- correlation_label_layout(
      labels, cell_width_pt, fontsize, fontfamily
    )
    ph <- make_heatmap(label_layout$angle, cell_width_pt, cell_height_pt)
    measured <- finalize_heatmap_gtable(ph$gtable, min_w = 0, min_h = 0)
    add_width_pt <- max(0, as.numeric(min_dim[1]) - measured$dim[1]) * 72 / n
    add_height_pt <- max(0, as.numeric(min_dim[2]) - measured$dim[2]) * 72 / n
    if (add_width_pt < 0.1 && add_height_pt < 0.1) break
    cell_width_pt <- cell_width_pt + add_width_pt
    cell_height_pt <- cell_height_pt + add_height_pt
  }
  label_layout <- correlation_label_layout(
    labels, cell_width_pt, fontsize, fontfamily
  )
  ph <- make_heatmap(label_layout$angle, cell_width_pt, cell_height_pt)
  measured <- finalize_heatmap_gtable(
    ph$gtable, min_w = as.numeric(min_dim[1]), min_h = as.numeric(min_dim[2])
  )
  list(gtable = measured$gtable, dim = measured$dim,
       angle = label_layout$angle,
       cell_width_pt = cell_width_pt, cell_height_pt = cell_height_pt)
}

# Annotate columns by the contrast factor (falls back to the first colData column).
group_var <- "condition"
de_cfg <- tryCatch(snakemake@config[["deseq2"]], error = function(e) NULL)
contrast_cfg <- list()
if (is.list(de_cfg)) {
  cons <- de_cfg[["contrasts"]]
  if (is.list(cons) && length(cons) >= 1) {
    contrast_cfg <- cons[[1]]
    if (!is.null(contrast_cfg[["factor"]])) {
      group_var <- as.character(contrast_cfg[["factor"]])
    }
  }
}
cd <- as.data.frame(SummarizedExperiment::colData(vsd))
if (!(group_var %in% colnames(cd))) group_var <- colnames(cd)[1]
ann <- cd[, group_var, drop = FALSE]

placeholder <- function(png_path, svg_path, msg) {
  draw <- function() { plot.new(); text(0.5, 0.5, msg, cex = 1.1) }
  png(png_path, width = fig_w, height = fig_h, units = "in", res = fig_dpi,
      bg = "white"); draw(); dev.off()
  svglite(svg_path, width = fig_w, height = fig_h, bg = "white"); draw(); dev.off()
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
    # pheatmap supports only one number colour.  The maximum correlation maps to
    # the darkest fill, so dark text on those cells fails contrast (the diagonal
    # is also redundant by definition).  Keep the useful off-diagonal values and
    # leave every maximum-valued cell blank; the colour scale and exported CSV
    # retain the exact value, including identical off-diagonal samples.
    number_labels <- matrix(
      sprintf(number_fmt, as.vector(cm)),
      nrow = nrow(cm), ncol = ncol(cm), dimnames = dimnames(cm)
    )
    max_corr <- max(cm[is.finite(cm)])
    number_labels[is.finite(cm) & abs(cm - max_corr) <= sqrt(.Machine$double.eps)] <- ""
    # Annotation track colours: map each level of the grouping factor onto the
    # shared discrete palette, keyed by the annotation column name (pheatmap form).
    ann_lvls <- unique(as.character(ann[[1]]))
    ann_colmap <- contrast_color_map(ann_lvls, contrast_cfg, pal_spec$discrete)
    ann_colors <- setNames(list(ann_colmap), colnames(ann))
    make_correlation_heatmap <- function(angle_col, cell_width_pt,
                                         cell_height_pt) {
      pheatmap(cm, clustering_method = "ward.D2",
               cluster_rows = cluster, cluster_cols = cluster,
               display_numbers = if (show_num) number_labels else FALSE,
               fontsize_number = num_fs, angle_col = angle_col,
               show_rownames = sample_labels, show_colnames = sample_labels,
               annotation_col = ann, annotation_colors = ann_colors,
               annotation_names_col = TRUE, annotation_legend = TRUE,
               cellwidth = cell_width_pt, cellheight = cell_height_pt,
               fontsize = base_size, breaks = brks,
               color = pal_spec$seq(255), main = NA, silent = TRUE)
    }
    minimum_cell <- correlation_minimum_cell_size(
      number_labels, show_num, num_fs, base_size, base_family
    )
    corr_render <- fit_correlation_heatmap(
      make_correlation_heatmap,
      if (sample_labels) colnames(cm) else character(0),
      minimum_cell, c(fig_w, fig_h), base_size, base_family
    )
    message(sprintf(
      paste0("Correlation geometry (", method, "): angle=%d degrees; ",
             "cell=%.1f x %.1f pt; canvas=%.2f x %.2f in"),
      corr_render$angle, corr_render$cell_width_pt, corr_render$cell_height_pt,
      corr_render$dim[1], corr_render$dim[2]
    ))
    # Draw under the configured font so these heatmaps match the ggplot figures. pheatmap
    # text grobs carry no fontfamily, so a viewport gpar propagates to them (png + svglite).
    draw_ph <- function() {
      grid::grid.newpage()
      if (!is.null(base_family)) {
        grid::pushViewport(grid::viewport(gp = grid::gpar(fontfamily = base_family)))
        grid::grid.draw(corr_render$gtable); grid::popViewport()
      } else {
        grid::grid.draw(corr_render$gtable)
      }
    }
    png(png_path, width = corr_render$dim[1], height = corr_render$dim[2], units = "in", res = fig_dpi,
        bg = "white"); draw_ph(); dev.off()
    svglite(svg_path, width = corr_render$dim[1], height = corr_render$dim[2], bg = "white"); draw_ph(); dev.off()
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
