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
  library(svglite)
})

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- readRDS(snakemake@input[["rds"]])
dds <- obj$dds; res <- obj$res; resLFC <- obj$resLFC; vsd <- obj$vsd
out <- snakemake@output
# Microarray (limma) backend: baseMean is average log2 intensity, not a count
# mean, so count-scale transforms (log10 on baseMean) are skipped below.
is_intensity <- identical(tryCatch(obj$assay_kind, error = function(e) NULL), "log2_intensity")

# ---- Style parameters (NULL-safe) ------------------------------------------
# Read from the rule's declared params (a Snakemake rerun trigger); fall back to
# config for older invocations that did not pass the style as a param.
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (is.null(style) || !is.list(style)) {
  style <- tryCatch(snakemake@config[["figures_style"]], error = function(e) NULL)
}
if (is.null(style) || !is.list(style)) style <- list()
getp <- function(key, default) {
  v <- style[[key]]
  if (is.null(v) || (is.character(v) && length(v) == 1 && !nzchar(v) && !is.character(default))) default else v
}

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

# ---- Palette helper ---------------------------------------------------------
# One named palette drives both discrete scales (PCA, volcano) and continuous
# colour ramps (sample-distance and top-DEG heatmaps). Viridis stops are
# hardcoded so no extra package is required in the env.
VIRIDIS_STOPS <- c("#440154", "#414487", "#2A788E", "#22A884", "#7AD151", "#FDE725")
palette_spec <- function(name) {
  if (identical(name, "Greyscale")) {
    list(discrete = c("#1A1A1A", "#7F7F7F", "#BFBFBF", "#4D4D4D", "#A6A6A6"),
         ramp = colorRampPalette(c("#F7F7F7", "#525252", "#000000")),
         diverging = colorRampPalette(rev(brewer.pal(9, "Greys"))))
  } else if (identical(name, "Viridis")) {
    list(discrete = c("#440154", "#21908C", "#FDE725", "#3B528B", "#5DC863"),
         ramp = colorRampPalette(VIRIDIS_STOPS),
         diverging = colorRampPalette(VIRIDIS_STOPS))
  } else {
    list(discrete = c("#2C7BB6", "#C0392B", "#2E7D32", "#B26A00", "#6A1B9A"),
         ramp = colorRampPalette(c("#2C7BB6", "white", "#C0392B")),
         diverging = colorRampPalette(rev(brewer.pal(9, "Greys"))))
  }
}
pal_spec <- palette_spec(palette_name)

# ---- Shared theme -----------------------------------------------------------
base_family <- if (nzchar(font_family)) font_family else NULL
style_theme <- function(base = theme_bw) {
  t <- if (is.null(base_family)) base(base_size = base_size) else base(base_size = base_size, base_family = base_family)
  extra <- theme()
  if (label_bold) extra <- extra + theme(axis.text = element_text(face = "bold"))
  if (title_bold) extra <- extra + theme(axis.title = element_text(face = "bold"))
  t + extra
}

save_gg <- function(plot, png_path, svg_path, w = fig_w, h = fig_h) {
  ggsave(png_path, plot, width = w, height = h, dpi = fig_dpi)
  ggsave(svg_path, plot, width = w, height = h)
}

