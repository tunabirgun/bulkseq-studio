# Comparative multi-study meta-analysis figures + tables (0.21.0). Reads the meta engine's
# results/meta/meta_analysis_results.csv (+ sibling per_study_<S>.csv for lfcSE, + optional
# results/enrichment/id_map.csv for gene symbols) and emits, all themed via figure_style.R:
#   figures : meta_volcano, meta_forest, meta_concordance_scatter, meta_convergent_heatmap,
#             meta_heterogeneity (k>=3 only), meta_combined_p_hist, meta_integration_gain
#   tables  : meta_convergent_genes.csv, meta_study_summary.csv + meta_analysis_summary.json
# Every figure degrades to a labelled placeholder so the rule always writes its declared outputs
# and never breaks the run (handles the empty-result backstop: nrow==0). k=2 is the primary case
# (fixed-effect pooling: tau2/I2/QEp are NA) so heterogeneity figures gate on k>=3.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})
suppressMessages({ library(ggplot2); library(svglite); library(scales); library(RColorBrewer); library(ggrepel) })
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt"); sink(log_con, type = "message")
out <- snakemake@output
style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style); gp <- getp_for(style, "comparative_meta")
alpha <- as.numeric(tryCatch(snakemake@params[["alpha"]], error = function(e) 0.05))
lfc_thr <- as.numeric(tryCatch(snakemake@params[["lfc_threshold"]], error = function(e) 1.0))
n_forest <- as.integer(tryCatch(snakemake@params[["n_forest"]], error = function(e) 6))
fig_dpi <- as.integer(getp("dpi", 300))
base_size <- as.numeric(gp("base_font_size", 12)); font_family <- as.character(gp("font_family", ""))
palette_name <- as.character(gp("palette", "Blue-Red"))
italic_genes <- isTRUE(as.logical(getp("italic_gene_labels", TRUE)))
pal_spec <- palette_spec(palette_name)
base_family <- resolve_font(font_family)
style_theme <- make_style_theme(base_size = base_size, base_family = base_family)
save_gg <- make_save_gg(fig_w = as.numeric(gp("width_in", 7)), fig_h = as.numeric(gp("height_in", 6)), fig_dpi = fig_dpi)
# Fixed direction colour map reused across volcano / scatter / heatmap so sign never flips meaning.
DIR_COL <- c(up = pal_spec$discrete[2], down = pal_spec$discrete[1], discordant = "grey70")

save_placeholder <- function(png_path, svg_path, msg) {
  p <- ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 4.2, family = base_family) +
    theme_void() + xlim(-1, 1) + ylim(-1, 1)
  tryCatch(save_gg(p, png_path, svg_path), error = function(e) message("placeholder save failed: ", conditionMessage(e)))
}
emit <- function(png_path, svg_path, expr, empty_msg, w = NULL, h = NULL) {
  p <- tryCatch(expr, error = function(e) { message("figure failed: ", conditionMessage(e)); NULL })
  if (is.null(p)) return(save_placeholder(png_path, svg_path, empty_msg))
  args <- list(p, png_path, svg_path)
  if (!is.null(w)) args$w <- w
  if (!is.null(h)) args$h <- h
  tryCatch(do.call(save_gg, args), error = function(e) save_placeholder(png_path, svg_path, empty_msg))
}
gsym <- function(ids, id_map) {
  if (is.null(id_map)) return(ids)
  idc <- intersect(c("gene_id", "GeneID", "ensembl", "input"), colnames(id_map))[1]
  syc <- intersect(c("symbol", "gene_symbol", "SYMBOL", "Symbol"), colnames(id_map))[1]
  if (is.na(idc) || is.na(syc)) return(ids)
  m <- setNames(as.character(id_map[[syc]]), as.character(id_map[[idc]]))
  out <- unname(m[as.character(ids)]); ifelse(is.na(out) | !nzchar(out), ids, out)
}

