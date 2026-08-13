# Shared figure style for the BulkSeq Studio R figure scripts. Sourced (no
# snakemake@ refs here) so make_figures.R, make_enrichment_figures.R, the three
# stats scripts and build_string_network.R all draw from one palette/theme/save
# source. Each consumer resolves this file via snakemake@scriptdir and binds the
# factory results to the bare names getp / style_theme / save_gg.

# ---- Palette helper ---------------------------------------------------------
# One named palette drives discrete scales, sequential ramps and diverging
# ramps. Viridis stops are hardcoded so no extra package is required. Each
# palette returns three genuine roles plus deprecated aliases:
#   discrete  - categorical colours (PCA groups, volcano direction, annotations)
#   seq       - genuinely sequential for every palette (distance, correlation,
#               density, p.adjust); low -> high, no false midpoint
#   div       - genuinely diverging, zero-centred, colourblind-safe (z-scores,
#               signed log2FC); pair with symmetric breaks
#   ramp      - deprecated alias for seq (kept one release for call-site migration)
#   diverging - deprecated alias for div (kept one release for call-site migration)
VIRIDIS_STOPS <- c("#440154", "#414487", "#2A788E", "#22A884", "#7AD151", "#FDE725")
MAGMA_STOPS <- c("#000004", "#51127C", "#B63679", "#FB8861", "#FCFDBF")
PLASMA_STOPS <- c("#0D0887", "#7E03A8", "#CC4778", "#F89540", "#F0F921")
CIVIDIS_STOPS <- c("#00204D", "#414D6B", "#7C7B78", "#BCAF6F", "#FFEA46")

# Viridis-family helper: stops are already sequential; div borrows a CB-safe RdBu
# (these palettes have no neutral hue of their own).
.uniform_spec <- function(discrete, stops) {
  seq <- colorRampPalette(stops)
  div <- colorRampPalette(rev(brewer.pal(11, "RdBu")))
  list(discrete = discrete, seq = seq, div = div, ramp = seq, diverging = div)
}

palette_spec <- function(name) {
  if (identical(name, "Greyscale")) {
    seq <- colorRampPalette(c("#F7F7F7", "#525252", "#000000"))
    # Greyscale has no honest diverging hue; use a CB-safe RdGy compromise.
    div <- colorRampPalette(rev(brewer.pal(11, "RdGy")))
    list(discrete = c("#1A1A1A", "#7F7F7F", "#BFBFBF", "#4D4D4D", "#A6A6A6"),
         seq = seq, div = div, ramp = seq, diverging = div)
  } else if (identical(name, "Viridis")) {
    .uniform_spec(c("#440154", "#21908C", "#FDE725", "#3B528B", "#5DC863"), VIRIDIS_STOPS)
  } else if (identical(name, "Magma")) {
    .uniform_spec(c("#000004", "#B63679", "#FB8861", "#51127C", "#FCFDBF"), MAGMA_STOPS)
  } else if (identical(name, "Plasma")) {
    .uniform_spec(c("#0D0887", "#CC4778", "#F89540", "#7E03A8", "#F0F921"), PLASMA_STOPS)
  } else if (identical(name, "Cividis")) {
    .uniform_spec(c("#00204D", "#7C7B78", "#BCAF6F", "#414D6B", "#FFEA46"), CIVIDIS_STOPS)
  } else if (identical(name, "Spectral")) {
    seq <- colorRampPalette(rev(brewer.pal(11, "Spectral")))
    div <- colorRampPalette(rev(brewer.pal(11, "RdBu")))
    list(discrete = brewer.pal(8, "Dark2"),
         seq = seq, div = div, ramp = seq, diverging = div)
  } else if (identical(name, "Red-Yellow-Blue")) {
    seq <- colorRampPalette(rev(brewer.pal(11, "RdYlBu")))
    div <- colorRampPalette(rev(brewer.pal(11, "RdBu")))
    list(discrete = brewer.pal(8, "Set2"),
         seq = seq, div = div, ramp = seq, diverging = div)
  } else {
    # Blue-Red (default).
    seq <- colorRampPalette(c("#F7FBFF", "#08519C"))  # white -> blue, single hue
    div <- colorRampPalette(c("#2C7BB6", "white", "#C0392B"))  # blue-white-red
    list(discrete = c("#2C7BB6", "#C0392B", "#2E7D32", "#B26A00", "#6A1B9A"),
         seq = seq, div = div, ramp = seq, diverging = div)
  }
}

