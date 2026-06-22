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
    ggsave(png_path, plot, width = w, height = h, units = "in", dpi = fig_dpi)
    ggsave(svg_path, plot, width = w, height = h, units = "in")
  }
}