# --- load ---------------------------------------------------------------------
res <- tryCatch(read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE), error = function(e) NULL)
if (is.null(res)) res <- data.frame()
meta_dir <- dirname(snakemake@input[["results"]])
study_cols <- grep("^study_.*_log2FC$", colnames(res), value = TRUE)
studies <- sub("^study_(.*)_log2FC$", "\\1", study_cols)
k <- length(studies)
id_map <- tryCatch({
  f <- file.path(dirname(meta_dir), "enrichment", "id_map.csv")
  if (file.exists(f)) read.csv(f, stringsAsFactors = FALSE) else NULL
}, error = function(e) NULL)
per_study <- setNames(lapply(studies, function(s) {
  f <- file.path(meta_dir, paste0("per_study_", s, ".csv"))
  if (file.exists(f)) tryCatch(read.csv(f, stringsAsFactors = FALSE), error = function(e) NULL) else NULL
}), studies)
has_rows <- nrow(res) > 0 && "meta_sig" %in% colnames(res)
sig <- if (has_rows) res[res$meta_sig %in% TRUE, , drop = FALSE] else res[0, , drop = FALSE]

# ============================ FIGURES =========================================
# 1. Meta-volcano: pooled effect vs combined significance, coloured by concordance.
emit(out[["volcano_png"]], out[["volcano_svg"]], {
  if (!has_rows) stop("no meta result")
  d <- res; d$neglog <- -log10(pmax(d$combined_padj, .Machine$double.xmin))
  d$dir <- factor(d$common_direction, levels = names(DIR_COL))
  d$x <- ifelse(is.na(d$rem_log2FC), 0, d$rem_log2FC)
  # metaRNASeq combined FDR hits exact 0 for strong genes -> -log10 saturates at ~308 and squashes
  # the informative band. Cap the y-axis just above the largest FINITE (padj>0) value and mark the
  # off-scale genes with a triangle, so the real 0..cap region fills the plot (EnhancedVolcano-style).
  fin <- d$neglog[d$combined_padj > 0 & is.finite(d$neglog)]
  cap <- if (length(fin)) max(fin) * 1.1 else -log10(alpha) * 3
  cap <- max(cap, -log10(alpha) * 2)
  d$capped <- d$neglog > cap; d$yy <- pmin(d$neglog, cap)
  # Label meta-DEGs: break padj ties by |effect| so labels spread across x, not stacked at the cap.
  lab <- sig; lab <- lab[order(lab$combined_padj, -abs(lab$rem_log2FC)), , drop = FALSE]
  lab <- head(lab, as.integer(getp("meta_label_top", 10)))
  lab$yy <- pmin(-log10(pmax(lab$combined_padj, .Machine$double.xmin)), cap)
  lab$x <- ifelse(is.na(lab$rem_log2FC), 0, lab$rem_log2FC); lab$sym <- gsym(lab$gene_id, id_map)
  ncap <- sum(d$capped, na.rm = TRUE)
  ggplot(d, aes(x, yy, colour = dir)) +
    geom_point(aes(shape = capped), alpha = 0.6, size = 1.5) +
    geom_hline(yintercept = -log10(alpha), linetype = 2, colour = "grey40") +
    geom_vline(xintercept = c(-lfc_thr, lfc_thr), linetype = 2, colour = "grey40") +
    (if (nrow(lab)) geom_text_repel(data = lab, aes(x, yy, label = sym), inherit.aes = FALSE,
        size = 2.8, fontface = if (italic_genes) 3 else 1, max.overlaps = 20, min.segment.length = 0,
        box.padding = 0.6, point.padding = 0.3, direction = "both", seed = 42) else NULL) +
    scale_colour_manual(values = DIR_COL, drop = FALSE, name = "cross-study\ndirection") +
    scale_shape_manual(values = c(`FALSE` = 16, `TRUE` = 17), guide = "none") +
    scale_y_continuous(expand = expansion(mult = c(0.02, 0.20))) +
    labs(x = "pooled log2 fold change (random/fixed-effect)", y = "-log10 combined FDR",
         subtitle = if (ncap > 0) sprintf("%d genes off-scale (combined FDR near 0), shown as triangles at the cap", ncap) else NULL) +
    style_theme()
}, "Meta-volcano unavailable\n(no shared genes / meta not run)")

