# Per-study figures + tables for a multi-study meta-analysis. Reuses the vsd persisted by
# run_meta_analysis.R and the per_study_<S>.csv DE tables; produces, per admissible study S:
#   tables/  de_results.csv  upregulated.csv  downregulated.csv  summary.csv
#   figures/ volcano  ma_plot  pval_hist  pca  heatmap_topdeg   (each PNG + SVG)
#   index.html  (links its own figures/tables)
# and a single declared target results/meta/per_study/manifest.json listing every study. No
# parse-time expand(): studies are discovered on-disk, so excluded studies simply never appear.

local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

suppressMessages({
  library(DESeq2)
  library(ggplot2)
  library(ggrepel)
  library(pheatmap)
  library(RColorBrewer)
  library(svglite)
  library(jsonlite)
})
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt"); sink(log_con, type = "message")

results_csv <- snakemake@input[["results"]]
de_csv      <- tryCatch(snakemake@input[["de_results"]], error = function(e) "")
out_manifest <- snakemake@output[["manifest"]]
alpha   <- as.numeric(tryCatch(snakemake@params[["alpha"]], error = function(e) 0.05))
lfc_thr <- as.numeric(tryCatch(snakemake@params[["lfc_threshold"]], error = function(e) 1.0))
style   <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()

meta_dir <- dirname(results_csv)                 # results/meta
per_dir  <- dirname(out_manifest)                # results/meta/per_study
dir.create(per_dir, recursive = TRUE, showWarnings = FALSE)

# --- style (inherit the comparative_meta figure group, else global) ------------------------------
gp <- getp_for(style, "comparative_meta")
base_size <- { v <- suppressWarnings(as.numeric(gp("base_font_size", 12))); if (length(v) != 1 || is.na(v)) 12 else v }
pal_spec  <- palette_spec(as.character(gp("palette", "Blue-Red")))
base_family <- resolve_font(as.character(gp("font_family", "")))
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = isTRUE(as.logical(gp("label_bold", FALSE))),
                                title_bold = isTRUE(as.logical(gp("title_bold", FALSE))))
save_gg    <- make_save_gg(fig_dpi = { v <- suppressWarnings(as.numeric(gp("dpi", 300))); if (is.na(v)) 300 else v })
point_size <- { v <- suppressWarnings(as.numeric(gp("point_size", 2.5))); if (is.na(v)) 2.5 else v }
italic_genes <- { v <- style[["gene_symbol_italic"]]; if (is.null(v)) TRUE else isTRUE(as.logical(v)) }
top_n  <- as.integer({ v <- suppressWarnings(as.numeric(gp("volcano_top_n", 15))); if (is.na(v)) 15 else v })
hm_top <- as.integer({ v <- suppressWarnings(as.numeric(gp("heatmap_top_n", 30))); if (is.na(v)) 30 else v })
DIR_COL <- c(Up = pal_spec$discrete[2], Down = pal_spec$discrete[1], "n.s." = "grey80")

# gene_id -> symbol map from the joint DE table (universal source across engines)
sym_map <- NULL
if (length(de_csv) && nzchar(de_csv[[1]]) && file.exists(de_csv[[1]])) {
  de0 <- tryCatch(read.csv(de_csv[[1]], stringsAsFactors = FALSE), error = function(e) NULL)
  if (!is.null(de0) && all(c("gene_id", "symbol") %in% names(de0))) sym_map <- setNames(de0$symbol, de0$gene_id)
}
gsym <- function(ids) { if (is.null(sym_map)) return(ids); s <- unname(sym_map[ids]); ifelse(is.na(s) | !nzchar(s), ids, s) }

placeholder <- function(txt) ggplot() + annotate("text", x = 0, y = 0, label = txt, size = 4) + theme_void()

# ---- per-figure builders ------------------------------------------------------------------------
UFLOOR <- 320  # -log10(.Machine$double.xmin) ~ 307.6; padj==0 -> finite ceiling + off-scale triangle