save_grid <- function(gtable, png_path, svg_path, w = fig_w, h = fig_h) {
  png(png_path, width = w, height = h, units = "in", res = fig_dpi)
  grid::grid.newpage(); grid::grid.draw(gtable); dev.off()
  svglite(svg_path, width = w, height = h)
  grid::grid.newpage(); grid::grid.draw(gtable); dev.off()
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
if (!(group_var %in% colnames(colData(dds)))) group_var <- colnames(colData(dds))[1]

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
pca <- plotPCA(vsd, intgroup = group_var, ntop = pca_ntop, returnData = TRUE)
pv <- round(100 * attr(pca, "percentVar"))
p_pca <- ggplot(pca, aes(PC1, PC2, colour = group)) +
  geom_point(size = point_size, alpha = 0.9) +
  geom_text_repel(aes(label = name), size = 3, seed = 1, show.legend = FALSE) +
  scale_colour_manual(values = pal_spec$discrete) +
  labs(x = paste0("PC1 (", pv[1], "%)"), y = paste0("PC2 (", pv[2], "%)")) +
  style_theme(theme_bw)
# No coord_fixed(): when PC1 dominates (e.g. 98% vs 1%) a 1:1 aspect squeezes the
# plot into a thin band, so let the points fill the (near-square) panel instead.
save_gg(p_pca, out[["pca_png"]], out[["pca_svg"]])

# ---- Sample-distance heatmap -----------------------------------------------
sampleDists <- dist(t(assay(vsd)), method = "euclidean")
mat <- as.matrix(sampleDists)
rownames(mat) <- colnames(mat) <- paste(colData(vsd)[[group_var]], colnames(vsd), sep = " | ")
cols <- pal_spec$diverging(255)
ph <- pheatmap(mat, clustering_distance_rows = sampleDists,
               clustering_distance_cols = sampleDists,
               clustering_method = "ward.D2", col = cols,
               fontsize = base_size, silent = TRUE)
save_grid(ph$gtable, out[["dist_png"]], out[["dist_svg"]])

# ---- MA plot ----------------------------------------------------------------
# The MA plot is dense; scale the configured point size down so it stays legible.
ma_point <- max(0.3, point_size * 0.4)
ma <- as.data.frame(resLFC)
ma <- ma[!is.na(ma$padj), ]
ma$sig <- ma$padj < alpha_thr
p_ma <- ggplot(ma, aes(baseMean, log2FoldChange, colour = sig)) +
  geom_point(size = ma_point, alpha = 0.6) +
  geom_hline(yintercept = 0, colour = "grey40") +
  scale_colour_manual(values = c("FALSE" = "grey75", "TRUE" = pal_spec$discrete[1]),
                      name = sprintf("padj < %.3g", alpha_thr)) +
  labs(x = if (is_intensity) "average log2 expression" else "mean of normalised counts",
       y = "log2 fold change") +
  style_theme(theme_bw)
# Counts span orders of magnitude (log x); log2 intensities do not.
if (!is_intensity) p_ma <- p_ma + scale_x_log10()
save_gg(p_ma, out[["ma_png"]], out[["ma_svg"]])

# ---- Volcano ----------------------------------------------------------------
vol <- as.data.frame(resLFC)
vol$gene <- rownames(vol)
vol <- vol[!is.na(vol$padj), ]
vol$neglog10padj <- -log10(vol$padj)
vol$direction <- "n.s."
vol$direction[vol$padj < alpha_thr & vol$log2FoldChange >= lfc_thr] <- "Up"
vol$direction[vol$padj < alpha_thr & vol$log2FoldChange <= -lfc_thr] <- "Down"
lab <- vol[vol$direction != "n.s.", ]
lab <- head(lab[order(lab$padj), ], volcano_top)
pal <- c(Up = pal_spec$discrete[2], Down = pal_spec$discrete[1], "n.s." = "grey80")
shp <- c(Up = 17, Down = 16, "n.s." = 16)
p_vol <- ggplot(vol, aes(log2FoldChange, neglog10padj)) +
  geom_vline(xintercept = c(-lfc_thr, lfc_thr), linetype = "dashed", colour = "grey60", linewidth = 0.3) +
  geom_hline(yintercept = -log10(alpha_thr), linetype = "dashed", colour = "grey60", linewidth = 0.3) +
  geom_point(aes(colour = direction, shape = direction), size = point_size, alpha = 0.8) +
  geom_text_repel(data = lab, aes(label = gene), size = 3, seed = 42, max.overlaps = Inf) +
  scale_colour_manual(values = pal, name = NULL) +
  scale_shape_manual(values = shp, name = NULL) +
  labs(x = "log2 fold change", y = "-log10 adjusted p") +
  style_theme(theme_classic) + theme(legend.position = "top")
save_gg(p_vol, out[["volcano_png"]], out[["volcano_svg"]])

# ---- Top-DEG heatmap --------------------------------------------------------
n_top <- min(heatmap_top, nrow(res))
top <- head(order(res$padj), n_top)
hm <- assay(vsd)[top, , drop = FALSE]
hm <- t(scale(t(hm)))
ann <- as.data.frame(colData(dds)[, group_var, drop = FALSE])
ph2 <- pheatmap(hm, scale = "none", annotation_col = ann, show_rownames = TRUE,
                clustering_method = "ward.D2",
                color = pal_spec$ramp(255),
                border_color = NA, fontsize = base_size, fontsize_row = 7, silent = TRUE)
save_grid(ph2$gtable, out[["heatmap_png"]], out[["heatmap_svg"]], w = fig_w, h = max(fig_h, 7))

sink(type = "message")
close(log_con)
