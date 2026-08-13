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

# Transcriptomics figures (protocol section 9). Each figure is written as PNG
# (raster) and SVG (vector). Titles are omitted (captions live in text).
# Visual style is read from config[["figures_style"]] (set in the GUI); every
# field falls back to a default so older configs without the block still run.

suppressMessages({
  library(DESeq2)
  library(ggplot2)
  library(ggrepel)
  library(pheatmap)
  library(RColorBrewer)
  library(scales)
  library(svglite)
})

# Shared palette/theme/getp/save_gg helpers (sourced; resolved via scriptdir).
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
dds <- obj$dds; res <- obj$res; resLFC <- obj$resLFC; vsd <- obj$vsd
out <- snakemake@output
# Bring-your-own DESeq2-results mode ships a synthetic RDS with no dds/vsd, so the
# count/VST-dependent figures (PCA, sample-distance, top-DEG heatmap, model
# diagnostics) degrade to labelled placeholders; MA, volcano and the p-value
# histogram render from res/resLFC as usual.
has_counts <- !is.null(vsd) && !is.null(dds)
# Log-scale backends (microarray log2 intensity; limma-voom logCPM): baseMean is a
# log-scale mean, not a count mean, so count-scale transforms (log10 on baseMean)
# are skipped below and the DESeq2-only model diagnostics degrade to placeholders.
assay_kind <- tryCatch(obj$assay_kind, error = function(e) NULL)
is_intensity <- isTRUE(assay_kind %in% c("log2_intensity", "log2_cpm"))

# Gene-id -> symbol labels (from the DE step). Falls back to the gene id when no
# symbol is known, so RefSeq/locus-tag references and older RDS files still work.
symbol_map <- tryCatch(obj$symbol_map, error = function(e) NULL)
label_for <- function(ids) {
  if (is.null(symbol_map)) return(ids)
  s <- unname(symbol_map[ids])
  ifelse(is.na(s) | !nzchar(s), ids, s)
}

# ---- Style parameters (NULL-safe) ------------------------------------------
# Read from the rule's declared params (a Snakemake rerun trigger); fall back to
# config for older invocations that did not pass the style as a param.
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (is.null(style) || !is.list(style)) {
  style <- tryCatch(snakemake@config[["figures_style"]], error = function(e) NULL)
}
if (is.null(style) || !is.list(style)) style <- list()
getp <- make_getp(style)
# Per-group override (palette / font / point size / base font / scaling) for the core figures.
gp <- getp_for(style, "core")

palette_name <- as.character(gp("palette", "Blue-Red"))
point_size   <- as.numeric(gp("point_size", 2.5))
base_size    <- as.numeric(gp("base_font_size", 12))
font_family  <- as.character(gp("font_family", ""))
label_bold   <- isTRUE(as.logical(getp("label_bold", FALSE)))
title_bold   <- isTRUE(as.logical(getp("title_bold", FALSE)))
volcano_top  <- as.integer(getp("volcano_top_n", 15))
heatmap_top  <- as.integer(getp("heatmap_top_n", 30))
pca_ntop     <- as.integer(getp("pca_ntop", 500))
fig_w        <- as.numeric(gp("width_in", 6))
fig_h        <- as.numeric(gp("height_in", 5))
fig_dpi      <- as.integer(getp("dpi", 300))

# New figure-style fields (W2). All read NULL-safe so older configs still run.
scatter_alpha_fg <- as.numeric(getp("scatter_alpha_fg", 0.8))
scatter_alpha_bg <- as.numeric(getp("scatter_alpha_bg", 0.25))
pca_fixed_aspect <- isTRUE(as.logical(getp("pca_fixed_aspect", FALSE)))
# Per-sample text labels on PCA + sample-distance heatmap. Default TRUE (unchanged);
# off declutters a many-sample run (common on microarray series).
sample_labels    <- isTRUE(as.logical(getp("sample_labels", TRUE)))
# Italicise gene-symbol labels (volcano + DEG heatmap rows). Default TRUE (HGNC convention).
gene_symbol_italic <- isTRUE(as.logical(getp("gene_symbol_italic", TRUE)))
heatmap_zlim     <- as.numeric(getp("heatmap_zlim", 2.5))
heatmap_cell_h   <- as.numeric(getp("heatmap_cell_height", 12))
heatmap_fs_row   <- as.integer(getp("heatmap_fontsize_row", 0))  # 0 = auto (base - 4)

# Per-figure canvas size overrides: key -> c(w_in, h_in). Falls back to global.
size_overrides <- tryCatch(getp("size_overrides", list()), error = function(e) list())
if (is.null(size_overrides) || !is.list(size_overrides)) size_overrides <- list()
fig_dim <- function(key) {
  v <- size_overrides[[key]]
  if (is.null(v) || length(v) < 2) return(c(fig_w, fig_h))
  c(as.numeric(v[[1]]), as.numeric(v[[2]]))
}

# Palette roles + theme + ggplot save come from figure_style.R.
pal_spec    <- palette_spec(palette_name)
base_family <- resolve_font(font_family)
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = label_bold, title_bold = title_bold)
save_gg     <- make_save_gg(fig_w = fig_w, fig_h = fig_h, fig_dpi = fig_dpi)

# Draw a grid gtable (pheatmap output) under the configured font. pheatmap's text grobs
# carry no fontfamily of their own, so a viewport gpar(fontfamily=...) propagates to them
# and makes these heatmaps match the ggplot figures' font (both png and svglite honour it).
draw_grid <- function(gtable) {
  grid::grid.newpage()
  if (!is.null(base_family)) {
    grid::pushViewport(grid::viewport(gp = grid::gpar(fontfamily = base_family)))
    grid::grid.draw(gtable); grid::popViewport()
  } else {
    grid::grid.draw(gtable)
  }
}
save_grid <- function(gtable, png_path, svg_path, w = fig_w, h = fig_h) {
  png(png_path, width = w, height = h, units = "in", res = fig_dpi,
      bg = "white")
  draw_grid(gtable); dev.off()
  svglite(svg_path, width = w, height = h, bg = "white")
  draw_grid(gtable); dev.off()
}

# Base-graphics figures (plotDispEsts, boxplot) to PNG + SVG.
save_base <- function(draw_fn, png_path, svg_path, w = fig_w, h = fig_h) {
  png(png_path, width = w, height = h, units = "in", res = fig_dpi); draw_fn(); dev.off()
  svglite(svg_path, width = w, height = h); draw_fn(); dev.off()
}

# A labelled placeholder so a declared output always exists when a figure does
# not apply (e.g. count-scale diagnostics on the microarray backend).
save_placeholder <- function(msg, png_path, svg_path) {
  p <- ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) +
    theme_void()
  save_gg(p, png_path, svg_path)
}