# ---- Contrast-aware categorical colours ------------------------------------
# Differential-expression figures use a semantic two-colour contract instead
# of whichever order happens to occur in the metadata: denominator/reference is
# the first (cool) colour and numerator/comparison is the second (warm) colour.
# Remaining levels are assigned deterministically by name. This keeps PCA,
# annotation strips and every DEG heatmap consistent even when rows/columns are
# reordered by clustering or a sample sheet is rewritten in a different order.
contrast_color_map <- function(levels, contrast = NULL, discrete) {
  levels <- unique(as.character(levels))
  levels <- levels[!is.na(levels) & nzchar(levels)]
  if (!length(levels)) return(setNames(character(0), character(0)))
  if (length(discrete) < 2) stop("contrast_color_map needs at least two discrete colours")

  numerator <- tryCatch(trimws(as.character(contrast[["numerator"]])[1]),
                        error = function(e) "")
  denominator <- tryCatch(trimws(as.character(contrast[["denominator"]])[1]),
                          error = function(e) "")
  semantic <- nzchar(numerator) && nzchar(denominator) &&
    !identical(numerator, denominator) &&
    numerator %in% levels && denominator %in% levels

  ans <- setNames(rep(NA_character_, length(levels)), levels)
  if (semantic) {
    ans[denominator] <- discrete[1]
    ans[numerator] <- discrete[2]
  }
  remaining <- sort(names(ans)[is.na(ans)], method = "radix")
  if (length(remaining)) {
    available <- if (semantic) discrete[-c(1, 2)] else discrete
    if (!length(available)) available <- discrete
    if (length(remaining) > length(available)) {
      available <- grDevices::colorRampPalette(available)(length(remaining))
    }
    ans[remaining] <- available[seq_along(remaining)]
  }
  ans
}

# ---- Font resolver ----------------------------------------------------------
# Map a requested font family to one actually installed in the pipeline environment.
# Windows font names (Times New Roman, Arial, Courier New, ...) are not present on a stock
# Linux/WSL env, so without this a serif request silently renders as a sans default. An exact
# match is used as-is; known serif/mono names map to an installed serif/mono; anything else is
# left for systemfonts to substitute. Returns NULL for an empty request (device default).
resolve_font <- function(fam) {
  if (is.null(fam) || !nzchar(fam)) return(NULL)
  installed <- tryCatch(unique(systemfonts::system_fonts()$family), error = function(e) character(0))
  if (fam %in% installed) return(fam)
  key <- tolower(trimws(fam))
  serif <- c("times new roman", "times", "times roman", "georgia", "cambria", "garamond",
             "book antiqua", "palatino", "palatino linotype", "minion pro", "serif")
  mono <- c("courier new", "courier", "consolas", "monaco", "lucida console", "menlo", "monospace")
  pick <- function(cands) { for (c in cands) if (c %in% installed) return(c); NULL }
  if (key %in% serif) { t <- pick(c("Liberation Serif", "DejaVu Serif", "Noto Serif", "FreeSerif")); if (!is.null(t)) return(t) }
  if (key %in% mono)  { t <- pick(c("DejaVu Sans Mono", "Liberation Mono", "Noto Mono", "FreeMono")); if (!is.null(t)) return(t) }
  fam
}

# ---- getp factory -----------------------------------------------------------
# Returns a getter over a style list. Empty-string config values fall back to
# the default ONLY when the default is not itself a string, so string-defaulted
# fields (e.g. gsea_line_color default "") keep their configured empty value.
make_getp <- function(style) {
  if (is.null(style) || !is.list(style)) style <- list()
  function(key, default) {
    v <- style[[key]]
    if (is.null(v) || (is.character(v) && length(v) == 1 && !nzchar(v) && !is.character(default))) default else v
  }
}

