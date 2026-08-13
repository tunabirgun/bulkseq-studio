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

# gseaplot2 interprets a numeric geneSetID as a row index into the persisted
# gseaResult table. Derive the visible title from that same row so the plot and
# its CSV evidence cannot silently name different gene sets.
GSEA_GENE_SET_ID <- 1L
gsea_selected_title <- function(x, gene_set_id = GSEA_GENE_SET_ID) {
  index <- tryCatch(suppressWarnings(as.integer(gene_set_id)), error = function(e) NA_integer_)
  if (length(gene_set_id) != 1L || length(index) != 1L || is.na(index) || index < 1L) return(NULL)
  tab <- tryCatch(as.data.frame(x), error = function(e) NULL)
  if (is.null(tab) || nrow(tab) < index ||
      !all(c("Description", "ID") %in% names(tab))) return(NULL)
  description <- tryCatch(trimws(as.character(tab$Description[[index]])),
                          error = function(e) character(0))
  identifier <- tryCatch(trimws(as.character(tab$ID[[index]])),
                         error = function(e) character(0))
  if (length(description) != 1L || length(identifier) != 1L ||
      is.na(description) || is.na(identifier) ||
      !nzchar(description) || !nzchar(identifier)) return(NULL)
  sprintf("%s (%s)", description, identifier)
}

gsea_placeholder_message <- function(scope, x, title, no_results) {
  if (!have_ep) return(sprintf("%s GSEA figure unavailable: enrichplot is not installed", scope))
  if (nrows(x) == 0) return(no_results)
  if (is.null(title)) return(sprintf(
    "%s GSEA figure unavailable: selected geneSetID=1 row lacks a nonblank Description or ID",
    scope))
  "Figure could not be generated"
}

# Large enrichplot canvases (emap/cnet) can exceed ggsave's 50-inch guard, so the
# enrichplot path uses a limitsize-tolerant save; the shared save_gg covers the rest.
save_big <- function(p, png_path, svg_path, w = fig_w, h = fig_h) {
  ggsave(png_path, p, width = w, height = h, units = "in", dpi = fig_dpi,
         limitsize = FALSE, bg = "white")
  ggsave(svg_path, p, width = w, height = h, units = "in",
         limitsize = FALSE, bg = "white")
}
wrap_placeholder_message <- function(msg, canvas_width_in, text_size_mm = 5,
                                     fontfamily = NULL,
                                     horizontal_margin = 0.12) {
  words <- strsplit(trimws(as.character(msg)), "[[:space:]]+")[[1]]
  words <- words[nzchar(words)]
  if (!length(words)) return("")
  available_width_in <- max(0.5, as.numeric(canvas_width_in) *
                              (1 - 2 * as.numeric(horizontal_margin)))
  fontsize_pt <- as.numeric(text_size_mm) * 72.27 / 25.4
  grDevices::pdf(NULL, width = max(1, canvas_width_in), height = 2)
  on.exit(grDevices::dev.off(), add = TRUE)
  grid::grid.newpage()
  text_gp <- grid::gpar(
    fontsize = fontsize_pt,
    fontfamily = if (is.null(fontfamily)) "" else fontfamily
  )
  text_width <- function(x) grid::convertWidth(
    grid::grobWidth(grid::textGrob(x, gp = text_gp)), "in", valueOnly = TRUE
  )
  lines <- character(0)
  current <- ""
  for (word in words) {
    candidate <- if (nzchar(current)) paste(current, word) else word
    if (text_width(candidate) <= available_width_in || !nzchar(current)) {
      current <- candidate
    } else {
      lines <- c(lines, current)
      current <- word
    }
  }
  c(lines, current) |> paste(collapse = "\n")
}