# Choose the least-rotated sample-label layout whose measured horizontal extent
# fits one fixed-width matrix cell. Fixed cells prevent long row labels and two
# legends from collapsing the matrix to slivers; the final device is measured
# from the rendered gtable below, so labels and legends cannot push it off-canvas.
sample_distance_label_layout <- function(labels, cell_width_pt, fontsize,
                                          fontfamily = NULL, gap_pt = 2) {
  labels <- as.character(labels)
  if (!length(labels)) {
    return(list(angle = 0, height_in = 0, cell_spacing_in = Inf,
                max_label_width_in = 0, max_label_height_in = 0))
  }

  metrics <- (function() {
    grDevices::pdf(NULL, width = 7, height = 7)
    on.exit(grDevices::dev.off(), add = TRUE)
    grid::grid.newpage()
    if (!is.null(fontfamily) && nzchar(fontfamily)) {
      grid::pushViewport(grid::viewport(
        gp = grid::gpar(fontfamily = fontfamily)
      ))
    }
    text_gp <- grid::gpar(
      fontsize = fontsize,
      fontfamily = if (is.null(fontfamily)) "" else fontfamily
    )
    label_grobs <- lapply(labels, function(x) grid::textGrob(x, gp = text_gp))
    label_widths <- vapply(
      label_grobs,
      function(g) grid::convertWidth(grid::grobWidth(g), "in", valueOnly = TRUE),
      numeric(1)
    )
    label_heights <- vapply(
      label_grobs,
      function(g) grid::convertHeight(grid::grobHeight(g), "in", valueOnly = TRUE),
      numeric(1)
    )
    list(label_widths_in = label_widths, label_heights_in = label_heights,
         max_label_width_in = max(label_widths),
         max_label_height_in = max(label_heights))
  })()

  cell_spacing_in <- as.numeric(cell_width_pt) / 72
  gap_in <- as.numeric(gap_pt) / 72
  candidate_angles <- c(0, 45, 90)
  projected_widths <- vapply(candidate_angles, function(angle) {
    theta <- angle * pi / 180
    max(metrics$label_widths_in * cos(theta) +
          metrics$label_heights_in * sin(theta))
  }, numeric(1))
  fits <- projected_widths + gap_in <= cell_spacing_in
  angle <- if (any(fits)) candidate_angles[which(fits)[1]] else 90
  theta <- angle * pi / 180
  height_in <- metrics$max_label_width_in * sin(theta) +
    metrics$max_label_height_in * cos(theta) + gap_in
  list(angle = angle, height_in = height_in,
       cell_spacing_in = cell_spacing_in,
       max_label_width_in = metrics$max_label_width_in,
       max_label_height_in = metrics$max_label_height_in)
}

# Build, measure, and (when the configured minimum canvas has spare room) grow
# only the cells. Rebuilding is deliberate: pheatmap's legends and text grobs
# have real dimensions that an n-by-n arithmetic estimate cannot recover.
fit_sample_distance_heatmap <- function(make_heatmap, labels, min_cell_width_pt,
                                        min_cell_height_pt, min_dim,
                                        fontsize, fontfamily = NULL,
                                        iterations = 3L) {
  n <- max(length(labels), 1L)
  cell_width_pt <- as.numeric(min_cell_width_pt)
  cell_height_pt <- as.numeric(min_cell_height_pt)
  label_layout <- NULL
  measured <- NULL
  for (i in seq_len(max(1L, as.integer(iterations)))) {
    label_layout <- sample_distance_label_layout(
      labels, cell_width_pt, fontsize, fontfamily
    )
    ph <- make_heatmap(label_layout$angle, TRUE,
                       cell_width_pt, cell_height_pt)
    ph <- prepare_sample_distance_gtable(ph)
    measured <- finalize_heatmap_gtable(ph$gtable, min_w = 0, min_h = 0)
    add_width_pt <- max(0, as.numeric(min_dim[1]) - measured$dim[1]) * 72 / n
    add_height_pt <- max(0, as.numeric(min_dim[2]) - measured$dim[2]) * 72 / n
    if (add_width_pt < 0.1 && add_height_pt < 0.1) break
    cell_width_pt <- cell_width_pt + add_width_pt
    cell_height_pt <- cell_height_pt + add_height_pt
  }
  label_layout <- sample_distance_label_layout(
    labels, cell_width_pt, fontsize, fontfamily
  )
  ph <- make_heatmap(label_layout$angle, TRUE,
                     cell_width_pt, cell_height_pt)
  ph <- prepare_sample_distance_gtable(ph)
  measured <- finalize_heatmap_gtable(
    ph$gtable, min_w = as.numeric(min_dim[1]), min_h = as.numeric(min_dim[2])
  )
  list(gtable = measured$gtable, dim = measured$dim,
       angle = label_layout$angle,
       cell_width_pt = cell_width_pt, cell_height_pt = cell_height_pt,
       label_layout = label_layout)
}

# pheatmap draws a continuous legend as hundreds of abutting rectangles. SVG
# renderers antialias each rectangle independently, exposing horizontal seams
# that are absent in the PNG. Replace only that legend bar with one true vector
# linear gradient; tick labels and all other heatmap grobs remain unchanged.
smooth_continuous_legend <- function(gtable) {
  legend_idx <- which(gtable$layout$name == "legend")
  if (length(legend_idx) != 1L) return(gtable)
  legend_grob <- gtable$grobs[[legend_idx]]
  rect_idx <- which(vapply(legend_grob$children, function(child) {
    inherits(child, "rect") && length(child$gp$fill) > 1L &&
      length(child$height) > 1L
  }, logical(1)))
  if (length(rect_idx) != 1L) return(gtable)

  source_rect <- legend_grob$children[[rect_idx]]
  fill_colors <- rep_len(
    as.character(source_rect$gp$fill), length(source_rect$height)
  )
  gradient_rect <- grid::rectGrob(
    x = source_rect$x[1], y = source_rect$y[1],
    width = source_rect$width[1], height = sum(source_rect$height),
    hjust = source_rect$hjust, vjust = source_rect$vjust,
    name = source_rect$name,
    gp = grid::gpar(
      fill = grid::linearGradient(
        fill_colors, stops = seq(0, 1, length.out = length(fill_colors)),
        x1 = 0, y1 = 0, x2 = 0, y2 = 1
      ),
      col = NA
    )
  )
  legend_grob$children[[rect_idx]] <- gradient_rect
  gtable$grobs[[legend_idx]] <- legend_grob
  gtable
}

prepare_sample_distance_gtable <- function(ph) {
  ph$gtable <- smooth_continuous_legend(ph$gtable)
  ph
}

# ---- Volcano ranked-key geometry -------------------------------------------