# ---- Heatmap canvas auto-sizing (scale with matrix size) --------------------
# Heatmaps previously pinned width at fig_w regardless of sample count, so a
# many-sample run (e.g. a microarray series) crushed columns into unreadable
# slivers; height only grew with rows. This sizes BOTH axes from the matrix:
# width from ncol (+ a row-label gutter that grows with the longest gene symbol
# + legend), height from nrow (+ dendrogram/annotation/angled-label budget),
# clamped to [min_in, max_in]. Returns c(w_in, h_in). Small runs land near the
# old 6-7 in and reproduce prior output; scaling only engages as the matrix grows.
heatmap_dim <- function(nrow, ncol, cell_h = 12, cell_w = 10,
                        row_label_chars = 8, show_col_labels = TRUE,
                        legend_in = 1.7, min_w = 6, min_h = 5,
                        max_w = 44, max_h = 44) {
  left       <- min(2.6, 0.6 + 0.070 * max(row_label_chars, 1))  # gene-symbol gutter
  bottom_col <- if (isTRUE(show_col_labels)) 1.1 else 0.3         # angled sample labels
  w <- left + (ncol * cell_w) / 72 + legend_in
  h <- (nrow * cell_h) / 72 + 2.0 + bottom_col                   # +2 = dendro + annot track
  # Width is capped (the crowding fix). Height must NOT be capped below the body when the
  # caller pins pheatmap's cellheight (fixed pt/row) -- a cap would clip the bottom rows -- so
  # those callers pass max_h = Inf. n x n heatmaps that don't pin cellheight cap height safely.
  c(min(max(w, min_w), max_w), min(max(h, min_h), max_h))
}

# Effective per-cell width so ncol columns fit inside a width budget (used to keep
# the body inside max_in before the font floor kicks in).
heatmap_cell_w_fit <- function(ncol, want_cell_w = 10, budget_in = 44,
                               left_in = 2.6, legend_in = 1.7) {
  usable <- max(budget_in - left_in - legend_in, 1) * 72
  min(want_cell_w, usable / max(ncol, 1))
}

# Fill the available heatmap canvas for small matrices without turning cells
# into oversized blocks. The returned width is deterministic and remains capped
# for large sample sets; explicit per-figure size overrides still take priority.
heatmap_cell_w_fill <- function(ncol, canvas_w, row_label_chars = 8,
                                 legend_in = 1.7, min_cell_w = 12,
                                 max_cell_w = 34) {
  label_gutter <- min(2.6, 0.6 + 0.070 * max(row_label_chars, 1))
  usable_pt <- max(canvas_w - label_gutter - legend_in, 1) * 72
  min(max(usable_pt / max(ncol, 1), min_cell_w), max_cell_w)
}

# pheatmap places its continuous and annotation legends in two side-by-side
# columns even when the heatmap is tall. For a 30 x 6 DEG heatmap this spends
# roughly an inch on empty horizontal space. Stack those guides when they fit
# vertically; short heatmaps keep pheatmap's original side-by-side layout.
stack_heatmap_legends <- function(gtable, gap_pt = 10) {
  i_heat <- which(gtable$layout$name == "legend")
  i_ann <- which(gtable$layout$name == "annotation_legend")
  if (length(i_heat) != 1L || length(i_ann) != 1L) return(gtable)

  heat_col <- gtable$layout$l[i_heat]
  ann_col <- gtable$layout$l[i_ann]
  heat_grob <- gtable$grobs[[i_heat]]
  ann_grob <- gtable$grobs[[i_ann]]
  legend_w <- grid::unit.pmax(gtable$widths[heat_col], gtable$widths[ann_col])
  stacked <- gtable::gtable(
    widths = legend_w,
    heights = grid::unit.c(grid::grobHeight(heat_grob),
                           grid::unit(gap_pt, "pt"),
                           grid::grobHeight(ann_grob))
  )
  stacked <- gtable::gtable_add_grob(stacked, heat_grob, t = 1, l = 1,
                                     clip = "off")
  stacked <- gtable::gtable_add_grob(stacked, ann_grob, t = 3, l = 1,
                                     clip = "off")
  row_top <- min(gtable$layout$t[c(i_heat, i_ann)])
  row_bottom <- max(gtable$layout$b[c(i_heat, i_ann)])
  available_h <- grid::convertHeight(
    sum(gtable$heights[seq.int(row_top, row_bottom)]), "in", valueOnly = TRUE
  )
  stacked_h <- grid::convertHeight(grid::grobHeight(stacked), "in",
                                   valueOnly = TRUE)
  if (!is.finite(stacked_h) || stacked_h > available_h) return(gtable)

  keep <- setdiff(seq_along(gtable$grobs), c(i_heat, i_ann))
  gtable$grobs <- gtable$grobs[keep]
  gtable$layout <- gtable$layout[keep, , drop = FALSE]
  gtable <- gtable::gtable_add_grob(
    gtable, stacked, t = row_top, b = row_bottom,
    l = heat_col, r = heat_col, clip = "off", name = "stacked_legends"
  )
  gtable$widths[heat_col] <- legend_w
  gtable$widths[ann_col] <- grid::unit(0, "pt")
  gtable
}