# 2. Forest: per-study log2FC +/- 95% CI (square ~ inverse-variance weight) + pooled diamond.
emit(out[["forest_png"]], out[["forest_svg"]], {
  if (!has_rows || nrow(sig) == 0) stop("no convergent genes")
  top <- head(sig[order(sig$combined_padj), , drop = FALSE], n_forest)
  top$sym <- gsym(top$gene_id, id_map)
  rows <- list()
  for (i in seq_len(nrow(top))) {
    g <- top$gene_id[i]
    for (s in studies) {
      ps <- per_study[[s]]
      if (is.null(ps) || !("gene_id" %in% colnames(ps))) next
      r <- ps[ps$gene_id == g, , drop = FALSE]; if (!nrow(r)) next
      lfc <- r$log2FoldChange[1]; se <- r$lfcSE[1]
      if (is.na(lfc) || is.na(se)) next
      rows[[length(rows) + 1]] <- data.frame(gene = top$sym[i], study = s, est = lfc,
        lo = lfc - 1.96 * se, hi = lfc + 1.96 * se, wt = 1 / (se^2 + 1e-9), kind = "study")
    }
    rows[[length(rows) + 1]] <- data.frame(gene = top$sym[i], study = "Summary",
      est = top$rem_log2FC[i], lo = top$rem_ci_lo[i], hi = top$rem_ci_hi[i], wt = NA, kind = "pooled")
  }
  df <- do.call(rbind, rows); if (is.null(df) || !nrow(df)) stop("no per-study SE")
  df$study <- factor(df$study, levels = c("Summary", rev(sort(studies))))
  pool_note <- if (k >= 3) "random-effect (DL)" else "fixed-effect (k=2)"
  studydf <- df[df$kind == "study", , drop = FALSE]; pooldf <- df[df$kind == "pooled", , drop = FALSE]
  ggplot(df, aes(est, study)) +
    geom_vline(xintercept = 0, linetype = 2, colour = "grey50") +
    geom_errorbar(aes(xmin = lo, xmax = hi), width = 0.25, colour = "grey40", orientation = "y") +
    geom_point(data = studydf, aes(size = wt), shape = 15, colour = pal_spec$discrete[1]) +
    geom_point(data = pooldf, shape = 18, size = 4, colour = pal_spec$discrete[2]) +
    facet_wrap(~gene, scales = "free_x") +
    scale_size(range = c(1.5, 4.5), guide = "none") +
    labs(x = "log2 fold change (study vs pooled)", y = NULL,
         caption = paste0("Pooling: ", pool_note, ". Squares sized by inverse-variance weight; diamond = pooled estimate.")) +
    style_theme() + theme(strip.text = element_text(face = if (italic_genes) 3 else 1))
}, "Forest plot unavailable\n(no convergent meta-DEGs)")

# 3. Cross-study concordance scatter (pairwise), quadrant-coloured + Spearman rho.
emit(out[["scatter_png"]], out[["scatter_svg"]], {
  if (!has_rows || k < 2) stop("need >=2 studies")
  pairs <- if (k == 2) list(studies) else combn(studies, 2, simplify = FALSE)
  mk <- function(a, b) {
    xa <- res[[paste0("study_", a, "_log2FC")]]; xb <- res[[paste0("study_", b, "_log2FC")]]
    keep <- is.finite(xa) & is.finite(xb)
    data.frame(a = xa[keep], b = xb[keep],
               conc = ifelse(sign(xa[keep]) == sign(xb[keep]),
                             ifelse(xa[keep] > 0, "up", "down"), "discordant"),
               pair = paste0(a, " vs ", b),
               rho = suppressWarnings(cor(xa[keep], xb[keep], method = "spearman")))
  }
  df <- do.call(rbind, lapply(pairs, function(p) mk(p[1], p[2])))
  df$conc <- factor(df$conc, levels = names(DIR_COL))
  ann <- aggregate(rho ~ pair, df, function(x) x[1])
  ann$lab <- sprintf("Spearman rho = %.2f", ann$rho)
  # k=2: name the two studies on the axes; k>2: generic (the facet strip names the pair).
  xlab <- if (length(pairs) == 1) paste0(pairs[[1]][1], " log2FC") else "per-study log2FC"
  ylab <- if (length(pairs) == 1) paste0(pairs[[1]][2], " log2FC") else "per-study log2FC"
  ggplot(df, aes(a, b, colour = conc)) +
    geom_hline(yintercept = 0, colour = "grey80") + geom_vline(xintercept = 0, colour = "grey80") +
    geom_abline(slope = 1, intercept = 0, linetype = 2, colour = "grey50") +
    geom_point(alpha = 0.5, size = 1.3) +
    (if (length(pairs) > 1) facet_wrap(~pair) else NULL) +
    geom_text(data = ann, aes(x = -Inf, y = Inf, label = lab), inherit.aes = FALSE,
              hjust = -0.1, vjust = 1.5, size = 3.2) +
    scale_colour_manual(values = DIR_COL, drop = FALSE, name = "direction") +
    labs(x = xlab, y = ylab) + style_theme()
}, "Concordance scatter unavailable\n(need >=2 studies)")