placeholder <- function(msg, png_path, svg_path, w = fig_w, h = fig_h) {
  text_size_mm <- 5
  wrapped <- wrap_placeholder_message(
    msg, w, text_size_mm = text_size_mm, fontfamily = base_family
  )
  text_args <- list(
    geom = "text", x = 0, y = 0, label = wrapped,
    size = text_size_mm, hjust = 0.5, vjust = 0.5, lineheight = 1.15
  )
  if (!is.null(base_family)) text_args$family <- base_family
  p <- ggplot() + do.call(annotate, text_args) + theme_void()
  save_gg(p, png_path, svg_path, w = w, h = h)
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
  p <- suppressWarnings(
    p + scale_color_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse") +
        scale_fill_gradientn(colours = pal_spec$seq(255), name = "p.adjust", transform = "reverse")
  ) + labs(title = NULL) + style_theme(theme_bw)
  # Large bubbles at the maximum GeneRatio used to be bisected by the panel
  # border. Reserve data-derived right expansion and keep a small outer margin;
  # no coordinate limit is changed, so values and relative positions stay true.
  tryCatch(
    p + scale_x_continuous(expand = expansion(mult = c(0.03, 0.16))) +
      coord_cartesian(clip = "off") +
      theme(plot.margin = margin(6, 14, 6, 6)),
    error = function(e) p
  )
}