# Keep every selected gene visible without implying a line-to-point association.
# The measured outside keys report adjusted-p rank and exact signed log2FC;
# highlighted markers remain at their true displayed coordinates in the panel.
volcano_add_ranked_key <- function(plot, labels, xm, ytop, canvas_w, canvas_h,
                                   label_family = NULL, label_size = 4,
                                   marker_size_mm = 1.4) {
  if (!nrow(labels)) {
    keyed_plot <- plot + ggplot2::coord_cartesian(
      xlim = c(-xm, xm), ylim = c(0, ytop), clip = "on", expand = FALSE
    )
    attr(keyed_plot, "volcano_canvas_width") <- canvas_w
    return(keyed_plot)
  }
  if (!is.finite(xm) || xm <= 0 || !is.finite(ytop) || ytop <= 0) {
    stop("Volcano key geometry requires finite positive axis spans")
  }
  required <- c("label", "log2FoldChange", "padj_rank", "direction",
                "y_plot", "capped")
  if (!all(required %in% colnames(labels))) {
    stop("Volcano key is missing required selected-gene fields")
  }
  if (any(!is.finite(labels$log2FoldChange)) ||
      any(!is.finite(labels$padj_rank))) {
    stop("Volcano key requires finite effects and adjusted-p ranks")
  }
  labels$key_side <- ifelse(labels$log2FoldChange < 0, "left", "right")
  labels$key_text <- sprintf("%02d  %s  (%+.2f)",
                             as.integer(labels$padj_rank),
                             as.character(labels$label),
                             labels$log2FoldChange)
  labels <- labels[order(labels$key_side, labels$padj_rank,
                         labels$label, method = "radix"), , drop = FALSE]

  gap_in <- 3 / 72
  panel_width_fraction <- 0.90
  minimum_data_fraction <- 0.34
  panel_w_in <- max(1, canvas_w * panel_width_fraction)
  panel_h_in <- max(1, canvas_h * 0.75)
  font_points <- label_size * 72.27 / 25.4
  text_gp <- grid::gpar(
    fontsize = font_points,
    fontfamily = if (is.null(label_family)) "" else label_family
  )
  row_grobs <- lapply(labels$key_text, function(x) {
    grid::textGrob(x, hjust = 0, gp = text_gp)
  })
  row_width_in <- vapply(
    row_grobs,
    function(g) grid::convertWidth(grid::grobWidth(g), "in", valueOnly = TRUE),
    numeric(1)
  )
  row_height_in <- vapply(
    row_grobs,
    function(g) grid::convertHeight(grid::grobHeight(g), "in", valueOnly = TRUE),
    numeric(1)
  )
  header_height_in <- grid::convertHeight(
    grid::grobHeight(grid::textGrob("Down", gp = text_gp)),
    "in", valueOnly = TRUE
  )
  key_width_in <- vapply(c("left", "right"), function(side) {
    values <- row_width_in[labels$key_side == side]
    if (length(values)) max(values) else 0
  }, numeric(1))
  names(key_width_in) <- c("left", "right")
  reserved_key_in <- key_width_in + 2 * gap_in
  required_panel_w_in <- sum(reserved_key_in) /
    (1 - minimum_data_fraction)
  required_canvas_w_in <- required_panel_w_in / panel_width_fraction
  canvas_w <- max(canvas_w, required_canvas_w_in)
  panel_w_in <- max(1, canvas_w * panel_width_fraction)
  data_panel_in <- panel_w_in - sum(reserved_key_in)
  if (!is.finite(data_panel_in) ||
      data_panel_in < minimum_data_fraction * panel_w_in) {
    stop("Volcano ranked keys require a wider figure canvas")
  }
  em_height_in <- font_points / 72
  row_step_in <- max(c(row_height_in, header_height_in, em_height_in)) + gap_in
  side_count <- table(factor(labels$key_side, levels = c("left", "right")))
  required_height_in <- (max(side_count) + 1) * row_step_in + gap_in
  if (!is.finite(required_height_in) || required_height_in > panel_h_in) {
    stop("Volcano ranked keys require a taller figure canvas")
  }

  x_per_in <- 2 * xm / data_panel_in
  y_per_in <- ytop / panel_h_in
  edge_gap_x <- gap_in * x_per_in
  left_extent <- reserved_key_in[["left"]] * x_per_in
  right_extent <- reserved_key_in[["right"]] * x_per_in
  x_limits <- c(-xm - left_extent, xm + right_extent)
  labels$key_x <- ifelse(
    labels$key_side == "left",
    x_limits[1] + gap_in * x_per_in,
    xm + edge_gap_x
  )
  labels$key_y <- NA_real_
  header_rows <- list()
  for (side in c("left", "right")) {
    indices <- which(labels$key_side == side)
    if (!length(indices)) next
    labels$key_y[indices] <- ytop -
      (seq_along(indices) + 1) * row_step_in * y_per_in
    header_rows[[side]] <- data.frame(
      key_side = side,
      direction = if (side == "left") "Down" else "Up",
      header = if (side == "left") "Down key" else "Up key",
      x = labels$key_x[indices[1]],
      y = ytop - row_step_in * y_per_in
    )
  }
  if (any(!is.finite(labels$key_y)) || any(labels$key_y <= 0)) {
    stop("Volcano ranked-key packing left a row outside the canvas")
  }
  headers <- do.call(rbind, header_rows)

  regular_selected <- labels[!labels$capped, , drop = FALSE]
  capped_selected <- labels[labels$capped, , drop = FALSE]
  if (nrow(regular_selected)) {
    plot <- plot + ggplot2::geom_point(
      data = regular_selected,
      mapping = ggplot2::aes(x = log2FoldChange, y = y_plot,
                             colour = direction),
      inherit.aes = FALSE, shape = 21, fill = "white",
      size = marker_size_mm + 0.8, stroke = 0.55,
      show.legend = FALSE
    )
  }
  if (nrow(capped_selected)) {
    plot <- plot + ggplot2::geom_point(
      data = capped_selected,
      mapping = ggplot2::aes(x = log2FoldChange, y = y_plot,
                             colour = direction),
      inherit.aes = FALSE, shape = 17, size = marker_size_mm + 0.65,
      alpha = 0.95, show.legend = FALSE
    )
  }
  row_args <- list(
    data = labels,
    mapping = ggplot2::aes(x = key_x, y = key_y, label = key_text,
                           colour = direction),
    inherit.aes = FALSE, hjust = 0, size = label_size,
    show.legend = FALSE
  )
  header_args <- list(
    data = headers,
    mapping = ggplot2::aes(x = x, y = y, label = header,
                           colour = direction),
    inherit.aes = FALSE, hjust = 0, size = label_size,
    fontface = "bold", show.legend = FALSE
  )
  if (!is.null(label_family)) {
    row_args$family <- label_family
    header_args$family <- label_family
  }
  x_breaks <- pretty(c(-xm, xm), n = 5)
  x_breaks <- x_breaks[x_breaks >= -xm & x_breaks <= xm]
  zero_fraction <- (0 - x_limits[1]) / diff(x_limits)
  keyed_plot <- plot +
    do.call(ggplot2::geom_text, row_args) +
    do.call(ggplot2::geom_text, header_args) +
    ggplot2::scale_x_continuous(breaks = x_breaks, minor_breaks = NULL) +
    ggplot2::coord_cartesian(
      xlim = x_limits, ylim = c(0, ytop), clip = "on", expand = FALSE
    ) +
    ggplot2::labs(caption = paste0(
      "Highlighted triangles: listed genes.\n",
      "Ranked by adjusted p-value; signed log2FC gives x position."
    )) +
    ggplot2::theme(axis.title.x = ggplot2::element_text(hjust = zero_fraction))
  attr(keyed_plot, "volcano_canvas_width") <- canvas_w
  keyed_plot
}

