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

# Enrichment visualisations (0.5.0) from the persisted clusterProfiler objects
# (results/enrichment/enrichment_objects.rds). Best-effort: every figure degrades
# to a labelled placeholder when there is no result or a plot call fails, so the
# rule always produces its declared PNG+SVG outputs and never breaks the run.

suppressMessages({
  library(ggplot2)
  library(svglite)
  library(scales)
  library(RColorBrewer)
})

# Shared palette/theme/getp/save_gg helpers (sourced; resolved via scriptdir).
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

obj <- tryCatch(readRDS(snakemake@input[["objects"]]), error = function(e) list())
out <- snakemake@output

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL)
if (!is.list(style)) style <- list()
getp <- make_getp(style)
gp <- getp_for(style, "enrichment")  # per-group palette/font/point/base-font/scaling override
fig_w <- as.numeric(gp("width_in", 7))
fig_h <- as.numeric(gp("height_in", 6))
fig_dpi <- as.integer(getp("dpi", 300))
base_size <- as.numeric(gp("base_font_size", 12))
font_family <- as.character(gp("font_family", ""))
label_bold <- isTRUE(as.logical(getp("label_bold", FALSE)))
title_bold <- isTRUE(as.logical(getp("title_bold", FALSE)))

# Enrichment-specific config (NULL-safe; defaults reproduce prior behaviour).
palette_name <- as.character(gp("palette", "Blue-Red"))
show_cat <- as.integer(getp("enrich_show_category", 15))
cnet_cat <- as.integer(getp("enrich_cnet_category", 5))
emap_cat <- as.integer(getp("enrich_emap_category", 15))
label_wrap <- as.integer(getp("enrich_label_wrap", 40))
gsea_line_color <- as.character(getp("gsea_line_color", ""))

pal_spec <- palette_spec(palette_name)
base_family <- resolve_font(font_family)
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                label_bold = label_bold, title_bold = title_bold)
save_gg <- make_save_gg(fig_w = fig_w, fig_h = fig_h, fig_dpi = fig_dpi)

# gseaplot2 returns a multi-panel patchwork with enrichplot's own theme; propagate the
# configured font family across all panels so the running-score plot matches the other
# figures. Best-effort (patchwork's `&`); unchanged if it is unavailable or errors.
theme_gsea <- function(p) {
  if (is.null(base_family)) return(p)
  tryCatch(p & theme(text = element_text(family = base_family)), error = function(e) p)
}

# Large enrichplot canvases (emap/cnet) can exceed ggsave's 50-inch guard, so the
# enrichplot path uses a limitsize-tolerant save; the shared save_gg covers the rest.
save_big <- function(p, png_path, svg_path, w = fig_w, h = fig_h) {
  ggsave(png_path, p, width = w, height = h, units = "in", dpi = fig_dpi, limitsize = FALSE)
  ggsave(svg_path, p, width = w, height = h, units = "in", limitsize = FALSE)
}
placeholder <- function(msg, png_path, svg_path) {
  save_gg(ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void(),
          png_path, svg_path)
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)

# Route an enrichplot S4 dotplot through the shared palette + theme: p.adjust on the
# sequential ramp (reversed so most significant is darkest, matching the gost/set-
# overlap dotplots), long terms wrapped, no embedded title. Description (pathway
# NAME) labels are kept by enrichplot -- never raw GO/KEGG ids.
themed_dotplot <- function(x, n) {
  p <- dotplot(x, showCategory = n,
               label_format = function(lbl) scales::label_wrap(label_wrap)(lbl))
  # enrichplot's dotplot maps p.adjust to `fill` in current versions (older ones used
  # `colour`), so set BOTH: a colour-only scale silently did nothing and the dotplots kept
  # enrichplot's default red-blue instead of the project palette. Reversed = significant darkest.
  suppressWarnings(
    p + scale_color_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse") +
        scale_fill_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse")
  ) + labs(title = NULL) + style_theme(theme_bw)
}

