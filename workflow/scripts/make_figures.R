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

palette_name <- as.character(getp("palette", "Blue-Red"))
point_size   <- as.numeric(getp("point_size", 2.5))
base_size    <- as.numeric(getp("base_font_size", 12))
font_family  <- as.character(getp("font_family", ""))
label_bold   <- isTRUE(as.logical(getp("label_bold", FALSE)))
title_bold   <- isTRUE(as.logical(getp("title_bold", FALSE)))
volcano_top  <- as.integer(getp("volcano_top_n", 15))
heatmap_top  <- as.integer(getp("heatmap_top_n", 30))
pca_ntop     <- as.integer(getp("pca_ntop", 500))
fig_w        <- as.numeric(getp("width_in", 6))
fig_h        <- as.numeric(getp("height_in", 5))
fig_dpi      <- as.integer(getp("dpi", 300))

# New figure-style fields (W2). All read NULL-safe so older configs still run.
scatter_alpha_fg <- as.numeric(getp("scatter_alpha_fg", 0.8))
scatter_alpha_bg <- as.numeric(getp("scatter_alpha_bg", 0.25))
pca_fixed_aspect <- isTRUE(as.logical(getp("pca_fixed_aspect", FALSE)))
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
  png(png_path, width = w, height = h, units = "in", res = fig_dpi)
  draw_grid(gtable); dev.off()
  svglite(svg_path, width = w, height = h)
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