# ---- End volcano-label geometry --------------------------------------------

# ---- Grouping factor (from the DESeq2 contrast; falls back safely) ----------
group_var <- "condition"
contrast_cfg <- list()
de_cfg <- tryCatch(snakemake@config[["deseq2"]], error = function(e) NULL)
if (is.list(de_cfg)) {
  cons <- de_cfg[["contrasts"]]
  if (is.list(cons) && length(cons) >= 1) {
    contrast_cfg <- cons[[1]]
    if (!is.null(contrast_cfg[["factor"]])) {
      group_var <- as.character(contrast_cfg[["factor"]])
    }
  }
}
if (has_counts && !(group_var %in% colnames(colData(dds)))) group_var <- colnames(colData(dds))[1]

# Significance thresholds from config (used by MA + volcano).
num_cfg <- function(key, default) {
  v <- tryCatch(as.numeric(de_cfg[[key]]), error = function(e) default)
  if (length(v) != 1 || is.na(v)) default else v
}
alpha_thr <- if (is.list(de_cfg)) num_cfg("alpha", 0.05) else 0.05
lfc_thr <- if (is.list(de_cfg)) num_cfg("lfc_threshold", 1) else 1

# ---- PCA --------------------------------------------------------------------
# plotPCA adds a generic "group" column for whatever intgroup is, so the plot
# code stays independent of the factor's name.
if (has_counts) {
pca <- plotPCA(vsd, intgroup = group_var, ntop = pca_ntop, returnData = TRUE)
pv <- round(100 * attr(pca, "percentVar"))
# The shared mapping is named, contrast-aware and therefore independent of the
# order in which samples happen to occur in pca/colData.
pca_disc <- contrast_color_map(unique(as.character(pca$group)), contrast_cfg,
                               pal_spec$discrete)
p_pca <- ggplot(pca, aes(PC1, PC2, colour = group)) +
  geom_point(size = point_size, alpha = 0.9)
if (sample_labels) {
  p_pca <- p_pca +
    geom_text_repel(aes(label = name), family = base_family, size = 3, seed = 1,
                    min.segment.length = 0, box.padding = 0.5, point.padding = 0.3,
                    max.overlaps = Inf, segment.colour = "grey55", show.legend = FALSE)
}
p_pca <- p_pca +
  scale_colour_manual(values = pca_disc, name = group_var) +
  scale_x_continuous(expand = expansion(mult = 0.08)) +
  scale_y_continuous(expand = expansion(mult = 0.08)) +
  labs(x = paste0("PC1 (", pv[1], "%)"), y = paste0("PC2 (", pv[2], "%)")) +
  style_theme(theme_bw)
# coord_fixed preserves Euclidean score distances (config toggle); skip it when a
# single PC dominates so the panel is not squeezed into a thin band.
if (pca_fixed_aspect) p_pca <- p_pca + coord_fixed()
pca_dim <- fig_dim("pca")
save_gg(p_pca, out[["pca_png"]], out[["pca_svg"]], w = pca_dim[1], h = pca_dim[2])

# ---- Sample-distance heatmap -----------------------------------------------
sampleDists <- dist(t(assay(vsd)), method = "euclidean")
mat <- as.matrix(sampleDists)
# Short sample IDs on the matrix; the group factor moves to an annotation track.
rownames(mat) <- colnames(mat) <- colnames(vsd)
dist_ann <- as.data.frame(colData(vsd)[, group_var, drop = FALSE])
ann_levels <- unique(as.character(dist_ann[[group_var]]))
ann_cols <- contrast_color_map(ann_levels, contrast_cfg, pal_spec$discrete)
# Distance is non-negative and sequential, not diverging (no false midpoint).
# Exclude the structural zero diagonal from the colour-domain calculation: it
# otherwise consumes almost the full sequential ramp and makes scientifically
# relevant off-diagonal differences indistinguishable. The diagonal is drawn in
# a neutral colour and the legend explicitly states its off-diagonal domain.
off_diag <- mat[row(mat) != col(mat) & is.finite(mat)]
dist_rng <- range(off_diag)
if (!length(off_diag) || any(!is.finite(dist_rng))) dist_rng <- c(0, 1)
if (dist_rng[2] <= dist_rng[1]) {
  eps <- max(abs(dist_rng[1]) * 0.01, 1e-8)
  dist_rng <- dist_rng + c(-eps, eps)
}
dist_breaks <- seq(dist_rng[1], dist_rng[2], length.out = 256)
dist_legend_breaks <- c(dist_rng[1], mean(dist_rng), dist_rng[2])
dist_legend_labels <- c(
  formatC(dist_legend_breaks[1], digits = 4, format = "fg"),
  formatC(dist_legend_breaks[2], digits = 4, format = "fg"),
  paste0(formatC(dist_legend_breaks[3], digits = 4, format = "fg"),
         "  off-diagonal\nEuclidean distance")
)
mat_display <- mat
diag(mat_display) <- NA_real_
cols <- pal_spec$seq(255)
make_distance_heatmap <- function(angle_col, show_colnames,
                                  cell_width_pt, cell_height_pt) {
  pheatmap(mat_display, clustering_distance_rows = sampleDists,
           clustering_distance_cols = sampleDists,
           clustering_method = "ward.D2", col = cols,
           breaks = dist_breaks, legend_breaks = dist_legend_breaks,
           legend_labels = dist_legend_labels, na_col = "#F2F4F7",
           annotation_col = dist_ann,
           annotation_colors = setNames(list(ann_cols), group_var),
           annotation_names_col = TRUE, annotation_legend = TRUE,
           angle_col = angle_col,
           show_rownames = sample_labels, show_colnames = show_colnames,
           cellwidth = cell_width_pt, cellheight = cell_height_pt,
           fontsize = base_size, silent = TRUE)
}
# A physical cell floor scales with the configured font, while the configured
# figure size remains a minimum canvas rather than a clipping boundary.
dist_floor <- fig_dim("sample_distance")
dist_min_cell_width_pt <- max(18, 1.6 * base_size)
dist_min_cell_height_pt <- max(18, 1.6 * base_size)
if (sample_labels) {
  dist_render <- fit_sample_distance_heatmap(
    make_distance_heatmap, colnames(vsd),
    dist_min_cell_width_pt, dist_min_cell_height_pt, dist_floor,
    fontsize = base_size, fontfamily = base_family
  )
} else {
  make_unlabelled_distance_heatmap <- function(angle_col, show_colnames,
                                               cell_width_pt, cell_height_pt) {
    make_distance_heatmap(angle_col, FALSE, cell_width_pt, cell_height_pt)
  }
  dist_render <- fit_sample_distance_heatmap(
    make_unlabelled_distance_heatmap, character(0),
    dist_min_cell_width_pt, dist_min_cell_height_pt, dist_floor,
    fontsize = base_size, fontfamily = base_family
  )
}
message(sprintf(
  paste0("Sample-distance geometry: angle=%d degrees; cell=%.1f x %.1f pt; ",
         "canvas=%.2f x %.2f in"),
  dist_render$angle, dist_render$cell_width_pt, dist_render$cell_height_pt,
  dist_render$dim[1], dist_render$dim[2]
))
save_grid(dist_render$gtable, out[["dist_png"]], out[["dist_svg"]],
          w = dist_render$dim[1], h = dist_render$dim[2])
} else {
  save_placeholder("PCA needs the counts / VST matrix (unavailable for a DESeq2-results upload).", out[["pca_png"]], out[["pca_svg"]])
  save_placeholder("Sample-distance heatmap needs the counts / VST matrix (unavailable for a DESeq2-results upload).", out[["dist_png"]], out[["dist_svg"]])
}