# Render `expr` to PNG+SVG; placeholder when `ok` is FALSE or the plot errors.
# `expr` is lazily evaluated, so it never runs when there is no data.
render <- function(ok, expr, png_path, svg_path, empty_msg) {
  if (!isTRUE(ok)) { placeholder(empty_msg, png_path, svg_path); return(invisible()) }
  p <- tryCatch(expr, error = function(e) { message("plot failed: ", conditionMessage(e)); NULL })
  if (is.null(p)) placeholder("Figure could not be generated", png_path, svg_path)
  else save_big(p, png_path, svg_path)
}

have_ep <- requireNamespace("enrichplot", quietly = TRUE)
if (have_ep) suppressMessages(library(enrichplot))

backend <- tryCatch(as.character(obj$backend), error = function(e) NA_character_)
gp_tab <- tryCatch(obj$gprofiler_table, error = function(e) NULL)

# Empty-figure messages, split by cause so an empty plot is not misread as failure:
# the route ran but nothing cleared the cutoff, vs the organism has no annotation
# database / KEGG code, vs enrichment was skipped or did not complete. orgdb / kegg
# are read from the persisted objects; a pre-0.8.3 RDS has no `kegg` field, so KEGG
# falls back to the original combined wording (backward compatible).
orgdb_name <- tryCatch(as.character(obj$orgdb), error = function(e) character(0))
kegg_code  <- tryCatch(as.character(obj$kegg), error = function(e) character(0))
enrich_ran     <- length(backend) > 0 && !is.na(backend[1]) && nzchar(backend[1])
have_orgdb     <- length(orgdb_name) > 0 && nzchar(orgdb_name[1])
kegg_present   <- "kegg" %in% names(obj)
have_kegg_code <- kegg_present && length(kegg_code) > 0 && nzchar(kegg_code[1])
no_go <- if (!enrich_ran) "No GO enrichment (analysis was skipped or did not complete)" else
         if (have_orgdb)  "No GO BP terms passed the significance cutoff" else
                          "No GO enrichment: no annotation database (OrgDb) for this organism"
no_gsea <- "No significant GO GSEA gene sets"
no_kegg <- if (!enrich_ran)    "No KEGG enrichment (analysis was skipped or did not complete)" else
           if (!kegg_present)  "No KEGG pathway enrichment (no KEGG code or nothing significant)" else
           if (have_kegg_code) "No KEGG pathways passed the significance cutoff" else
                               "No KEGG enrichment: no KEGG organism code for this organism"

# Manual ORA dotplot from a g:Profiler gost $result subset (no S4 object exists for
# the gost backend). Mirrors the enrichplot dotplot: term NAME on y, GeneRatio
# (intersection_size/term_size) on x, p.adjust on the sequential ramp (reversed so
# significant is darkest), Count as size. Falls back to a placeholder when the
# source rows are absent.
gp_dotplot <- function(df, src, n) {
  if (is.null(df) || !is.data.frame(df) || nrow(df) == 0) return(NULL)
  d <- df[!is.na(df$source) & df$source == src, , drop = FALSE]
  if (nrow(d) == 0) return(NULL)
  d <- d[order(d$p_value), , drop = FALSE]
  d <- head(d, n)
  d$Count <- if ("intersection_size" %in% names(d)) d$intersection_size else NA_integer_
  d$GeneRatio <- if (all(c("intersection_size", "term_size") %in% names(d)))
    d$intersection_size / d$term_size else NA_real_
  d$term <- factor(d$term_name, levels = rev(d$term_name))
  ggplot(d, aes(x = GeneRatio, y = term)) +
    geom_point(aes(size = Count, colour = p_value)) +
    scale_colour_gradientn(colours = pal_spec$seq(255), name = "p.adjust",
                           transform = "reverse") +
    scale_size_area(name = "Count") +
    scale_y_discrete(labels = scales::label_wrap(label_wrap)) +
    labs(x = "GeneRatio", y = NULL, title = NULL) +
    style_theme(theme_bw)
}