# ---- Grouping factor (from the DESeq2 contrast; falls back safely) ----------
group_var <- "condition"
de_cfg <- tryCatch(snakemake@config[["deseq2"]], error = function(e) NULL)
if (is.list(de_cfg)) {
  cons <- de_cfg[["contrasts"]]
  if (is.list(cons) && length(cons) >= 1 && !is.null(cons[[1]][["factor"]])) {
    group_var <- as.character(cons[[1]][["factor"]])
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
p_pca <- ggplot(pca, aes(PC1, PC2, colour = group)) +
  geom_point(size = point_size, alpha = 0.9) +
  geom_text_repel(aes(label = name), family = base_family, size = 3, seed = 1,
                  min.segment.length = 0, box.padding = 0.5, point.padding = 0.3,
                  max.overlaps = Inf, segment.colour = "grey55", show.legend = FALSE) +
  scale_colour_manual(values = pal_spec$discrete, name = group_var) +
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
ann_cols <- setNames(pal_spec$discrete[seq_along(ann_levels)], ann_levels)
# Distance is non-negative and sequential, not diverging (no false midpoint).
cols <- pal_spec$seq(255)
ph <- pheatmap(mat, clustering_distance_rows = sampleDists,
               clustering_distance_cols = sampleDists,
               clustering_method = "ward.D2", col = cols,
               annotation_col = dist_ann,
               annotation_colors = setNames(list(ann_cols), group_var),
               annotation_names_col = FALSE, angle_col = 45,
               fontsize = base_size, silent = TRUE)
dist_dim <- fig_dim("sample_distance")
save_grid(ph$gtable, out[["dist_png"]], out[["dist_svg"]], w = dist_dim[1], h = dist_dim[2])
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

# Y cap: 0 = auto (quantile over finite-padj genes), then squish with pmin.
ycap <- as.numeric(getp("volcano_y_cap", 0))
if (ycap <= 0) {
  finite_y <- vol$neglog10padj[!was_inf]
  if (!length(finite_y)) finite_y <- vol$neglog10padj  # all-underflow guard
  ycap <- as.numeric(stats::quantile(finite_y, getp("volcano_y_cap_quantile", 0.995)))
}
# Cap only when underflow exists (Inf must be squished) or a finite outlier exceeds
# the cap by the headroom margin, so clean datasets (no extreme tail) stay un-capped.
do_cap <- any(was_inf) ||
  max(vol$neglog10padj) > ycap * (1 + as.numeric(getp("volcano_cap_headroom", 0.10)))
vol$y_plot <- if (do_cap) pmin(vol$neglog10padj, ycap) else vol$neglog10padj
vol$capped <- do_cap & vol$neglog10padj > ycap

vol$direction <- "n.s."
vol$direction[vol$padj < alpha_thr & vol$log2FoldChange >=  lfc_thr] <- "Up"
vol$direction[vol$padj < alpha_thr & vol$log2FoldChange <= -lfc_thr] <- "Down"
lab <- vol[vol$direction != "n.s.", ]
lab <- head(lab[order(lab$padj), ], volcano_top)

pal <- c(Up = pal_spec$discrete[2], Down = pal_spec$discrete[1], "n.s." = "grey80")
shp <- c(Up = 17, Down = 16, "n.s." = 16)

# Density-readable core: faint n.s. under, smaller/softer significant on top.
sig_size  <- max(0.6, point_size * as.numeric(getp("volcano_point_scale", 0.55)))
sig_alpha <- as.numeric(getp("volcano_point_alpha", 0.55))

xm   <- max(abs(vol$log2FoldChange))
ytop <- if (do_cap) {
  ycap * (1 + as.numeric(getp("volcano_cap_headroom", 0.10)))
} else max(vol$y_plot)

p_vol <- ggplot(vol, aes(log2FoldChange, y_plot)) +
  geom_vline(xintercept = c(-lfc_thr, lfc_thr), linetype = "dashed",
             colour = "grey60", linewidth = 0.3) +
  geom_hline(yintercept = -log10(alpha_thr), linetype = "dashed",
             colour = "grey60", linewidth = 0.3) +
  geom_point(data = subset(vol, direction == "n.s." & !capped),
             colour = "grey80", shape = 16,
             size = max(0.5, sig_size * 0.8), alpha = 0.4) +
  geom_point(data = subset(vol, direction != "n.s." & !capped),
             aes(colour = direction, shape = direction),
             size = sig_size, alpha = sig_alpha)

# Hollow up-triangle marks points pushed to the cap line (no data hidden).
if (any(vol$capped)) {
  p_vol <- p_vol +
    geom_point(data = subset(vol, capped), shape = 24, fill = "white",
               colour = "grey20", size = sig_size + 0.6, stroke = 0.4, alpha = 0.95)
}

p_vol <- p_vol +
  geom_text_repel(data = lab, aes(label = label, colour = direction),
                  family = base_family, size = 3, seed = 42,
                  max.overlaps = volcano_top + 5, box.padding = 0.5,
                  point.padding = 0.3, min.segment.length = 0,
                  segment.size = 0.3, segment.colour = "grey55",
                  force = 3, force_pull = 0.5,
                  ylim = c(-log10(alpha_thr), ytop), show.legend = FALSE) +
  scale_colour_manual(values = pal, name = NULL) +
  scale_shape_manual(values = shp, name = NULL) +
  coord_cartesian(xlim = c(-xm, xm), ylim = c(0, ytop), clip = "off") +
  labs(x = "log2 fold change",
       y = if (do_cap) "-log10 adjusted p (axis capped)" else "-log10 adjusted p") +
  style_theme(theme_bw) + theme(legend.position = "top")
vol_dim <- fig_dim("volcano")
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
hm_ann_cols <- setNames(pal_spec$discrete[seq_along(hm_levels)], hm_levels)
fs_row <- if (heatmap_fs_row > 0) heatmap_fs_row else max(4, base_size - 4)
ph2 <- pheatmap(hm, scale = "none", annotation_col = ann, show_rownames = TRUE,
                clustering_method = "ward.D2",
                color = pal_spec$div(255), breaks = hm_breaks,
                legend_breaks = c(-zlim, 0, zlim),
                legend_labels = c(sprintf("%.1f", -zlim), "0  (row z-score)", sprintf("%.1f", zlim)),
                annotation_colors = setNames(list(hm_ann_cols), group_var),
                annotation_names_col = FALSE, cellheight = heatmap_cell_h,
                border_color = NA, fontsize = base_size, fontsize_row = fs_row, silent = TRUE)
# Drive height from the row count so labels never collide and the canvas scales
# with heatmap_top_n; honour an explicit size override when set.
hm_h <- (n_top * heatmap_cell_h) / 72 + 2
hm_dim <- if (!is.null(size_overrides[["top_deg_heatmap"]])) {
  fig_dim("top_deg_heatmap")
} else c(fig_w, max(hm_h, fig_h))
save_grid(ph2$gtable, out[["heatmap_png"]], out[["heatmap_svg"]], w = hm_dim[1], h = hm_dim[2])
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
  hm_ann_cols <- setNames(pal_spec$discrete[seq_along(hm_levels)], hm_levels)
  fs_row <- if (heatmap_fs_row > 0) heatmap_fs_row else max(4, base_size - 4)
  ph <- pheatmap(hm, scale = "none", annotation_col = ann, show_rownames = TRUE,
                 clustering_method = "ward.D2",
                 color = pal_spec$div(255), breaks = hm_breaks,
                 legend_breaks = c(-heatmap_zlim, 0, heatmap_zlim),
                 legend_labels = c(sprintf("%.1f", -heatmap_zlim), "0  (row z-score)", sprintf("%.1f", heatmap_zlim)),
                 annotation_colors = setNames(list(hm_ann_cols), group_var),
                 annotation_names_col = FALSE, cellheight = heatmap_cell_h,
                 border_color = NA, fontsize = base_size, fontsize_row = fs_row, silent = TRUE)
  hm_h <- (n_top * heatmap_cell_h) / 72 + 2
  save_grid(ph$gtable, png_path, svg_path, w = fig_w, h = max(hm_h, fig_h))
}
make_dir_heatmap("Up", out[["up_heatmap_png"]], out[["up_heatmap_svg"]])
make_dir_heatmap("Down", out[["down_heatmap_png"]], out[["down_heatmap_svg"]])

# ---- Raw p-value histogram (DE calibration check) ---------------------------
# A spike near 0 over a flat background indicates a well-calibrated test; a
# U-shape or hill flags a mis-specified design or residual confounding. Works
# identically for DESeq2 (results$pvalue) and limma (P.Value -> pvalue).
pv <- res$pvalue[!is.na(res$pvalue)]
if (length(pv) > 0) {
  p_pval <- ggplot(data.frame(pvalue = pv), aes(pvalue)) +
    geom_histogram(boundary = 0, bins = 50, fill = pal_spec$discrete[1],
                   colour = "white", linewidth = 0.2, alpha = 0.9) +
    geom_vline(xintercept = alpha_thr, linetype = "dashed", colour = "grey40",
               linewidth = 0.3) +
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