# ---- MA plot ----------------------------------------------------------------
# The MA plot is dense; scale the configured point size down so it stays legible.
ma_point <- max(0.3, point_size * 0.4)
if ("baseMean" %in% colnames(as.data.frame(resLFC)) && any(is.finite(as.data.frame(resLFC)$baseMean))) {
ma <- as.data.frame(resLFC)
ma <- ma[!is.na(ma$padj), ]
ma$sig <- ma$padj < alpha_thr
# Colour each point by local 2D density (base R densCols) so the dense band
# regains a gradient while individual outliers stay visible. x is log-scaled for
# counts, so density is computed on log10(baseMean) there.
xv <- if (is_intensity) ma$baseMean else log10(pmax(ma$baseMean, .Machine$double.eps))
ma$dens <- grDevices::densCols(xv, ma$log2FoldChange,
                               colramp = colorRampPalette(pal_spec$seq(7)))
ma_sig <- ma[ma$sig, ]
p_ma <- ggplot(ma, aes(baseMean, log2FoldChange)) +
  geom_point(aes(colour = dens), size = ma_point, alpha = scatter_alpha_fg) +
  scale_colour_identity() +
  ggnewscale::new_scale_colour() +
  geom_point(data = ma_sig, aes(colour = sprintf("padj < %.3g", alpha_thr)),
             shape = 21, fill = NA, size = ma_point + 0.4, stroke = 0.3, alpha = scatter_alpha_fg) +
  scale_colour_manual(values = setNames(pal_spec$discrete[2], sprintf("padj < %.3g", alpha_thr)),
                      name = NULL) +
  geom_smooth(method = "loess", span = 0.3, se = FALSE, colour = "grey25", linewidth = 0.5) +
  geom_hline(yintercept = 0, colour = "grey40", linewidth = 0.4) +
  labs(x = if (is_intensity) "average log2 expression" else "mean of normalised counts",
       y = "log2 fold change") +
  style_theme(theme_bw)
# Counts span orders of magnitude (log x); log2 intensities do not.
if (!is_intensity) p_ma <- p_ma + scale_x_log10(labels = scales::label_log())
ma_dim <- fig_dim("ma_plot")
save_gg(p_ma, out[["ma_png"]], out[["ma_svg"]], w = ma_dim[1], h = ma_dim[2])
} else save_placeholder("MA plot needs a baseMean column in the results table.", out[["ma_png"]], out[["ma_svg"]])

# ---- Volcano ----------------------------------------------------------------
vol <- as.data.frame(resLFC)
vol$gene <- rownames(vol)
vol$label <- label_for(vol$gene)
# x position keeps the SHRUNKEN resLFC effect size (apeglm/ashr; correct display convention:
# it de-noises low-count genes' fold changes). Up/Down classification below instead uses the
# RAW log2FoldChange from `res`, matching upregulated_genes.csv / downregulated_genes.csv
# (run_deseq2.R:181-185) and the up/down heatmaps below, so the figure's point colours agree
# with the CSV row counts make_html_report.py captions it with.
vol$log2FoldChange_raw <- res$log2FoldChange[match(vol$gene, rownames(res))]
# Only the DESeq2 backend shrinks: run_voom.R, run_edger.R, run_limma.R (microarray) and
# ingest_deseq2_results.R all set `resLFC <- res`, so on those routes the x axis carries the
# raw value and calling it "shrunken" would be a false claim -- the same one
# make_html_report.py (_micro_tech, ~:1200) already strips from the shared tech captions.
# Derive it from the data instead of hardcoding, so a new backend cannot desync the label.
lfc_is_shrunken <- !identical(res$log2FoldChange, resLFC$log2FoldChange)
vol <- vol[!is.na(vol$padj), ]
vol$neglog10padj <- -log10(vol$padj)

# padj can underflow to 0 (most-significant genes) -> -log10 = Inf. Clamp to a
# finite ceiling, but compute the auto-cap quantile on the PRE-clamp finite
# subset: when >0.5% of genes underflow (e.g. fghs, 1.4%), the floored points
# would otherwise drag the 99.5th percentile onto the floor and silently disable
# the cap (do_cap = floor > floor = FALSE), re-squeezing the panel.
ufloor <- as.numeric(getp("volcano_neglogp_floor", 320))  # ~ -log10(double min)
was_inf <- !is.finite(vol$neglog10padj)
vol$neglog10padj[was_inf] <- ufloor

# volcano_y_scale: 'cap' (default) squishes the tall -log10(padj) tail to a cap line with off-scale
# triangle markers; 'full' / 'sqrt' keep every gene at its TRUE height so a marginal gene with extreme
# significance shows up. Default 'cap' reproduces the previous output byte-for-byte.
yscale <- as.character(getp("volcano_y_scale", "cap"))