build_volcano <- function(d) {
  d$neg <- -log10(pmax(d$padj, .Machine$double.xmin)); d$neg[!is.finite(d$neg)] <- UFLOOR
  d$capped <- d$padj == 0 & !is.na(d$padj)
  lab <- d[d$direction != "n.s." & !is.na(d$padj), , drop = FALSE]
  lab <- head(lab[order(lab$padj), , drop = FALSE], top_n)
  p <- ggplot(d, aes(log2FoldChange, neg, colour = direction)) +
    geom_vline(xintercept = c(-lfc_thr, lfc_thr), linetype = 2, colour = "grey60", linewidth = 0.3) +
    geom_hline(yintercept = -log10(alpha), linetype = 2, colour = "grey60", linewidth = 0.3) +
    geom_point(aes(shape = capped), alpha = 0.55, size = max(0.6, point_size * 0.55)) +
    scale_colour_manual(values = DIR_COL, drop = FALSE, name = NULL) +
    scale_shape_manual(values = c(`FALSE` = 16, `TRUE` = 17), guide = "none") +
    labs(x = "log2 fold change", y = "-log10 FDR") + style_theme()
  if (nrow(lab)) p <- p + geom_text_repel(data = lab,
      mapping = aes(x = log2FoldChange, y = neg, label = gsym(gene_id)), inherit.aes = FALSE,
      size = 2.8, fontface = if (italic_genes) 3 else 1, max.overlaps = 20,
      min.segment.length = 0, seed = 42)
  p
}
build_ma <- function(d) {
  d$sig <- ifelse(!is.na(d$padj) & d$padj < alpha, d$direction, "n.s.")
  ggplot(d, aes(baseMean, log2FoldChange, colour = sig)) +
    geom_hline(yintercept = 0, colour = "grey50", linewidth = 0.3) +
    geom_point(alpha = 0.5, size = max(0.5, point_size * 0.5)) +
    scale_x_log10() + scale_colour_manual(values = DIR_COL, drop = FALSE, name = NULL) +
    labs(x = "mean of normalized counts", y = "log2 fold change") + style_theme()
}
build_phist <- function(d) {
  ggplot(d[!is.na(d$pvalue), , drop = FALSE], aes(pvalue)) +
    geom_histogram(boundary = 0, bins = 40, fill = pal_spec$discrete[1], colour = "white", linewidth = 0.2) +
    labs(x = "p-value", y = "gene count") + style_theme()
}

studies <- sub("^per_study_(.*)\\.csv$", "\\1",
               basename(list.files(meta_dir, pattern = "^per_study_.*\\.csv$")))
# The .csv anchor already excludes the per_study_vsd_*.rds sidecars, so no _vsd_ filter is
# needed -- and filtering on it would wrongly drop a legitimate study whose id contains '_vsd_'.
studies <- studies[nzchar(studies)]

# Drop stale per-study subdirectory trees left by a study that was removed from the sheet:
# the GUI figure gallery globs per_study/*/figures, so an old study's figures would otherwise
# surface as current. Only <STUDY>/ subdirs are removed; the manifest json files are kept.
for (d in list.dirs(per_dir, recursive = FALSE, full.names = TRUE))
  if (!(basename(d) %in% studies)) unlink(d, recursive = TRUE)