# Render `expr` to PNG+SVG; placeholder when `ok` is FALSE or the plot errors.
# `expr` is lazily evaluated, so it never runs when there is no data.
render <- function(ok, expr, png_path, svg_path, empty_msg, w = fig_w, h = fig_h) {
  if (!isTRUE(ok)) { placeholder(empty_msg, png_path, svg_path, w, h); return(invisible()) }
  p <- tryCatch(expr, error = function(e) { message("plot failed: ", conditionMessage(e)); NULL })
  if (is.null(p)) placeholder("Figure could not be generated", png_path, svg_path, w, h)
  # The draw (save_big -> ggsave) must be guarded too: enrichplot builds cnet/emap objects lazily,
  # so a draw-time grid error (e.g. a 2-node emap 'Viewport has zero dimension(s)') fires HERE, not
  # at construction. Unguarded it would abort the whole multi-output rule, leaving later figures
  # unwritten. Degrade to a placeholder so the rule always writes its declared PNG+SVG.
  else tryCatch(save_big(p, png_path, svg_path, w = w, h = h),
                error = function(e) { message("draw failed: ", conditionMessage(e))
                                      placeholder("Figure could not be generated", png_path, svg_path, w, h) })
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
  # GeneRatio = query genes in the term / query size (precision), matching the
  # clusterProfiler/enrichplot dotplot's GeneRatio so the two routes' identically-labelled
  # x-axes denote the same quantity. gost gives query_size; fall back to term_size (recall)
  # only if query_size is unavailable, so the axis is never silently NA.
  d$GeneRatio <- if (all(c("intersection_size", "query_size") %in% names(d)))
    d$intersection_size / d$query_size
  else if (all(c("intersection_size", "term_size") %in% names(d)))
    d$intersection_size / d$term_size else NA_real_
  d$term <- factor(d$term_name, levels = rev(d$term_name))
  ggplot(d, aes(x = GeneRatio, y = term)) +
    geom_point(aes(size = Count, colour = p_value)) +
    scale_colour_gradientn(colours = pal_spec$seq(255), name = "p.adjust",
                           transform = "reverse") +
    scale_size_area(name = "Count") +
    scale_x_continuous(expand = expansion(mult = c(0.03, 0.16))) +
    scale_y_discrete(labels = scales::label_wrap(label_wrap)) +
    labs(x = "GeneRatio", y = NULL, title = NULL) +
    style_theme(theme_bw) +
    coord_cartesian(clip = "off") +
    theme(plot.margin = margin(6, 14, 6, 6))
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

# Enrichment networks need more room than axis-based plots, and enrichplot's
# built-in shadow text is placed directly over nodes and edges. Draw the graph
# without labels, then place wrapped term labels outside the graph hull in two
# deterministic columns. Leader lines preserve the node-label association while
# the opaque boxes can no longer hide graph nodes or edges.
network_w <- max(fig_w, 11)
network_h <- max(fig_h, 8.5)
network_wrap <- max(18L, min(label_wrap, 28L))
network_label_size <- max(2.6, base_size * 0.26)
network_family <- if (is.null(base_family)) "" else base_family
network_theme <- theme_void(base_size = base_size, base_family = network_family) +
  theme(
    plot.background = element_rect(fill = "white", colour = NA),
    panel.background = element_rect(fill = "white", colour = NA),
    legend.background = element_rect(fill = "white", colour = NA),
    legend.key = element_rect(fill = "white", colour = NA),
    plot.margin = margin(18, 24, 18, 24),
    legend.position = "bottom",
    legend.box = "horizontal",
    legend.box.just = "center",
    legend.spacing.x = grid::unit(12, "pt"),
    legend.box.margin = margin(8, 6, 4, 6)
  )
network_guides <- guides(
  size = guide_legend(direction = "horizontal", title.position = "left", order = 1),
  colour = guide_colourbar(
    direction = "horizontal", title.position = "left", label.position = "bottom",
    barwidth = grid::unit(3.0, "in"), barheight = grid::unit(0.20, "in"), order = 2
  ),
  fill = guide_colourbar(
    direction = "horizontal", title.position = "left", label.position = "bottom",
    barwidth = grid::unit(3.0, "in"), barheight = grid::unit(0.20, "in"), order = 2
  )
)

external_network_label_layout <- function(p, category_only = FALSE) {
  d <- p$data
  if (!is.data.frame(d) || !all(c("x", "y", "label") %in% names(d))) return(NULL)
  d <- d[is.finite(d$x) & is.finite(d$y), , drop = FALSE]
  if (!nrow(d)) return(NULL)
  keep <- rep(TRUE, nrow(d))
  if (isTRUE(category_only) && ".isCategory" %in% names(d)) {
    keep <- !is.na(d$.isCategory) & d$.isCategory
  }
  lab <- d[keep & !is.na(d$label) & nzchar(as.character(d$label)), , drop = FALSE]
  if (!nrow(lab)) return(NULL)

  x_range <- range(d$x)
  y_range <- range(d$y)
  x_span <- max(diff(x_range), 1)
  y_span <- max(diff(y_range), 1)
  gap <- max(0.30, x_span * 0.12)
  label_extent <- max(1.50, x_span * 0.55)
  y_pad <- max(0.25, y_span * 0.08)

  # Split by horizontal rank rather than an arbitrary zero crossing. This keeps
  # the columns balanced for off-centre layouts while still assigning each term
  # to its nearest side. Within each side, preserve vertical rank so leaders do
  # not cross one another solely because labels were reordered.
  x_order <- order(lab$x, lab$y, as.character(lab$label))
  left_count <- ceiling(nrow(lab) / 2)
  lab$side <- "right"
  lab$side[x_order[seq_len(left_count)]] <- "left"
  lab$display_label <- scales::label_wrap(network_wrap)(as.character(lab$label))
  lab$label_x <- ifelse(lab$side == "left", x_range[1] - gap, x_range[2] + gap)
  lab$hjust <- ifelse(lab$side == "left", 1, 0)
  lab$label_y <- NA_real_
  for (side_name in c("left", "right")) {
    idx <- which(lab$side == side_name)
    if (!length(idx)) next
    idx <- idx[order(lab$y[idx], lab$x[idx], as.character(lab$label[idx]))]
    slots <- if (length(idx) == 1L) mean(y_range) else
      seq(y_range[1] - y_pad, y_range[2] + y_pad, length.out = length(idx))
    lab$label_y[idx] <- slots
  }

  # Runtime invariant: a future edit that moves either label anchor back into
  # the graph hull fails closed instead of silently recreating the obstruction.
  if (any(lab$label_x[lab$side == "left"] >= x_range[1]) ||
      any(lab$label_x[lab$side == "right"] <= x_range[2])) {
    stop("Enrichment-network label anchors must remain outside the graph hull")
  }
  if (any(!is.finite(lab$label_y))) stop("Enrichment-network label slots are incomplete")

  list(
    labels = lab,
    xlim = c(x_range[1] - gap - label_extent, x_range[2] + gap + label_extent),
    ylim = c(y_range[1] - y_pad * 1.35, y_range[2] + y_pad * 1.35)
  )
}

external_network_labels <- function(p, category_only = FALSE) {
  layout <- external_network_label_layout(p, category_only = category_only)
  if (is.null(layout)) return(p + network_theme)
  lab <- layout$labels
  # Insert leaders below the original graph layers, so nodes remain intact at
  # their endpoints instead of being bisected by a line drawn over their centre.
  leader_layer <- geom_segment(
    data = lab,
    aes(x = x, y = y, xend = label_x, yend = label_y),
    inherit.aes = FALSE,
    colour = "grey50",
    linewidth = 0.28,
    lineend = "round",
    show.legend = FALSE
  )
  p$layers <- append(list(leader_layer), p$layers)
  p +
    geom_label(
      data = lab,
      aes(x = label_x, y = label_y, label = display_label, hjust = hjust),
      inherit.aes = FALSE,
      label.padding = grid::unit(0.18, "lines"),
      fill = "white",
      colour = "grey10",
      linewidth = 0.25,
      size = network_label_size,
      family = network_family,
      fontface = if (label_bold) "bold" else "plain",
      lineheight = 0.92,
      show.legend = FALSE
    ) +
    network_guides +
    coord_equal(xlim = layout$xlim, ylim = layout$ylim, expand = FALSE, clip = "off") +
    network_theme
}

outlined_network_nodes <- function(p) {
  d <- p$data
  if (!is.data.frame(d) || !all(c("x", "y", "size") %in% names(d))) return(p)
  d <- d[is.finite(d$x) & is.finite(d$y) & is.finite(d$size), , drop = FALSE]
  if (!nrow(d)) return(p)
  p + geom_point(
    data = d,
    aes(x = x, y = y, size = size),
    inherit.aes = FALSE,
    shape = 21,
    fill = NA,
    colour = "grey35",
    stroke = 0.45,
    show.legend = FALSE
  )
}

make_cnet <- function(x, categories, fold_change) {
  set.seed(42)
  p <- tryCatch(
    cnetplot(x, showCategory = categories, node_label = "none", foldChange = fold_change),
    error = function(e) cnetplot(x, showCategory = categories, node_label = "none")
  )
  external_network_labels(p, category_only = TRUE)
}

make_emap <- function(x, categories) {
  set.seed(42)
  p <- emapplot(pairwise_termsim(x), showCategory = categories, node_label = "none")
  external_network_labels(outlined_network_nodes(p))
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

  # Use one explicitly scoped GO-BP object for every ORA-derived figure. The three
  # ORAs are separate BH families: never pool their terms or imply that an empty
  # combined foreground means both directional tests were empty. When combined is
  # empty, show the direction with more adjusted-significant terms and state the
  # exact result counts for all three tests in the figure caption.
  combined_n <- nrows(ego_all)
  up_n <- nrows(obj$ego_up)
  down_n <- nrows(obj$ego_down)
  go_plot_obj <- ego_all
  go_scope_caption <- NULL
  go_scope_label <- "combined-foreground"
  if (combined_n == 0 && max(up_n, down_n) > 0) {
    if (up_n >= down_n) {
      go_plot_obj <- obj$ego_up
      go_scope_label <- "up-regulated"
      go_scope_caption <- sprintf(
        "Up-regulated ORA selected (separate BH family): %d GO BP terms.\nAdjusted-significant terms - combined: %d; down-regulated: %d.",
        up_n, combined_n, down_n)
    } else {
      go_plot_obj <- obj$ego_down
      go_scope_label <- "down-regulated"
      go_scope_caption <- sprintf(
        "Down-regulated ORA selected (separate BH family): %d GO BP terms.\nAdjusted-significant terms - combined: %d; up-regulated: %d.",
        down_n, combined_n, up_n)
    }
  }
  no_go_scoped <- if (enrich_ran && have_orgdb) {
    "No GO Biological Process terms met the adjusted criterion in the combined, up-regulated, or down-regulated ORAs"
  } else no_go

  render(have_ep && nrows(go_plot_obj) > 0,
         themed_dotplot(go_plot_obj, show_cat) + labs(caption = go_scope_caption),
         out[["dotplot_png"]], out[["dotplot_svg"]], no_go_scoped)

  # GSEA running-score for the top gene set, titled from the exact selected result
  # row as Description (ID); running-score line from the project palette.
  go_gsea_title <- gsea_selected_title(gse, GSEA_GENE_SET_ID)
  render(have_ep && !is.null(go_gsea_title),
         theme_gsea(gseaplot2(gse, geneSetID = GSEA_GENE_SET_ID,
                              title = go_gsea_title,
                              base_size = base_size, color = gsea_col)),
         out[["gsea_png"]], out[["gsea_svg"]],
         gsea_placeholder_message("GO", gse, go_gsea_title, no_gsea))
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
  # cnet gene nodes are coloured by fold change (diverging ramp, not reversed).
  cnet_caption <- if (is.null(go_scope_caption)) NULL else paste(
    go_scope_caption,
    sprintf("Gene-concept network displays %d of %d selected terms.",
            min(cnet_cat, nrows(go_plot_obj)), nrows(go_plot_obj)),
    sep = "\n")
  render(have_ep && nrows(go_plot_obj) > 0,
         paletteize(
           make_cnet(go_plot_obj, cnet_cat, geneList),
           pal_spec$div(255)) + labs(caption = cnet_caption),
         out[["cnet_png"]], out[["cnet_svg"]], no_go_scoped,
         w = network_w, h = network_h)
  # emap term nodes are coloured by p.adjust (sequential ramp, reversed so significant is darkest).
  emap_empty <- if (nrows(go_plot_obj) == 1) {
    sprintf("Term-similarity map unavailable: the %s ORA returned only one adjusted-significant GO BP term", go_scope_label)
  } else no_go_scoped
  render(have_ep && nrows(go_plot_obj) > 1,
         paletteize(make_emap(go_plot_obj, emap_cat),
                    pal_spec$seq(255), reverse = TRUE) + labs(caption = go_scope_caption),
         out[["emap_png"]], out[["emap_svg"]], emap_empty,
         w = network_w, h = network_h)

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
kegg_gsea_title <- gsea_selected_title(obj$kegg_gse, GSEA_GENE_SET_ID)
render(have_ep && !is.null(kegg_gsea_title),
       theme_gsea(gseaplot2(obj$kegg_gse, geneSetID = GSEA_GENE_SET_ID,
                            title = kegg_gsea_title,
                            base_size = base_size, color = gsea_col)),
       out[["kegg_gsea_png"]], out[["kegg_gsea_svg"]],
       gsea_placeholder_message("KEGG", obj$kegg_gse, kegg_gsea_title, no_kegg))

# Per-ontology GO ORA dotplots (BP/MF/CC), backend-aware. OrgDb/clusterProfiler route: from the
# persisted enrichResults (ego_all reused as BP, plus ego_mf/ego_cc). g:Profiler route queries only
# GO:BP (gost sources = GO:BP), so BP is drawn from gprofiler_table exactly like the main dotplot,
# and MF/CC carry a g:Profiler-specific message -- never the "no OrgDb" wording, which otherwise
# falsely denied the real GO:BP terms the main dotplot shows on that route. Dotplots need the S4
# object (or the manual gp_dotplot); they are never fed a CSV data frame.
if (identical(backend, "gprofiler")) {
  render(TRUE, gp_dotplot(gp_tab, "GO:BP", show_cat),
         out[["go_bp_png"]], out[["go_bp_svg"]], "No GO:BP enrichment (g:Profiler returned no terms)")
  placeholder("GO Molecular Function not queried on the g:Profiler route", out[["go_mf_png"]], out[["go_mf_svg"]])
  placeholder("GO Cellular Component not queried on the g:Profiler route", out[["go_cc_png"]], out[["go_cc_svg"]])
} else {
  go_ont_msg <- function(lab) if (have_orgdb) sprintf("No combined-foreground GO %s terms met the adjusted criterion", lab) else no_go
  render(have_ep && nrows(go_plot_obj) > 0,
         themed_dotplot(go_plot_obj, show_cat) + labs(caption = go_scope_caption),
         out[["go_bp_png"]], out[["go_bp_svg"]], no_go_scoped)
  render(have_ep && nrows(obj$ego_mf) > 0, themed_dotplot(obj$ego_mf, show_cat),
         out[["go_mf_png"]], out[["go_mf_svg"]], go_ont_msg("Molecular Function"))
  render(have_ep && nrows(obj$ego_cc) > 0, themed_dotplot(obj$ego_cc, show_cat),
         out[["go_cc_png"]], out[["go_cc_svg"]], go_ont_msg("Cellular Component"))
}

sink(type = "message")
close(log_con)