# Y cap: 0 = auto (quantile over finite-padj genes), then squish with pmin.
ycap <- as.numeric(getp("volcano_y_cap", 0))
if (ycap <= 0) {
  finite_y <- vol$neglog10padj[!was_inf]
  if (!length(finite_y)) finite_y <- vol$neglog10padj  # all-underflow guard
  ycap <- as.numeric(stats::quantile(finite_y, getp("volcano_y_cap_quantile", 0.995)))
}
if (identical(yscale, "cap")) {
  # Cap only when underflow exists (Inf must be squished) or a finite outlier exceeds
  # the cap by the headroom margin, so clean datasets (no extreme tail) stay un-capped.
  do_cap <- any(was_inf) ||
    max(vol$neglog10padj) > ycap * (1 + as.numeric(getp("volcano_cap_headroom", 0.10)))
  vol$y_plot <- if (do_cap) pmin(vol$neglog10padj, ycap) else vol$neglog10padj
  vol$capped <- do_cap & vol$neglog10padj > ycap
} else {
  # 'full' / 'sqrt': every gene sits at its true -log10(padj); only the padj==0 machine-underflow
  # genes (floored to ufloor) keep an off-scale triangle marker, so nothing is silently relocated.
  do_cap <- FALSE
  vol$y_plot <- vol$neglog10padj
  vol$capped <- was_inf
}

vol$direction <- "n.s."
# Guard on !is.na(log2FoldChange_raw) so an NA raw LFC is classified "n.s." rather than
# left to the comparison's NA. A gene can have a non-NA padj with an NA raw LFC (a gene
# present in resLFC but absent from res, so the match() above yields NA). With the
# length-1 RHS used here R would silently skip those positions anyway, so this is about
# stating the intent, not avoiding an error.
vol$direction[!is.na(vol$log2FoldChange_raw) & vol$padj < alpha_thr & vol$log2FoldChange_raw >=  lfc_thr] <- "Up"
vol$direction[!is.na(vol$log2FoldChange_raw) & vol$padj < alpha_thr & vol$log2FoldChange_raw <= -lfc_thr] <- "Down"
lab <- vol[vol$direction != "n.s.", ]
lab <- head(lab[order(lab$padj), ], volcano_top)
lab$padj_rank <- seq_len(nrow(lab))
vol$volcano_label_selected <- vol$gene %in% lab$gene

pal <- c(Down = pal_spec$discrete[1], "n.s." = "grey80", Up = pal_spec$discrete[2])

# Density-readable core: faint n.s. under, smaller/softer significant on top.
sig_size  <- max(0.6, point_size * as.numeric(getp("volcano_point_scale", 0.55)))
sig_alpha <- as.numeric(getp("volcano_point_alpha", 0.55))

xm   <- max(abs(vol$log2FoldChange))
cap_labels <- lab[lab$capped, , drop = FALSE]
cap_side_n <- if (nrow(cap_labels)) max(table(cap_labels$direction)) else 0
# Capped genes share one truthful y coordinate. Retain modest headroom above the
# off-scale marker shelf; the measured ranked keys occupy outside side columns.
cap_label_headroom <- if (do_cap && nrow(cap_labels)) {
  max(as.numeric(getp("volcano_cap_headroom", 0.10)),
      min(0.55, 0.08 + 0.04 * cap_side_n))
} else as.numeric(getp("volcano_cap_headroom", 0.10))
ytop <- if (do_cap) {
  ycap * (1 + cap_label_headroom)
} else max(vol$y_plot) * 1.02

p_vol <- ggplot(vol, aes(log2FoldChange, y_plot)) +
  geom_vline(xintercept = c(-lfc_thr, lfc_thr), linetype = "dashed",
             colour = "grey60", linewidth = 0.3) +
  annotate("segment", x = -xm, xend = xm,
           y = -log10(alpha_thr), yend = -log10(alpha_thr),
           linetype = "dashed", colour = "grey60", linewidth = 0.3) +
  geom_point(data = subset(vol, direction == "n.s." & !capped),
             aes(colour = direction), shape = 16,
             size = max(0.5, sig_size * 0.8), alpha = 0.4) +
  geom_point(data = subset(vol, direction != "n.s." & !capped),
             aes(colour = direction), shape = 16,
             size = sig_size, alpha = sig_alpha)

# Small open triangles mark points pushed to the cap line (no data hidden). Their
# direction colour remains visible without the dense white-filled marker row that
# previously collided with every capped-gene label.
if (any(vol$capped)) {
  p_vol <- p_vol +
    geom_point(data = subset(vol, capped & !volcano_label_selected),
               aes(colour = direction), shape = 2,
               size = sig_size + 0.25, stroke = 0.45, alpha = 0.75,
               show.legend = FALSE)
}

p_vol <- p_vol +
  scale_colour_manual(values = pal, breaks = c("Down", "n.s.", "Up"),
                      drop = FALSE, name = NULL,
                      guide = guide_legend(override.aes = list(shape = 16,
                                                               alpha = 1,
                                                               size = 3))) +
  labs(x = if (lfc_is_shrunken) "log2 fold change (shrunken)" else "log2 fold change",
       y = if (do_cap) "-log10 adjusted p (axis capped)"
           else if (identical(yscale, "sqrt")) "-log10 adjusted p (sqrt scale)"
           else "-log10 adjusted p") +
  style_theme(theme_bw) + theme(legend.position = "top")
# 'sqrt' compresses the tall padj tail (incl. the padj==0 floor shelf) so extreme genes stay readable
# without squashing the bulk. It is a scale transform, so guide and label coordinates stay in data units.
if (identical(yscale, "sqrt")) p_vol <- p_vol + scale_y_sqrt()
vol_dim <- fig_dim("volcano")
# Every selected gene is retained in a measured side key with rank and exact
# signed effect. Highlighted markers stay at their actual displayed positions;
# no connector is drawn when point-to-label association cannot be proved.
p_vol <- volcano_add_ranked_key(
  p_vol, lab, xm = xm, ytop = ytop, canvas_w = vol_dim[1],
  canvas_h = vol_dim[2], label_family = base_family,
  label_size = 4, marker_size_mm = sig_size
)
derived_vol_w <- attr(p_vol, "volcano_canvas_width", exact = TRUE)
if (length(derived_vol_w) == 1L && is.finite(derived_vol_w)) {
  vol_dim[1] <- max(vol_dim[1], derived_vol_w)
}
save_gg(p_vol, out[["volcano_png"]], out[["volcano_svg"]], w = vol_dim[1], h = vol_dim[2])