manifest <- list()
for (s in studies) {
  de <- tryCatch(read.csv(file.path(meta_dir, paste0("per_study_", s, ".csv")), stringsAsFactors = FALSE),
                 error = function(e) NULL)
  if (is.null(de) || !("gene_id" %in% names(de)) || !nrow(de)) next
  sdir <- file.path(per_dir, s); tdir <- file.path(sdir, "tables"); fdir <- file.path(sdir, "figures")
  dir.create(tdir, recursive = TRUE, showWarnings = FALSE); dir.create(fdir, recursive = TRUE, showWarnings = FALSE)

  de$symbol <- gsym(de$gene_id)
  de$direction <- "n.s."
  sig <- !is.na(de$padj) & de$padj < alpha
  de$direction[sig & de$log2FoldChange >=  lfc_thr] <- "Up"
  de$direction[sig & de$log2FoldChange <= -lfc_thr] <- "Down"
  de$direction <- factor(de$direction, levels = c("Up", "Down", "n.s."))

  cols <- c("gene_id", "symbol", "baseMean", "log2FoldChange", "lfcSE", "pvalue", "padj", "direction")
  ord  <- order(de$padj)
  write.csv(de[ord, cols], file.path(tdir, "de_results.csv"), row.names = FALSE)
  up <- de[which(de$direction == "Up"), cols]; down <- de[which(de$direction == "Down"), cols]
  write.csv(up[order(up$padj), ],   file.path(tdir, "upregulated.csv"),   row.names = FALSE)
  write.csv(down[order(down$padj), ], file.path(tdir, "downregulated.csv"), row.names = FALSE)
  n_up <- nrow(up); n_down <- nrow(down); n_tested <- sum(!is.na(de$padj))
  write.csv(data.frame(study = s, n_tested = n_tested, n_up = n_up, n_down = n_down,
                       alpha = alpha, lfc_threshold = lfc_thr),
            file.path(tdir, "summary.csv"), row.names = FALSE)

  save_gg(build_volcano(de), file.path(fdir, "volcano.png"),   file.path(fdir, "volcano.svg"),   w = 7, h = 6)
  save_gg(build_ma(de),      file.path(fdir, "ma_plot.png"),   file.path(fdir, "ma_plot.svg"),   w = 7, h = 5)
  save_gg(build_phist(de),   file.path(fdir, "pval_hist.png"), file.path(fdir, "pval_hist.svg"), w = 6, h = 4)

  # PCA + top-DEG heatmap from the persisted vsd (absent -> graceful placeholder, study still listed)
  vsd_path <- file.path(meta_dir, paste0("per_study_vsd_", s, ".rds"))
  has_vsd <- file.exists(vsd_path)
  if (has_vsd) {
    vsd <- tryCatch(readRDS(vsd_path), error = function(e) NULL)
  } else vsd <- NULL
  if (!is.null(vsd)) {
    grp <- tryCatch(as.character(colData(vsd)[["condition"]]), error = function(e) NULL)
    if (is.null(grp)) grp <- rep("sample", ncol(vsd))
    pcadata <- tryCatch({
      pv <- assay(vsd); rv <- matrixStats::rowVars(pv)
      sel <- head(order(rv, decreasing = TRUE), min(500, nrow(pv)))
      pc <- prcomp(t(pv[sel, , drop = FALSE]))
      pct <- round(100 * pc$sdev^2 / sum(pc$sdev^2), 1)
      list(df = data.frame(PC1 = pc$x[, 1], PC2 = pc$x[, 2], group = grp), pct = pct)
    }, error = function(e) NULL)
    if (!is.null(pcadata)) {
      gc2 <- setNames(rep(pal_spec$discrete, length.out = length(unique(pcadata$df$group))), sort(unique(pcadata$df$group)))
      p_pca <- ggplot(pcadata$df, aes(PC1, PC2, colour = group)) + geom_point(size = point_size) +
        scale_colour_manual(values = gc2, name = NULL) +
        labs(x = sprintf("PC1 (%.1f%%)", pcadata$pct[1]), y = sprintf("PC2 (%.1f%%)", pcadata$pct[2])) + style_theme()
      save_gg(p_pca, file.path(fdir, "pca.png"), file.path(fdir, "pca.svg"), w = 6, h = 5)
    } else { save_gg(placeholder("PCA unavailable"), file.path(fdir, "pca.png"), file.path(fdir, "pca.svg")) }

    # top-DEG z-scored heatmap
    sig_ids <- de$gene_id[which(de$direction != "n.s.")]
    sig_ids <- sig_ids[order(de$padj[match(sig_ids, de$gene_id)])]
    sig_ids <- head(intersect(sig_ids, rownames(vsd)), hm_top)
    if (length(sig_ids) >= 2) {
      mat <- assay(vsd)[sig_ids, , drop = FALSE]; rownames(mat) <- gsym(rownames(mat))
      mat <- t(scale(t(mat)))
      ann <- tryCatch(as.data.frame(colData(vsd)[, "condition", drop = FALSE]), error = function(e) NULL)
      wh <- heatmap_dim(nrow(mat), ncol(mat), row_label_chars = max(nchar(rownames(mat))), max_h = Inf)
      ph <- pheatmap(mat, scale = "none", annotation_col = ann, show_rownames = TRUE,
                     labels_row = italic_labels(rownames(mat), italic_genes),
                     cluster_rows = all(is.finite(mat)), clustering_method = "ward.D2",
                     color = pal_spec$div(255), border_color = NA, fontsize = base_size,
                     fontsize_row = 8, silent = TRUE)
      png(file.path(fdir, "heatmap_topdeg.png"), width = wh[1], height = wh[2], units = "in", res = 300)
      grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
      svglite(file.path(fdir, "heatmap_topdeg.svg"), width = wh[1], height = wh[2])
      grid::grid.newpage(); grid::grid.draw(ph$gtable); dev.off()
    } else {
      save_gg(placeholder("< 2 DEGs for heatmap"), file.path(fdir, "heatmap_topdeg.png"), file.path(fdir, "heatmap_topdeg.svg"))
    }
  } else {
    for (nm in c("pca", "heatmap_topdeg"))
      save_gg(placeholder("per-sample counts unavailable"), file.path(fdir, paste0(nm, ".png")), file.path(fdir, paste0(nm, ".svg")))
  }

  figs <- c("volcano", "ma_plot", "pval_hist", "pca", "heatmap_topdeg")
  # self-contained index.html (relative img src; opened from the results tree)
  rows <- paste0(sprintf('<figure><img src="figures/%s.png" alt="%s"><figcaption>%s</figcaption></figure>', figs, figs, figs), collapse = "\n")
  html <- sprintf('<!doctype html><html><head><meta charset="utf-8"><title>%s — per-study</title>
<style>body{font-family:Inter,system-ui,sans-serif;margin:2rem;max-width:1100px}figure{display:inline-block;margin:.5rem;vertical-align:top}img{max-width:520px;border:1px solid #eee}figcaption{font:600 .8rem monospace;color:#555}a{color:#2C7BB6}</style></head>
<body><h1>%s</h1><p>%d genes tested · %d up · %d down (FDR&lt;%.3g, |log2FC|&ge;%.2g)</p>
<h2>Figures</h2>%s
<h2>Tables</h2><ul><li><a href="tables/de_results.csv">de_results.csv</a></li><li><a href="tables/upregulated.csv">upregulated.csv</a></li><li><a href="tables/downregulated.csv">downregulated.csv</a></li><li><a href="tables/summary.csv">summary.csv</a></li></ul></body></html>',
    s, s, n_tested, n_up, n_down, alpha, lfc_thr, rows)
  writeLines(html, file.path(sdir, "index.html"))

  manifest[[length(manifest) + 1]] <- list(study = s, n_tested = n_tested, n_up = n_up, n_down = n_down,
    has_vsd = !is.null(vsd), dir = file.path("results/meta/per_study", s), index = file.path("results/meta/per_study", s, "index.html"),
    figures = as.list(figs), tables = list("de_results.csv", "upregulated.csv", "downregulated.csv", "summary.csv"))
}

write_json(list(n_studies = length(manifest), alpha = alpha, lfc_threshold = lfc_thr, studies = manifest),
           out_manifest, auto_unbox = TRUE, pretty = TRUE)
sink(type = "message"); close(log_con)