# Running-score line colour (shared by GO + KEGG GSEA, both backends).
gsea_col <- if (nzchar(gsea_line_color)) gsea_line_color else pal_spec$discrete[2]

# enrichplot's cnetplot/emapplot ignore the project palette -- they use enrichplot's
# own gradients (viridis-ish), so those two network figures did not match the dotplot/
# ridge/GSEA palette. Append the palette scale for whichever continuous aesthetic each
# uses: colour for cnet fold change / emap p.adjust, and fill for newer enrichplot that
# maps nodes with fill. Appending a scale for an unused aesthetic is a harmless no-op and
# replacing the built-in one is intended, so warnings are suppressed; best-effort tryCatch
# keeps the figure rendering even if a future enrichplot changes its aesthetics.
paletteize <- function(p, colours, reverse = FALSE, name = ggplot2::waiver()) {
  tr <- if (reverse) "reverse" else "identity"
  tryCatch(suppressWarnings(
    p + scale_color_gradientn(colours = colours, name = name, transform = tr) +
        scale_fill_gradientn(colours = colours, name = name, transform = tr)
  ), error = function(e) p)
}

# GO-derived figures fork on the backend: g:Profiler has no S4 object, so the GO
# dotplot is built manually from gost $result and the S4-only GO figures (GSEA,
# ridgeplot, cnet, emap, DO) degrade to placeholders. The clusterProfiler/OrgDb
# path keeps the enrichplot S4 figures.
if (identical(backend, "gprofiler")) {
  render(TRUE, gp_dotplot(gp_tab, "GO:BP", show_cat),
         out[["dotplot_png"]], out[["dotplot_svg"]],
         "No GO:BP enrichment (g:Profiler returned no terms)")
  placeholder("GSEA not available (g:Profiler is ORA-only)", out[["gsea_png"]], out[["gsea_svg"]])
  placeholder("Ridgeplot not available (g:Profiler is ORA-only)", out[["ridge_png"]], out[["ridge_svg"]])
  placeholder("Gene-concept network not available (g:Profiler backend)", out[["cnet_png"]], out[["cnet_svg"]])
  placeholder("Term-similarity map not available (g:Profiler backend)", out[["emap_png"]], out[["emap_svg"]])
  placeholder("No disease-ontology terms (human/mouse only)", out[["do_dotplot_png"]], out[["do_dotplot_svg"]])
} else {
  ego_all <- obj$ego_all
  gse <- obj$gse
  geneList <- obj$geneList

  # ORA dotplot: combined up+down GO BP terms by default. On small designs the
  # combined hypergeometric test can return nothing while one direction still has
  # terms, so fall back to the up- (then down-) regulated set and record the
  # provenance in a caption instead of rendering an empty placeholder.
  dot_obj <- ego_all; dot_cap <- NULL
  if (nrows(dot_obj) == 0 && nrows(obj$ego_up) > 0) {
    dot_obj <- obj$ego_up
    dot_cap <- "Up-regulated genes only (combined up+down set had no enriched GO BP terms)"
  } else if (nrows(dot_obj) == 0 && nrows(obj$ego_down) > 0) {
    dot_obj <- obj$ego_down
    dot_cap <- "Down-regulated genes only (combined up+down set had no enriched GO BP terms)"
  }
  render(have_ep && nrows(dot_obj) > 0,
         themed_dotplot(dot_obj, show_cat) + labs(caption = dot_cap),
         out[["dotplot_png"]], out[["dotplot_svg"]], no_go)

  # GSEA running-score for the top gene set, and a ridgeplot of the leading sets.
  # No embedded title (caption lives in text); running-score line from the palette.
  render(have_ep && nrows(gse) > 0,
         theme_gsea(gseaplot2(gse, geneSetID = 1, base_size = base_size, color = gsea_col)),
         out[["gsea_png"]], out[["gsea_svg"]], no_gsea)
  # enrichplot::ridgeplot hits an "object 'selected'" bug on these gseaResults, so
  # build leading-edge fold-change ridges directly from core_enrichment + geneList.
  ridge_plot <- if (have_ep && nrows(gse) > 0) tryCatch({
    rd <- as.data.frame(gse)
    rd <- head(rd[order(rd$p.adjust), ], min(show_cat, nrow(rd)))
    parts <- lapply(seq_len(nrow(rd)), function(i) {
      g <- strsplit(rd$core_enrichment[i], "/", fixed = TRUE)[[1]]
      fc <- geneList[g]; fc <- fc[is.finite(fc)]
      if (!length(fc)) NULL else data.frame(term = rd$Description[i], fc = as.numeric(fc))
    })
    dd <- do.call(rbind, parts)
    if (is.null(dd) || !nrow(dd)) NULL else
      ggplot(dd, aes(x = fc, y = reorder(term, fc, FUN = stats::median), fill = after_stat(x))) +
        ggridges::geom_density_ridges_gradient(scale = 1.3, rel_min_height = 0.01,
                                               linewidth = 0.3, colour = "grey40") +
        scale_fill_gradientn(colours = pal_spec$div(255), name = "log2 FC") +
        # Wrap long GO term labels; unwrapped they consume the panel width and squash
        # every ridge into an invisible sliver (matches the ORA/GSEA dotplots).
        scale_y_discrete(labels = scales::label_wrap(label_wrap)) +
        labs(x = "core-enrichment log2 fold change", y = NULL) +
        style_theme(theme_bw)
  }, error = function(e) { message("ridge build failed: ", conditionMessage(e)); NULL }) else NULL
  render(!is.null(ridge_plot), ridge_plot, out[["ridge_png"]], out[["ridge_svg"]],
         "Ridgeplot unavailable (no leading-edge fold changes)")

  # Gene-concept network (fold-change coloured when possible) and term-similarity map.
  set.seed(42)
  # cnet gene nodes are coloured by fold change (diverging ramp, not reversed).
  render(have_ep && nrows(ego_all) > 0,
         paletteize(
           tryCatch(cnetplot(ego_all, showCategory = cnet_cat, node_label = "category", foldChange = geneList),
                    error = function(e) cnetplot(ego_all, showCategory = cnet_cat, node_label = "category")),
           pal_spec$div(255)),
         out[["cnet_png"]], out[["cnet_svg"]], no_go)
  # emap term nodes are coloured by p.adjust (sequential ramp, reversed so significant is darkest).
  render(have_ep && nrows(ego_all) > 1,
         paletteize(emapplot(pairwise_termsim(ego_all), showCategory = emap_cat),
                    pal_spec$seq(255), reverse = TRUE),
         out[["emap_png"]], out[["emap_svg"]], no_go)

  # Disease-ontology ORA dotplot (human/mouse only; placeholder otherwise).
  render(have_ep && nrows(obj$ego_do) > 0,
         themed_dotplot(obj$ego_do, show_cat),
         out[["do_dotplot_png"]], out[["do_dotplot_svg"]],
         "No disease-ontology terms (human/mouse only)")
}

# KEGG ORA dotplot + KEGG GSEA running-score are BACKEND-AGNOSTIC: clusterProfiler
# enrichKEGG/gseKEGG runs on every route (OrgDb, KEGG-only, AND g:Profiler), so
# ekegg_all/kegg_gse are real S4 objects regardless of backend. Render the S4 path
# unconditionally; never rebuild KEGG from gost or placeholder it on g:Profiler.
render(have_ep && nrows(obj$ekegg_all) > 0,
       themed_dotplot(obj$ekegg_all, show_cat),
       out[["kegg_dotplot_png"]], out[["kegg_dotplot_svg"]], no_kegg)
render(have_ep && nrows(obj$kegg_gse) > 0,
       theme_gsea(gseaplot2(obj$kegg_gse, geneSetID = 1, base_size = base_size, color = gsea_col)),
       out[["kegg_gsea_png"]], out[["kegg_gsea_svg"]], no_kegg)

sink(type = "message")
close(log_con)
