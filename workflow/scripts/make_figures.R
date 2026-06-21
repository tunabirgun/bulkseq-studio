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
MAGMA_STOPS <- c("#000004", "#51127C", "#B63679", "#FB8861", "#FCFDBF")
PLASMA_STOPS <- c("#0D0887", "#7E03A8", "#CC4778", "#F89540", "#F0F921")
CIVIDIS_STOPS <- c("#00204D", "#414D6B", "#7C7B78", "#BCAF6F", "#FFEA46")
.uniform_spec <- function(stops) {
  list(discrete = stops, ramp = colorRampPalette(stops), diverging = colorRampPalette(stops))
}
palette_spec <- function(name) {
  if (identical(name, "Greyscale")) {
    list(discrete = c("#1A1A1A", "#7F7F7F", "#BFBFBF", "#4D4D4D", "#A6A6A6"),
         ramp = colorRampPalette(c("#F7F7F7", "#525252", "#000000")),
         diverging = colorRampPalette(rev(brewer.pal(9, "Greys"))))
  } else if (identical(name, "Viridis")) {
    list(discrete = c("#440154", "#21908C", "#FDE725", "#3B528B", "#5DC863"),
         ramp = colorRampPalette(VIRIDIS_STOPS),
         diverging = colorRampPalette(VIRIDIS_STOPS))
  } else if (identical(name, "Magma")) {
    .uniform_spec(MAGMA_STOPS)
  } else if (identical(name, "Plasma")) {
    .uniform_spec(PLASMA_STOPS)
  } else if (identical(name, "Cividis")) {
    .uniform_spec(CIVIDIS_STOPS)
  } else if (identical(name, "Spectral")) {
    list(discrete = brewer.pal(8, "Dark2"),
         ramp = colorRampPalette(rev(brewer.pal(11, "Spectral"))),
         diverging = colorRampPalette(brewer.pal(11, "Spectral")))
  } else if (identical(name, "Red-Yellow-Blue")) {
    list(discrete = brewer.pal(8, "Set2"),
         ramp = colorRampPalette(rev(brewer.pal(11, "RdYlBu"))),
         diverging = colorRampPalette(brewer.pal(11, "RdYlBu")))
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
vol$label <- label_for(vol$gene)
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
  geom_text_repel(data = lab, aes(label = label), size = 3, seed = 42, max.overlaps = Inf) +
  scale_colour_manual(values = pal, name = NULL) +
  scale_shape_manual(values = shp, name = NULL) +
  labs(x = "log2 fold change", y = "-log10 adjusted p") +
  style_theme(theme_classic) + theme(legend.position = "top")
save_gg(p_vol, out[["volcano_png"]], out[["volcano_svg"]])

# ---- Top-DEG heatmap --------------------------------------------------------
# Drop NA padj first (order() puts NA last, so a naive head() would pull in NA
# rows), then index assay(vsd) BY NAME so the heatmap is robust to any row-order
# difference between res and vsd.
ok <- which(!is.na(res$padj))
ord <- ok[order(res$padj[ok])]
n_top <- min(heatmap_top, length(ord))
top_names <- rownames(res)[head(ord, n_top)]
hm <- assay(vsd)[top_names, , drop = FALSE]
rownames(hm) <- label_for(top_names)
hm <- t(scale(t(hm)))
ann <- as.data.frame(colData(dds)[, group_var, drop = FALSE])
ph2 <- pheatmap(hm, scale = "none", annotation_col = ann, show_rownames = TRUE,
                clustering_method = "ward.D2",
                color = pal_spec$ramp(255),
                border_color = NA, fontsize = base_size, fontsize_row = 7, silent = TRUE)
save_grid(ph2$gtable, out[["heatmap_png"]], out[["heatmap_svg"]], w = fig_w, h = max(fig_h, 7))

# ---- Raw p-value histogram (DE calibration check) ---------------------------
# A spike near 0 over a flat background indicates a well-calibrated test; a
# U-shape or hill flags a mis-specified design or residual confounding. Works
# identically for DESeq2 (results$pvalue) and limma (P.Value -> pvalue).
pv <- res$pvalue[!is.na(res$pvalue)]
if (length(pv) > 0) {
  p_pval <- ggplot(data.frame(pvalue = pv), aes(pvalue)) +
    geom_histogram(boundary = 0, bins = 50, fill = pal_spec$discrete[1],
                   colour = "white", linewidth = 0.2) +
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
if (!is_intensity) {
  save_base(function() plotDispEsts(dds), out[["disp_png"]], out[["disp_svg"]])
  cooks <- tryCatch(assays(dds)[["cooks"]], error = function(e) NULL)
  if (!is.null(cooks)) {
    save_base(function() {
      op <- par(mar = c(8, 4.5, 2, 1)); on.exit(par(op))
      boxplot(log10(cooks + 1), las = 2, outline = FALSE,
              ylab = "log10(Cook's distance + 1)", col = pal_spec$discrete[1])
    }, out[["cooks_png"]], out[["cooks_svg"]])
  } else {
    save_placeholder("Cook's distances unavailable", out[["cooks_png"]], out[["cooks_svg"]])
  }
  libsz <- colSums(counts(dds))
  libdf <- data.frame(sample = names(libsz), reads = as.numeric(libsz))
  p_lib <- ggplot(libdf, aes(reads, reorder(sample, reads))) +
    geom_col(fill = pal_spec$discrete[1]) +
    labs(x = "assigned reads (library size)", y = NULL) +
    style_theme(theme_bw)
  save_gg(p_lib, out[["libsize_png"]], out[["libsize_svg"]])
} else {
  na_msg <- "Diagnostic not applicable (microarray)"
  save_placeholder(na_msg, out[["disp_png"]], out[["disp_svg"]])
  save_placeholder(na_msg, out[["cooks_png"]], out[["cooks_svg"]])
  save_placeholder(na_msg, out[["libsize_png"]], out[["libsize_svg"]])
}

sink(type = "message")
close(log_con)