# ---- Top-DEG heatmap --------------------------------------------------------
# Drop NA padj first (order() puts NA last, so a naive head() would pull in NA
# rows), then index assay(vsd) BY NAME so the heatmap is robust to any row-order
# difference between res and vsd.
if (has_counts) {
ok <- which(!is.na(res$padj))
ord <- ok[order(res$padj[ok])]
n_top <- min(heatmap_top, length(ord))
top_names <- rownames(res)[head(ord, n_top)]
hm <- assay(vsd)[top_names, , drop = FALSE]
rownames(hm) <- label_for(top_names)
hm <- t(scale(t(hm)))
# Signed row z-scores need a zero-anchored diverging ramp with symmetric breaks,
# so z=0 maps to the neutral colour (not the data midpoint). Cap at +/- zlim.
zlim <- heatmap_zlim
hm <- pmin(pmax(hm, -zlim), zlim)
hm_breaks <- seq(-zlim, zlim, length.out = 256)
ann <- as.data.frame(colData(dds)[, group_var, drop = FALSE])
hm_levels <- unique(as.character(ann[[group_var]]))
hm_ann_cols <- contrast_color_map(hm_levels, contrast_cfg, pal_spec$discrete)
fs_row <- if (heatmap_fs_row > 0) heatmap_fs_row else max(4, base_size - 4)
hm_canvas_w <- if (!is.null(size_overrides[["top_deg_heatmap"]])) fig_dim("top_deg_heatmap")[1] else fig_w
hm_cell_w <- heatmap_cell_w_fill(ncol(hm), hm_canvas_w,
                                 row_label_chars = max(nchar(rownames(hm))))
ph2 <- pheatmap(hm, scale = "none", annotation_col = ann, show_rownames = TRUE,
                show_colnames = sample_labels,  # hide sample names to declutter a many-sample run
                labels_row = italic_labels(rownames(hm), gene_symbol_italic),
                cluster_rows = nrow(hm) >= 2,  # heatmap_top_n = 1 -> 1 row; hclust needs >= 2
                clustering_method = "ward.D2",
                color = pal_spec$div(255), breaks = hm_breaks,
                legend_breaks = c(-zlim, 0, zlim),
                legend_labels = c(sprintf("%.1f", -zlim), "0  (row z-score)", sprintf("%.1f", zlim)),
                annotation_colors = setNames(list(hm_ann_cols), group_var),
                annotation_names_col = TRUE, cellheight = heatmap_cell_h,
                cellwidth = hm_cell_w,
                border_color = NA, fontsize = base_size, fontsize_row = fs_row, silent = TRUE)
# pheatmap's fixed cells can make its gtable slightly wider than an estimated
# canvas; derive the final device from the padded gtable so neither dendrogram
# nor annotation legend can touch or cross the export boundary.
hm_min_dim <- fig_dim("top_deg_heatmap")
hm_render <- finalize_heatmap_gtable(ph2$gtable, hm_min_dim[1], hm_min_dim[2])
save_grid(hm_render$gtable, out[["heatmap_png"]], out[["heatmap_svg"]],
          w = hm_render$dim[1], h = hm_render$dim[2])
} else save_placeholder("Top-DEG heatmap needs the counts / VST matrix (unavailable for a DESeq2-results upload).", out[["heatmap_png"]], out[["heatmap_svg"]])

# ---- Separate up- / down-regulated top-DEG heatmaps -------------------------
# Split the significant genes by direction (raw log2FC sign + |log2FC| >= lfc_thr,
# the same definition as the up/down gene CSVs in run_deseq2.R) and draw a heatmap
# of the top-N by padj within each side. Called at top level: every declared output
# is always written (real heatmap, too-few-genes placeholder, or no-counts
# placeholder), so the rule never fails in count / upload / microarray / voom modes.
make_dir_heatmap <- function(direction, png_path, svg_path) {
  if (!has_counts) {
    save_placeholder(sprintf("%s-regulated heatmap needs the counts / VST matrix (unavailable for a DESeq2-results upload).", direction), png_path, svg_path)
    return(invisible(NULL))
  }
  keep <- !is.na(res$padj) & res$padj < alpha_thr & !is.na(res$log2FoldChange) &
    (if (identical(direction, "Up")) res$log2FoldChange >= lfc_thr else res$log2FoldChange <= -lfc_thr)
  ok <- which(keep)
  if (length(ok) < 2) {
    save_placeholder(sprintf("Fewer than 2 %s-regulated genes (padj < %.3g, |log2FC| >= %.2g).", tolower(direction), alpha_thr, lfc_thr), png_path, svg_path)
    return(invisible(NULL))
  }
  ord <- ok[order(res$padj[ok])]
  n_top <- min(heatmap_top, length(ord))
  top_names <- rownames(res)[head(ord, n_top)]
  hm <- assay(vsd)[top_names, , drop = FALSE]
  rownames(hm) <- label_for(top_names)
  hm <- t(scale(t(hm)))
  hm <- pmin(pmax(hm, -heatmap_zlim), heatmap_zlim)
  hm_breaks <- seq(-heatmap_zlim, heatmap_zlim, length.out = 256)
  ann <- as.data.frame(colData(dds)[, group_var, drop = FALSE])
  hm_levels <- unique(as.character(ann[[group_var]]))
  hm_ann_cols <- contrast_color_map(hm_levels, contrast_cfg, pal_spec$discrete)
  fs_row <- if (heatmap_fs_row > 0) heatmap_fs_row else max(4, base_size - 4)
  hm_key <- if (identical(direction, "Up")) "top_upregulated_heatmap" else "top_downregulated_heatmap"
  hm_min_dim <- fig_dim(hm_key)
  hm_canvas_w <- hm_min_dim[1]
  hm_cell_w <- heatmap_cell_w_fill(ncol(hm), hm_canvas_w,
                                   row_label_chars = max(nchar(rownames(hm))))
  ph <- pheatmap(hm, scale = "none", annotation_col = ann, show_rownames = TRUE,
                 show_colnames = sample_labels,  # hide sample names to declutter a many-sample run
                 labels_row = italic_labels(rownames(hm), gene_symbol_italic),
                 cluster_rows = nrow(hm) >= 2,  # heatmap_top_n = 1 -> 1 row; hclust needs >= 2
                 clustering_method = "ward.D2",
                 color = pal_spec$div(255), breaks = hm_breaks,
                 legend_breaks = c(-heatmap_zlim, 0, heatmap_zlim),
                 legend_labels = c(sprintf("%.1f", -heatmap_zlim), "0  (row z-score)", sprintf("%.1f", heatmap_zlim)),
                 annotation_colors = setNames(list(hm_ann_cols), group_var),
                 annotation_names_col = TRUE, cellheight = heatmap_cell_h,
                 cellwidth = hm_cell_w,
                 border_color = NA, fontsize = base_size, fontsize_row = fs_row, silent = TRUE)
  hm_render <- finalize_heatmap_gtable(ph$gtable, hm_min_dim[1], hm_min_dim[2])
  save_grid(hm_render$gtable, png_path, svg_path,
            w = hm_render$dim[1], h = hm_render$dim[2])
}
make_dir_heatmap("Up", out[["up_heatmap_png"]], out[["up_heatmap_svg"]])
make_dir_heatmap("Down", out[["down_heatmap_png"]], out[["down_heatmap_svg"]])