# 4. Convergent-gene heatmap (genes x studies, signed log2FC), winsorized diverging fill.
emit(out[["heatmap_png"]], out[["heatmap_svg"]], {
  if (!has_rows || nrow(sig) == 0) stop("no convergent genes")
  top <- head(sig[order(sig$combined_padj), , drop = FALSE], as.integer(getp("meta_heatmap_top", 50)))
  top <- top[order(top$rem_log2FC), , drop = FALSE]
  mat <- as.matrix(top[, study_cols, drop = FALSE]); rownames(mat) <- gsym(top$gene_id, id_map)
  colnames(mat) <- studies
  lim <- as.numeric(quantile(abs(mat[is.finite(mat)]), 0.98, na.rm = TRUE)); if (!is.finite(lim) || lim <= 0) lim <- 1
  long <- data.frame(gene = factor(rep(rownames(mat), ncol(mat)), levels = rownames(mat)),
                     study = factor(rep(colnames(mat), each = nrow(mat)), levels = colnames(mat)),
                     lfc = as.vector(mat))
  ggplot(long, aes(study, gene, fill = lfc)) + geom_tile(colour = "grey90", linewidth = 0.2) +
    scale_fill_gradientn(colours = pal_spec$div(255), limits = c(-lim, lim), oob = scales::squish,
                         name = "log2FC") +
    labs(x = NULL, y = NULL) + style_theme() +
    theme(axis.text.y = element_text(face = if (italic_genes) 3 else 1, size = 7),
          axis.text.x = element_text(angle = 45, hjust = 1))
}, "Convergent-gene heatmap unavailable\n(no convergent meta-DEGs)",
   h = max(4, min(0.18 * nrow(sig) + 1.5, 24)))

# 5. Heterogeneity: |pooled LFC| vs I2 (k>=3 only; NA at k=2 fixed-effect).
emit(out[["hetero_png"]], out[["hetero_svg"]], {
  if (!has_rows || k < 3 || all(is.na(res$I2))) stop("heterogeneity not estimable")
  d <- res[is.finite(res$I2) & is.finite(res$rem_log2FC), , drop = FALSE]
  d$dir <- factor(d$common_direction, levels = names(DIR_COL))
  ggplot(d, aes(abs(rem_log2FC), I2, colour = dir)) +
    geom_hline(yintercept = c(25, 50, 75), linetype = 3, colour = "grey70") +
    geom_point(alpha = 0.6, size = 1.5) +
    scale_colour_manual(values = DIR_COL, drop = FALSE, name = "direction") +
    labs(x = "|pooled log2 fold change|", y = expression(I^2~"(%) between-study heterogeneity")) +
    style_theme()
}, "Heterogeneity (I2) not estimable\nwith 2 studies (fixed-effect pooling)")

# 6. Combined-p histogram (metaRNASeq inverse-normal diagnostic: ~uniform + spike near 0).
emit(out[["phist_png"]], out[["phist_svg"]], {
  if (!has_rows) stop("no meta result")
  ggplot(res, aes(combined_pvalue)) +
    geom_histogram(bins = 50, fill = pal_spec$discrete[1], colour = "white", linewidth = 0.1) +
    labs(x = "combined p-value (inverse-normal)", y = "genes") + style_theme()
}, "Combined-p histogram unavailable")