# pheatmap uses fixed grid units when cell width/height are supplied. Drawing a
# gtable wider than its device silently clips both dendrogram strokes and the
# rightmost annotation legend. Add a real white gutter to the gtable and derive
# the output size from the rendered object, not an estimate of its contents.
finalize_heatmap_gtable <- function(gtable, min_w = 6, min_h = 5,
                                    padding_pt = 8) {
  gtable <- stack_heatmap_legends(gtable)
  padded <- gtable::gtable_add_padding(
    gtable, grid::unit(as.numeric(padding_pt), "pt")
  )
  intrinsic <- c(
    grid::convertWidth(sum(padded$widths), "in", valueOnly = TRUE),
    grid::convertHeight(sum(padded$heights), "in", valueOnly = TRUE)
  )
  list(gtable = padded, dim = pmax(c(min_w, min_h), intrinsic))
}

# ---- Gene-symbol italic row labels (pheatmap) -------------------------------
# pheatmap has no per-row fontface, so italicise gene-symbol row names via a
# plotmath expression vector for `labels_row`. Quotes inside italic("...") keep
# special characters (e.g. HLA-DRB1, MT-CO1) literal. Returns the plain vector
# when italic is off, so callers can pass the result straight to labels_row.
italic_labels <- function(x, italic = TRUE) {
  if (!isTRUE(italic)) return(x)
  parse(text = paste0('italic("', gsub('"', '', as.character(x)), '")'))
}

# ---- Per-figure-group style override ----------------------------------------
# Return a getp() bound to figure group `key`: any non-empty key in
# style$figure_overrides[[key]] (palette, font_family, point_size, base_font_size,
# width_in, height_in) overrides the global value for that group; absent keys
# inherit the global setting. Figures stay uniform by default (no override).
getp_for <- function(style, key) {
  if (is.null(style) || !is.list(style)) style <- list()
  ov <- tryCatch(style[["figure_overrides"]][[key]], error = function(e) NULL)
  merged <- style
  if (is.list(ov)) {
    for (k in names(ov)) {
      v <- ov[[k]]
      if (!is.null(v) && nzchar(as.character(v))) merged[[k]] <- v
    }
  }
  make_getp(merged)
}

# ---- Shared theme factory ---------------------------------------------------
# theme_bw base for every ggplot figure; minor grid off, faint major grid.
# base_family NULL when no font configured (ggplot/ggrepel accept family = NULL).
make_style_theme <- function(base_size = 12, base_family = NULL,
                             label_bold = FALSE, title_bold = FALSE) {
  function(base = theme_bw) {
    t <- if (is.null(base_family)) base(base_size = base_size)
         else base(base_size = base_size, base_family = base_family)
    extra <- theme(panel.grid.minor = element_blank(),
                   panel.grid.major = element_line(linewidth = 0.25, colour = "grey92"))
    if (label_bold) extra <- extra + theme(axis.text = element_text(face = "bold"))
    if (title_bold) extra <- extra + theme(axis.title = element_text(face = "bold"))
    t + extra
  }
}

# ---- save_gg factory --------------------------------------------------------
# ggplot figure -> PNG (raster, dpi) + SVG (vector). width/height/units explicit;
# dpi raster-only.
make_save_gg <- function(fig_w = 6, fig_h = 5, fig_dpi = 300) {
  function(plot, png_path, svg_path, w = fig_w, h = fig_h) {
    # An explicit white device background makes PNG/SVG exports independent of
    # the desktop theme and prevents transparent placeholders from rendering as
    # black panels in browsers and image viewers.
    ggsave(png_path, plot, width = w, height = h, units = "in", dpi = fig_dpi,
           bg = "white")
    ggsave(svg_path, plot, width = w, height = h, units = "in", bg = "white")
  }
}