# ---- Raw p-value histogram (DE calibration check) ---------------------------
# A spike near 0 over a flat background indicates a well-calibrated test; a
# U-shape or hill flags a mis-specified design or residual confounding. Works
# identically for DESeq2 (results$pvalue) and limma (P.Value -> pvalue).
pv <- res$pvalue[!is.na(res$pvalue)]
if (length(pv) > 0) {
  pval_counts <- hist(
    pv[pv >= 0 & pv <= 1],
    breaks = seq(0, 1, length.out = 51),
    plot = FALSE, include.lowest = TRUE, right = FALSE
  )$counts
  pval_label_y <- max(pval_counts, na.rm = TRUE) * 0.97
  p_pval <- ggplot(data.frame(pvalue = pv), aes(pvalue)) +
    geom_histogram(boundary = 0, bins = 50, fill = pal_spec$discrete[1],
                   colour = "white", linewidth = 0.2, alpha = 0.9) +
    geom_vline(xintercept = alpha_thr, linetype = "dashed", colour = "grey40",
               linewidth = 0.3) +
    annotate("text", x = alpha_thr, y = pval_label_y,
             label = sprintf("raw p = %.3g", alpha_thr),
             hjust = -0.08, vjust = 1, colour = "grey25",
             family = if (is.null(base_family)) "" else base_family, size = 3) +
    labs(x = "raw p-value", y = "gene count") +
    style_theme(theme_bw)
  save_gg(p_pval, out[["pval_png"]], out[["pval_svg"]])
} else {
  save_placeholder("No p-values available", out[["pval_png"]], out[["pval_svg"]])
}

# ---- Model diagnostics (count backend only) ---------------------------------
# Dispersion fit, Cook's-distance outlier spread, and per-sample library size
# all come from the DESeqDataSet. On the microarray (limma) backend dds is a
# DESeqTransform, so these are emitted as labelled placeholders instead.
if (!is_intensity && has_counts) {
  # Dispersion: faithful ggplot re-expression of plotDispEsts (gene-wise estimate,
  # fitted trend, final shrunken value, flagged outliers) so it inherits the
  # shared theme/palette/font instead of base graphics.
  disp_df <- as.data.frame(mcols(dds))
  disp_df <- disp_df[!is.na(disp_df$dispGeneEst) & disp_df$baseMean > 0, ]
  disp_out <- if ("dispOutlier" %in% colnames(disp_df)) (disp_df$dispOutlier %in% TRUE) else rep(FALSE, nrow(disp_df))
  p_disp <- ggplot(disp_df, aes(baseMean, dispGeneEst)) +
    geom_point(colour = "grey55", size = ma_point, alpha = 0.5) +
    geom_point(aes(y = dispersion), colour = pal_spec$discrete[1], size = ma_point, alpha = 0.6) +
    geom_point(aes(y = dispFit), colour = pal_spec$discrete[2], size = max(0.3, ma_point * 0.8)) +
    scale_x_log10(labels = scales::label_log()) +
    scale_y_log10(labels = scales::label_log()) +
    labs(x = "mean of normalised counts", y = "dispersion") +
    style_theme(theme_bw)
  if (any(disp_out)) {
    p_disp <- p_disp +
      geom_point(data = disp_df[disp_out, ], aes(y = dispGeneEst),
                 shape = 21, fill = NA, colour = pal_spec$discrete[1],
                 size = ma_point + 1, stroke = 0.4)
  }
  disp_dim <- fig_dim("dispersion")
  save_gg(p_disp, out[["disp_png"]], out[["disp_svg"]], w = disp_dim[1], h = disp_dim[2])

  cooks <- tryCatch(assays(dds)[["cooks"]], error = function(e) NULL)
  if (!is.null(cooks)) {
    # Reshape Cook's distances to long form; plot log10 on a log axis (DESeq2
    # vignette idiom) without the ad-hoc +1 that compressed the low end.
    ck <- as.data.frame(cooks)
    ck_long <- utils::stack(ck)
    ck_long <- ck_long[is.finite(ck_long$values) & ck_long$values > 0, ]
    colnames(ck_long) <- c("cooks", "sample")
    p_cooks <- ggplot(ck_long, aes(sample, cooks)) +
      geom_boxplot(outlier.shape = NA, fill = pal_spec$discrete[1], alpha = 0.65,
                   colour = "grey25", linewidth = 0.3) +
      scale_y_log10(labels = scales::label_log()) +
      labs(x = NULL, y = "Cook's distance") +
      style_theme(theme_bw) +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
    cooks_dim <- fig_dim("cooks_distance")
    save_gg(p_cooks, out[["cooks_png"]], out[["cooks_svg"]], w = cooks_dim[1], h = cooks_dim[2])
  } else {
    save_placeholder("Cook's distances unavailable", out[["cooks_png"]], out[["cooks_svg"]])
  }
  libsz <- colSums(counts(dds))
  libdf <- data.frame(sample = names(libsz), reads = as.numeric(libsz))
  p_lib <- ggplot(libdf, aes(reads, reorder(sample, reads))) +
    geom_col(fill = pal_spec$discrete[1], alpha = 0.85, colour = "grey30", linewidth = 0.2) +
    scale_x_continuous(labels = scales::label_number(scale_cut = scales::cut_short_scale()),
                       expand = expansion(mult = c(0, 0.05))) +
    labs(x = "assigned reads (library size)", y = NULL) +
    style_theme(theme_bw)
  lib_dim <- fig_dim("library_size")
  save_gg(p_lib, out[["libsize_png"]], out[["libsize_svg"]], w = lib_dim[1], h = lib_dim[2])
} else {
  na_msg <- if (identical(assay_kind, "log2_intensity")) "Diagnostic not applicable (microarray)" else if (identical(assay_kind, "log2_cpm")) "Diagnostic not applicable (limma-voom logCPM backend)" else "Diagnostic needs the count model (unavailable for a DESeq2-results upload)"
  save_placeholder(na_msg, out[["disp_png"]], out[["disp_svg"]])
  save_placeholder(na_msg, out[["cooks_png"]], out[["cooks_svg"]])
  save_placeholder(na_msg, out[["libsize_png"]], out[["libsize_svg"]])
}

sink(type = "message")
close(log_con)