# 7. Integration-gain bars: DEGs gained by pooling vs single-study-only vs shared.
emit(out[["gain_png"]], out[["gain_svg"]], {
  if (!has_rows || !length(study_cols)) stop("no meta result")
  padj_cols <- paste0("study_", studies, "_padj")
  padj_cols <- padj_cols[padj_cols %in% colnames(res)]
  single_sig <- if (length(padj_cols)) apply(res[, padj_cols, drop = FALSE], 1, function(r) any(r < alpha, na.rm = TRUE)) else rep(FALSE, nrow(res))
  ms <- res$meta_sig %in% TRUE
  cnt <- c("meta-gained\n(pooling only)" = sum(ms & !single_sig),
           "shared\n(both)" = sum(ms & single_sig),
           "single-study\nonly" = sum(!ms & single_sig))
  df <- data.frame(cat = factor(names(cnt), levels = names(cnt)), n = as.integer(cnt))
  ggplot(df, aes(cat, n, fill = cat)) + geom_col(width = 0.65) +
    geom_text(aes(label = n), vjust = -0.3, size = 3.4, family = base_family) +
    scale_fill_manual(values = unname(pal_spec$discrete)[1:3], guide = "none") +
    labs(x = NULL, y = "genes") + style_theme()
}, "Integration-gain plot unavailable")

# ============================ TABLES ==========================================
# Per-study DE summary from the per_study_<S>.csv files.
per_rows <- lapply(studies, function(s) {
  ps <- per_study[[s]]
  if (is.null(ps) || !all(c("log2FoldChange", "padj") %in% colnames(ps)))
    return(data.frame(study = s, n_tested = NA, n_up = NA, n_down = NA, n_total = NA))
  de <- !is.na(ps$padj) & ps$padj < alpha & abs(ps$log2FoldChange) >= lfc_thr
  data.frame(study = s, n_tested = nrow(ps),
             n_up = sum(de & ps$log2FoldChange > 0), n_down = sum(de & ps$log2FoldChange < 0),
             n_total = sum(de))
})
study_summary <- if (length(per_rows)) do.call(rbind, per_rows) else
  data.frame(study = character(0), n_tested = integer(0), n_up = integer(0), n_down = integer(0), n_total = integer(0))
write.csv(study_summary, out[["study_summary"]], row.names = FALSE)

# Headline convergent-gene table: symbol-annotated, filtered VIEW of the meta result (no recompute).
conv <- if (has_rows) sig[order(sig$combined_padj), , drop = FALSE] else res[0, , drop = FALSE]
if (nrow(conv)) {
  conv_out <- data.frame(gene_id = conv$gene_id, gene_symbol = gsym(conv$gene_id, id_map),
                         common_direction = conv$common_direction, n_studies_sig = conv$n_studies_sig)
  for (s in studies) conv_out[[paste0("study_", s, "_log2FC")]] <- conv[[paste0("study_", s, "_log2FC")]]
  for (cc in c("rem_log2FC", "rem_ci_lo", "rem_ci_hi", "tau2", "I2", "QEp", "combined_pvalue", "combined_padj"))
    conv_out[[cc]] <- conv[[cc]]
  write.csv(conv_out, out[["convergent"]], row.names = FALSE)
} else {
  write.csv(data.frame(gene_id = character(0), gene_symbol = character(0), common_direction = character(0)),
            out[["convergent"]], row.names = FALSE)
}

# Machine-readable summary for the report + the main-report cards.
n_up <- sum(has_rows & res$meta_sig %in% TRUE & res$common_direction == "up")
n_down <- sum(has_rows & res$meta_sig %in% TRUE & res$common_direction == "down")
n_disc <- sum(has_rows & res$common_direction == "discordant", na.rm = TRUE)
conc_rate <- if (has_rows && nrow(res)) round(100 * mean(res$common_direction != "discordant", na.rm = TRUE), 1) else NA
med_I2 <- if (has_rows && k >= 3 && any(is.finite(res$I2))) round(median(res$I2, na.rm = TRUE), 1) else NA
esc <- function(s) gsub('"', '\\\\"', as.character(s))
summ <- sprintf(paste0('{\n  "n_studies": %d,\n  "studies": "%s",\n  "n_shared_genes": %d,\n',
  '  "n_meta_sig": %d,\n  "n_sig_up": %d,\n  "n_sig_down": %d,\n  "n_discordant": %d,\n',
  '  "direction_concordance_pct": %s,\n  "median_I2": %s,\n  "pooling": "%s"\n}'),
  k, esc(paste(studies, collapse = ", ")), nrow(res), nrow(sig), n_up, n_down, n_disc,
  ifelse(is.na(conc_rate), "null", conc_rate), ifelse(is.na(med_I2), "null", med_I2),
  if (k >= 3) "random-effect (DL)" else "fixed-effect (k=2)")
writeLines(summ, out[["summary_json"]])

sink(type = "message"); close(log_con)
