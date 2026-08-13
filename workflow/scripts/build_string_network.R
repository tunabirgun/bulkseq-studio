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

# STRING protein-protein interaction network (0.6.0) from the DE / genes-of-
# interest set: map to STRING, fetch the interaction subnetwork, detect modules
# (Louvain) and hub genes (centrality), and export GraphML / SIF / cytoscape.js
# JSON + node/edge/hub CSVs + a static figure for further editing in Cytoscape.
# STRINGdb has NO offline mode (STRINGdb$new contacts string-db.org), so every
# step degrades to empty-but-valid outputs + a check when the network/organism is
# unavailable, rather than failing the run.

suppressMessages({
  library(STRINGdb)
  library(igraph)
  library(jsonlite)
  library(ggplot2)
  library(svglite)
  library(scales)
  library(RColorBrewer)
})

# Shared palette/theme/getp/save_gg helpers (sourced; resolved via scriptdir).
source(file.path(snakemake@scriptdir, "figure_style.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

out <- snakemake@output
organism <- tolower(as.character(snakemake@params[["organism"]]))
score_thr <- as.integer(snakemake@params[["score_threshold"]]); if (is.na(score_thr) || score_thr < 1) score_thr <- 400
taxon_override <- suppressWarnings(as.integer(snakemake@params[["taxon"]]))
seed_source <- as.character(snakemake@params[["seed_source"]])
string_version <- as.character(snakemake@params[["string_version"]]); if (!nzchar(string_version)) string_version <- "12.0"
max_seed <- as.integer(snakemake@params[["max_seed"]]); if (is.na(max_seed) || max_seed < 1) max_seed <- 400
hub_n <- as.integer(snakemake@params[["hub_labels"]]); if (is.na(hub_n) || hub_n < 0) hub_n <- 15
goi_path <- as.character(snakemake@params[["goi"]])

style <- tryCatch(snakemake@params[["style"]], error = function(e) NULL); if (!is.list(style)) style <- list()
getp <- make_getp(style)
gp <- getp_for(style, "network")  # per-group palette/font/point/base-font/scaling override
fig_w <- as.numeric(gp("width_in", 7)); fig_h <- as.numeric(gp("height_in", 6)); fig_dpi <- as.integer(getp("dpi", 300))
base_size <- as.numeric(gp("base_font_size", 12))
font_family <- as.character(gp("font_family", ""))
label_bold <- isTRUE(as.logical(getp("label_bold", FALSE)))
gene_symbol_italic <- isTRUE(as.logical(getp("gene_symbol_italic", TRUE)))
palette_name <- as.character(gp("palette", "Blue-Red"))
node_max_size <- as.numeric(getp("ppi_node_max_size", 11))
ppi_layout <- as.character(getp("ppi_layout", "fr"))

COMMUNITY_SEED <- 42L
LAYOUT_SEED <- 42L
LABEL_SEED <- 42L
PPI_PROVENANCE_SCHEMA_VERSION <- 1L
# A circular straight-edge drawing is outerplanar.  These bounds deliberately
# limit the static *rendering* attempt only: all network exports are written
# before layout, so an unsuitable dense topology remains fully inspectable in
# Cytoscape/the interactive view and in the CSV tables.
PPI_CIRCLE_SCORE_PAIR_BUDGET <- 250000
PPI_CIRCLE_SCORE_EVALUATION_BUDGET <- 96L
PPI_DENSE_STATIC_PLACEHOLDER <- paste(
  "Dense PPI topology cannot be represented as a crossing-free static view.",
  "\nUse the interactive PPI view, results/networks/ppi_hub_genes.csv, and the network tables."
)

# Inter-node topology must remain visible on the white publication canvas. The
# alpha-composited colour below clears the 3:1 non-text contrast threshold even
# for the thinnest edge; confidence is still encoded independently by linewidth.
PPI_EDGE_COLOUR <- "#374151"
PPI_EDGE_ALPHA <- 1
PPI_EDGE_MIN_CONTRAST <- 3
PPI_NODE_OUTLINE_COLOUR <- "#374151"
PPI_NODE_OUTLINE_STROKE_MM <- 0.60
PPI_REPORT_WIDTH_PX <- 760
PPI_EDGE_BAND_CUTS <- c(700L, 900L)
PPI_EDGE_BAND_WIDTH_MM <- c(0.65, 1.20, 1.80)
PPI_NODE_DIAMETER_MM <- c("1" = 4.5, "2-3" = 5.8, "4+" = 7.2)
PPI_NODE_LAYOUT_RADIUS <- c("1" = 0.65, "2-3" = 0.85, "4+" = 1.10)
PPI_NODE_CLEARANCE <- 0.55 * max(PPI_NODE_LAYOUT_RADIUS)
PPI_EDGE_NODE_CLEARANCE <- 0.65 * max(PPI_NODE_LAYOUT_RADIUS)
PPI_EDGE_EDGE_CLEARANCE <- 0.90 * max(PPI_NODE_LAYOUT_RADIUS)
PPI_COMPONENT_CLEARANCE <- 0.65
PPI_MIN_REPORT_STROKE_PX <- 1
edge_contrast_against_white <- function(colour, alpha) {
  rgb <- as.numeric(grDevices::col2rgb(colour)) / 255
  composite <- alpha * rgb + (1 - alpha)
  linear <- ifelse(composite <= 0.04045, composite / 12.92,
                   ((composite + 0.055) / 1.055)^2.4)
  luminance <- sum(c(0.2126, 0.7152, 0.0722) * linear)
  1.05 / (luminance + 0.05)
}
PPI_EDGE_EFFECTIVE_CONTRAST <- edge_contrast_against_white(
  PPI_EDGE_COLOUR, PPI_EDGE_ALPHA
)
if (!is.finite(PPI_EDGE_EFFECTIVE_CONTRAST) ||
    PPI_EDGE_EFFECTIVE_CONTRAST < PPI_EDGE_MIN_CONTRAST) {
  stop(sprintf(
    "PPI edge contrast %.2f:1 is below the required %.1f:1 against white",
    PPI_EDGE_EFFECTIVE_CONTRAST, PPI_EDGE_MIN_CONTRAST
  ))
}
ppi_linewidth_mm_to_report_px <- function(mm) {
  mm * .pt * 0.75 * PPI_REPORT_WIDTH_PX / (render_w * 72)
}

# Honour an explicit network override exactly. With the global 6 x 5 default,
# a labelled graph plus two legends is too small, so the un-overridden network
# canvas grows just enough for the configured hub-label count.
network_override <- tryCatch(style[["figure_overrides"]][["network"]], error = function(e) NULL)
has_width_override <- is.list(network_override) && !is.null(network_override[["width_in"]]) &&
  nzchar(as.character(network_override[["width_in"]]))
has_height_override <- is.list(network_override) && !is.null(network_override[["height_in"]]) &&
  nzchar(as.character(network_override[["height_in"]]))
render_w <- if (has_width_override) fig_w else max(fig_w, 11.5)
render_h <- if (has_height_override) fig_h else max(fig_h, 8.5)

pal_spec <- palette_spec(palette_name)
base_family <- resolve_font(font_family)  # map a Windows font name to an installed WSL/Linux one, like the other figures
style_theme <- make_style_theme(base_size = base_size, base_family = base_family,
                                 label_bold = label_bold)

# Mutable realized-state record. Every route, including a skipped/degraded
# network, writes the same structured sidecar and uses JSON null for facts that
# were never observed.
state <- new.env(parent = emptyenv())
state$taxon <- NA_integer_
state$query_date_utc <- NA_character_
state$string_realized_version <- NA_character_
state$string_realized_build <- NA_character_
state$seed_source <- NA_character_
state$seed_input_count <- NA_integer_
state$seed_after_limit_count <- NA_integer_
state$mapped_seed_count <- NA_integer_
state$mapped_string_id_count <- NA_integer_
state$interactions_returned_count <- NA_integer_
state$interactions_passing_threshold_count <- NA_integer_
state$realized_min_combined_score <- NA_real_
state$realized_max_combined_score <- NA_real_
state$node_count <- 0L
state$edge_count <- 0L
state$module_count <- 0L
state$hub_label_count <- 0L
state$layout_method <- NA_character_
state$layout_fallback_reason <- NA_character_
state$edge_visual_bands <- character(0)

pkg_version <- function(name) {
  tryCatch(as.character(utils::packageVersion(name)), error = function(e) NA_character_)
}
write_provenance <- function(status, reason = NA_character_) {
  payload <- list(
    schema_version = PPI_PROVENANCE_SCHEMA_VERSION,
    status = status,
    reason = reason,
    generated_at_utc = format(Sys.time(), tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ"),
    database = list(
      name = "STRING",
      configured_version = string_version,
      realized_version = state$string_realized_version,
      realized_build = state$string_realized_build,
      taxon = state$taxon,
      query_date_utc = state$query_date_utc
    ),
    software = list(
      R = paste(R.version$major, R.version$minor, sep = "."),
      STRINGdb = pkg_version("STRINGdb"),
      igraph = pkg_version("igraph"),
      ggplot2 = pkg_version("ggplot2")
    ),
    configuration = list(
      seed_source = seed_source,
      max_seed_genes = max_seed,
      score_threshold_combined = score_thr,
      string_combined_score_scale = "0-1000",
      stored_edge_weight = "combined_score / 1000",
      hub_label_count = hub_n,
      layout = ppi_layout
    ),
    realized = list(
      seed_source = state$seed_source,
      seed_input_count = state$seed_input_count,
      seed_after_limit_count = state$seed_after_limit_count,
      mapped_seed_count = state$mapped_seed_count,
      mapped_string_id_count = state$mapped_string_id_count,
      interactions_returned_count = state$interactions_returned_count,
      interactions_passing_threshold_count = state$interactions_passing_threshold_count,
      score_threshold_combined = score_thr,
      minimum_combined_score = state$realized_min_combined_score,
      maximum_combined_score = state$realized_max_combined_score,
      node_count = state$node_count,
      edge_count = state$edge_count,
      module_count = state$module_count,
      hub_label_count = state$hub_label_count,
      layout_method = state$layout_method,
      layout_fallback_reason = state$layout_fallback_reason,
      figure_width_in = render_w,
      figure_height_in = render_h
    ),
    methods = list(
      edge_source = list(
        method = "STRINGdb::get_interactions",
        evidence = "STRING combined_score integrates physical and functional association evidence; edges are not restricted to direct physical binding.",
        threshold = "combined_score >= configured threshold on the STRING 0-1000 scale",
        stored_weight = "combined_score / 1000"
      ),
      community_detection = list(
        algorithm = "igraph::cluster_louvain",
        weights = "combined_score / 1000",
        seed = COMMUNITY_SEED
      ),
      betweenness = list(
        algorithm = "igraph::betweenness",
        directed = FALSE,
        edge_distance = "1 / (combined_score / 1000)"
      ),
      layout = list(
        requested = ppi_layout,
        realized = state$layout_method,
        seed = LAYOUT_SEED
      ),
      figure_labels = list(
        algorithm = "no node labels in the static topology panel",
        selection = "none; identities and hub metrics are reported outside the static panel",
        identity_and_hub_metrics = c("interactive PPI view", "results/networks/ppi_hub_genes.csv"),
        seed = NA_integer_
      ),
      edge_visual_encoding = list(
        method = "manual occupied STRING combined-score bands",
        bands = state$edge_visual_bands,
        exact_weight_retained_in = c("edge CSV", "GraphML", "Cytoscape JSON")
      )
    )
  )
  jsonlite::write_json(payload, out[["provenance"]], pretty = TRUE,
                       auto_unbox = TRUE, na = "null")
}

write_check <- function(status, message) {
  msg <- gsub('"', '\\\\"', message)
  writeLines(sprintf('{\n  "check": "16_ppi_network",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                     status, status, msg), out[["check"]])
}
placeholder_fig <- function(msg) {
  p <- ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) +
    theme_void() +
    theme(panel.background = element_rect(fill = "white", colour = NA),
          plot.background = element_rect(fill = "white", colour = NA))
  ggsave(out[["png"]], p, width = render_w, height = render_h, units = "in",
         dpi = fig_dpi, bg = "white")
  ggsave(out[["svg"]], p, width = render_w, height = render_h, units = "in",
         bg = "white")
}
empty_graphml <- function(path) {
  # A valid empty GraphML (igraph writer), so degraded runs still import in
  # Cytoscape/igraph instead of shipping a 0-byte file that ParseErrors.
  tryCatch(igraph::write_graph(igraph::make_empty_graph(directed = FALSE), path, format = "graphml"),
           error = function(e) writeLines(paste0(
             '<?xml version="1.0" encoding="UTF-8"?>\n',
             '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
             '<graph edgedefault="undirected"></graph></graphml>'), path))
}
skip <- function(msg) {
  empty_graphml(out[["graphml"]]); writeLines(character(0), out[["sif"]])
  writeLines('{"elements":{"nodes":[],"edges":[]}}', out[["cyjs"]])
  write.csv(data.frame(id = character(0)), out[["nodes"]], row.names = FALSE)
  write.csv(data.frame(source = character(0), target = character(0)), out[["edges"]], row.names = FALSE)
  write.csv(data.frame(symbol = character(0), degree = integer(0)), out[["hubs"]], row.names = FALSE)
  placeholder_fig(msg)
  # WARNING (not PASS) so a dropped/empty network surfaces in the run-health rollup.
  write_check("WARNING", msg)
  write_provenance("WARNING", msg)
}

# NCBI RefSeq crop gene ids are LOC<GeneID>; STRING (like KEGG) maps the bare NCBI
# GeneID, not the LOC-prefixed form, so strip the prefix before mapping. Shape-gated
# to LOC + digits, so locus tags (LOC_Os.., FGSG_..) and symbols pass through.
strip_loc <- function(x) { i <- grepl("^LOC[0-9]+$", x); x[i] <- sub("^LOC", "", x[i]); x }

normalize_string_query <- function(x) {
  toupper(strip_loc(as.character(x)))
}

build_string_seed_lookup <- function(original_display_id) {
  original_display_id <- as.character(original_display_id)
  query_id <- strip_loc(original_display_id)
  normalized_query <- normalize_string_query(query_id)
  invalid <- is.na(original_display_id) | !nzchar(original_display_id) |
    is.na(normalized_query) | !nzchar(normalized_query)
  if (any(invalid)) stop("STRING seed identity contains an empty display or query identifier")

  lookup <- data.frame(
    original_display_id = original_display_id,
    query_id = query_id,
    normalized_query = normalized_query,
    stringsAsFactors = FALSE
  )
  display_ids_by_query <- split(lookup$original_display_id, lookup$normalized_query)
  ambiguous <- names(Filter(function(x) length(unique(x)) > 1L, display_ids_by_query))
  if (length(ambiguous)) {
    stop(sprintf(
      "ambiguous STRING query collision after case/LOC normalization: %s",
      paste(sort(ambiguous, method = "radix"), collapse = ", ")
    ))
  }
  lookup[!duplicated(lookup$normalized_query), , drop = FALSE]
}

restore_mapped_display_ids <- function(mapped, seed_lookup) {
  required_mapped <- c("gene_id", "STRING_id")
  required_lookup <- c("original_display_id", "query_id", "normalized_query")
  if (!is.data.frame(mapped) || !all(required_mapped %in% names(mapped))) {
    stop("STRING mapping result lacks gene_id or STRING_id")
  }
  if (!is.data.frame(seed_lookup) || !all(required_lookup %in% names(seed_lookup)) ||
      anyDuplicated(seed_lookup$normalized_query)) {
    stop("STRING seed lookup is missing required columns or has ambiguous query keys")
  }

  mapped_query <- normalize_string_query(mapped$gene_id)
  restore_at <- match(mapped_query, seed_lookup$normalized_query)
  unrestored <- is.na(mapped_query) | !nzchar(mapped_query) | is.na(restore_at)
  if (any(unrestored)) {
    failed <- unique(as.character(mapped$gene_id[unrestored]))
    failed[is.na(failed)] <- "<NA>"
    stop(sprintf(
      "STRING mapped identifiers could not be restored to original display IDs: %s",
      paste(sort(failed, method = "radix"), collapse = ", ")
    ))
  }
  mapped$gene_id <- seed_lookup$original_display_id[restore_at]
  mapped
}

tax <- NA_integer_
if (!is.na(taxon_override)) {
  tax <- taxon_override
} else if (grepl("homo|human", organism)) { tax <- 9606L
} else if (grepl("mus|mouse", organism)) { tax <- 10090L
} else if (grepl("drosophila", organism)) { tax <- 7227L
} else if (grepl("elegans|caenorhabditis", organism)) { tax <- 6239L
} else if (grepl("danio|zebrafish", organism)) { tax <- 7955L
} else if (grepl("cerevisiae|yeast", organism)) { tax <- 4932L
} else if (grepl("arabidopsis|thaliana", organism)) { tax <- 3702L }
state$taxon <- tax

ok <- tryCatch({
  if (is.na(tax)) stop(sprintf("no STRING taxid for organism '%s'", organism))
  seed_from_gene_id <- FALSE
  if (identical(seed_source, "goi") && nzchar(goi_path) && file.exists(goi_path)) {
    seed <- trimws(readLines(goi_path, warn = FALSE)); seed <- seed[nzchar(seed) & !startsWith(seed, "#")]
    state$seed_source <- "genes_of_interest"
  } else {
    up <- tryCatch(read.csv(snakemake@input[["up"]], stringsAsFactors = FALSE), error = function(e) data.frame())
    down <- tryCatch(read.csv(snakemake@input[["down"]], stringsAsFactors = FALSE), error = function(e) data.frame())
    seed <- unique(c(up$symbol, down$symbol))
    seed <- seed[!is.na(seed) & nzchar(seed)]
    state$seed_source <- "differential_expression_symbols"
    # Symbol-less genomes (e.g. Fusarium and other locus-tag annotations) have all-NA
    # symbols; fall back to gene_id, which STRING resolves directly (e.g. FGSG_* tags).
    if (length(seed) < 2) {
      seed <- unique(c(up$gene_id, down$gene_id))
      seed_from_gene_id <- TRUE
      state$seed_source <- "differential_expression_gene_ids"
    }
  }
  seed <- unique(seed[!is.na(seed) & nzchar(seed)])
  state$seed_input_count <- length(seed)
  if (length(seed) < 2) stop("fewer than 2 seed genes (no usable symbols or gene IDs)")
  if (length(seed) > max_seed) seed <- head(seed, max_seed)
  seed_lookup <- build_string_seed_lookup(seed)
  state$seed_after_limit_count <- nrow(seed_lookup)

  cache_dir <- "results/networks/string_cache"; dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  state$query_date_utc <- format(Sys.time(), tz = "UTC", format = "%Y-%m-%d")
  sdb <- STRINGdb$new(version = string_version, species = tax, score_threshold = score_thr, input_directory = cache_dir)
  state$string_realized_version <- tryCatch(as.character(sdb$version), error = function(e) string_version)
  if (!length(state$string_realized_version) || !nzchar(state$string_realized_version[1])) {
    state$string_realized_version <- string_version
  }
  state$string_realized_build <- tryCatch(as.character(sdb$stable_url), error = function(e) NA_character_)
  mapped <- sdb$map(
    data.frame(gene_id = seed_lookup$query_id, stringsAsFactors = FALSE),
    "gene_id", removeUnmappedRows = TRUE
  )
  if (is.null(mapped) || nrow(mapped) < 2) stop("fewer than 2 genes mapped to STRING")
  mapped <- restore_mapped_display_ids(mapped, seed_lookup)
  state$mapped_seed_count <- length(unique(mapped$gene_id))
  state$mapped_string_id_count <- length(unique(mapped$STRING_id))
  inter <- sdb$get_interactions(unique(mapped$STRING_id))
  if (is.null(inter) || nrow(inter) < 1) stop("no interactions returned")
  state$interactions_returned_count <- nrow(inter)
  # STRINGdb$new(score_threshold=) does NOT reliably filter get_interactions() output, so a
  # rebuild at a different confidence returned the same edges ("rebuild does nothing"). Filter
  # explicitly on the combined score (STRING's 0-1000 scale) so the threshold actually applies.
  inter <- inter[!is.na(inter$combined_score) & inter$combined_score >= score_thr, , drop = FALSE]
  if (nrow(inter) < 1) stop(sprintf("no interactions at combined_score >= %d", score_thr))
  state$interactions_passing_threshold_count <- nrow(inter)
  state$realized_min_combined_score <- min(inter$combined_score)
  state$realized_max_combined_score <- max(inter$combined_score)

  id2sym <- tapply(mapped$gene_id, mapped$STRING_id, function(x) x[1])
  edf <- data.frame(from = id2sym[inter$from], to = id2sym[inter$to],
                    weight = inter$combined_score / 1000, stringsAsFactors = FALSE)
  edf <- edf[!is.na(edf$from) & !is.na(edf$to) & edf$from != edf$to, ]
  if (nrow(edf) < 1) stop("no symbol-resolvable interactions")
  g <- igraph::simplify(igraph::graph_from_data_frame(edf, directed = FALSE), edge.attr.comb = "max")

  set.seed(COMMUNITY_SEED)
  comm <- igraph::cluster_louvain(g, weights = igraph::E(g)$weight)
  V(g)$module <- igraph::membership(comm)
  V(g)$degree <- igraph::degree(g)
  # STRING edge weights are combined_score/1000 (a similarity); betweenness treats the weight
  # as a distance, so invert it — else high-confidence edges count as the longest paths and
  # centrality routes around the true hubs.
  V(g)$betweenness <- igraph::betweenness(g, weights = 1 / igraph::E(g)$weight)
  state$node_count <- igraph::vcount(g)
  state$edge_count <- igraph::ecount(g)
  state$module_count <- length(unique(V(g)$module))
  resdf <- read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE, check.names = FALSE)
  # Case-insensitive join keyed on whichever id seeded the network (symbol, else
  # gene_id), so nodes are not all-NA log2FC for locus-tag genomes. STRING may also
  # return a different symbol case than the DE table.
  key_col <- if (seed_from_gene_id) strip_loc(resdf$gene_id) else resdf$symbol
  node_key <- if (seed_from_gene_id) strip_loc(V(g)$name) else V(g)$name
  lfc_map <- setNames(resdf$log2FoldChange, toupper(as.character(key_col)))
  V(g)$log2FC <- unname(lfc_map[toupper(as.character(node_key))])

  igraph::write_graph(g, out[["graphml"]], format = "graphml")
  el <- igraph::as_edgelist(g)
  writeLines(apply(el, 1, function(r) paste(r[1], "interacts", r[2], sep = "\t")), out[["sif"]])
  nodes_df <- data.frame(id = V(g)$name, module = V(g)$module, degree = V(g)$degree,
                         betweenness = V(g)$betweenness, log2FC = V(g)$log2FC, stringsAsFactors = FALSE)
  write.csv(nodes_df, out[["nodes"]], row.names = FALSE)
  edges_df <- data.frame(source = el[, 1], target = el[, 2], weight = igraph::E(g)$weight, stringsAsFactors = FALSE)
  write.csv(edges_df, out[["edges"]], row.names = FALSE)
  nodes_j <- lapply(seq_len(nrow(nodes_df)), function(i) list(data = as.list(nodes_df[i, , drop = FALSE])))
  edges_j <- lapply(seq_len(nrow(edges_df)), function(i) list(data = as.list(edges_df[i, , drop = FALSE])))
  writeLines(toJSON(list(elements = list(nodes = nodes_j, edges = edges_j)), auto_unbox = TRUE, na = "null"), out[["cyjs"]])
  hub_df <- nodes_df[order(-nodes_df$degree, nodes_df$id, method = "radix"),
                     c("id", "degree", "betweenness", "module", "log2FC")]
  names(hub_df)[1] <- "symbol"
  write.csv(hub_df, out[["hubs"]], row.names = FALSE)

  fig_error <- NA_character_
  fig_ok <- tryCatch({
  # The retained graphs are often disconnected. A single force-directed layout
  # overlays small components and makes both true and false adjacencies appear.
  # Lay out each component independently, prove its geometry, then shelf-pack
  # the expanded component boxes. The layout is deterministic and fails closed
  # if a graph cannot be drawn with straight, unambiguous topology edges.
  point_segment_distance <- function(px, py, ax, ay, bx, by) {
    dx <- bx - ax; dy <- by - ay
    denom <- dx * dx + dy * dy
    if (!is.finite(denom) || denom <= .Machine$double.eps) {
      return(sqrt((px - ax)^2 + (py - ay)^2))
    }
    t <- max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / denom))
    sqrt((px - (ax + t * dx))^2 + (py - (ay + t * dy))^2)
  }
  orientation <- function(ax, ay, bx, by, cx, cy) {
    (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
  }
  on_segment <- function(ax, ay, bx, by, px, py, tol = 1e-10) {
    abs(orientation(ax, ay, bx, by, px, py)) <= tol &&
      px >= min(ax, bx) - tol && px <= max(ax, bx) + tol &&
      py >= min(ay, by) - tol && py <= max(ay, by) + tol
  }
  segments_intersect <- function(a, b, tol = 1e-10) {
    o1 <- orientation(a$x, a$y, a$xend, a$yend, b$x, b$y)
    o2 <- orientation(a$x, a$y, a$xend, a$yend, b$xend, b$yend)
    o3 <- orientation(b$x, b$y, b$xend, b$yend, a$x, a$y)
    o4 <- orientation(b$x, b$y, b$xend, b$yend, a$xend, a$yend)
    if (((o1 > tol && o2 < -tol) || (o1 < -tol && o2 > tol)) &&
        ((o3 > tol && o4 < -tol) || (o3 < -tol && o4 > tol))) return(TRUE)
    on_segment(a$x, a$y, a$xend, a$yend, b$x, b$y, tol) ||
      on_segment(a$x, a$y, a$xend, a$yend, b$xend, b$yend, tol) ||
      on_segment(b$x, b$y, b$xend, b$yend, a$x, a$y, tol) ||
      on_segment(b$x, b$y, b$xend, b$yend, a$xend, a$yend, tol)
  }
  segment_distance <- function(a, b) {
    if (segments_intersect(a, b)) return(0)
    min(
      point_segment_distance(a$x, a$y, b$x, b$y, b$xend, b$yend),
      point_segment_distance(a$xend, a$yend, b$x, b$y, b$xend, b$yend),
      point_segment_distance(b$x, b$y, a$x, a$y, a$xend, a$yend),
      point_segment_distance(b$xend, b$yend, a$x, a$y, a$xend, a$yend)
    )
  }
  circle_score_evaluations <- 0L
  circle_crossing_count <- function(vertex_order, component_edges) {
    if (nrow(component_edges) < 2) return(0L)
    edge_pair_count <- nrow(component_edges) * (nrow(component_edges) - 1) / 2
    if (!is.finite(edge_pair_count) || edge_pair_count > PPI_CIRCLE_SCORE_PAIR_BUDGET) {
      stop(sprintf(
        "component crossing score requires %.0f edge-pair checks; deterministic static-layout budget is %.0f",
        edge_pair_count, PPI_CIRCLE_SCORE_PAIR_BUDGET
      ))
    }
    if (circle_score_evaluations >= PPI_CIRCLE_SCORE_EVALUATION_BUDGET) {
      stop(sprintf(
        "crossing-free static-layout search exhausted its deterministic %d-evaluation budget",
        PPI_CIRCLE_SCORE_EVALUATION_BUDGET
      ))
    }
    circle_score_evaluations <<- circle_score_evaluations + 1L
    pos <- setNames(seq_along(vertex_order), vertex_order)
    a <- unname(pos[component_edges$source]); b <- unname(pos[component_edges$target])
    lo <- pmin(a, b); hi <- pmax(a, b)
    pairs <- utils::combn(seq_len(nrow(component_edges)), 2)
    disjoint <- component_edges$source[pairs[1, ]] != component_edges$source[pairs[2, ]] &
      component_edges$source[pairs[1, ]] != component_edges$target[pairs[2, ]] &
      component_edges$target[pairs[1, ]] != component_edges$source[pairs[2, ]] &
      component_edges$target[pairs[1, ]] != component_edges$target[pairs[2, ]]
    if (!any(disjoint)) return(0L)
    i <- pairs[1, disjoint]; j <- pairs[2, disjoint]
    inside_c <- lo[j] > lo[i] & lo[j] < hi[i]
    inside_d <- hi[j] > lo[i] & hi[j] < hi[i]
    as.integer(sum(xor(inside_c, inside_d)))
  }
  optimise_circle_order <- function(component_graph, component_edges, component_rank) {
    ids <- sort(igraph::V(component_graph)$name, method = "radix")
    outerplanar_edge_bound <- if (length(ids) <= 1L) 0L else 2L * length(ids) - 3L
    if (nrow(component_edges) > outerplanar_edge_bound) {
      stop(sprintf(
        "dense component %d has %d vertices and %d edges, exceeding the crossing-free circular bound of %d",
        component_rank, length(ids), nrow(component_edges), outerplanar_edge_bound
      ))
    }
    if (length(ids) <= 3 || nrow(component_edges) <= 1) return(ids)
    set.seed(LAYOUT_SEED + as.integer(component_rank))
    fr <- igraph::layout_with_fr(component_graph, niter = 1500, grid = "nogrid")
    fr_order <- igraph::V(component_graph)$name[
      order(atan2(fr[, 2], fr[, 1]), igraph::V(component_graph)$name, method = "radix")
    ]
    root <- match(min(igraph::V(component_graph)$name), igraph::V(component_graph)$name)
    dfs_order <- igraph::as_ids(igraph::dfs(
      component_graph, root = root, mode = "all", order = TRUE
    )$order)
    candidates <- list(ids, fr_order, dfs_order)
    scores <- vapply(candidates, circle_crossing_count, integer(1),
                     component_edges = component_edges)
    best <- candidates[[which.min(scores)]]; best_score <- min(scores)
    if (best_score == 0L) return(best)
    restarts <- 5L
    remaining_evaluations <- PPI_CIRCLE_SCORE_EVALUATION_BUDGET - circle_score_evaluations
    if (remaining_evaluations <= 0L) {
      stop(sprintf(
        "crossing-free static-layout search exhausted its deterministic %d-evaluation budget",
        PPI_CIRCLE_SCORE_EVALUATION_BUDGET
      ))
    }
    iterations <- max(1L, floor(remaining_evaluations / restarts))
    for (restart in seq_len(restarts)) {
      current <- if (restart <= length(candidates)) candidates[[restart]] else sample(best)
      current_score <- circle_crossing_count(current, component_edges)
      for (iteration in seq_len(iterations)) {
        swap <- sample.int(length(current), 2)
        proposal <- current
        proposal[swap] <- proposal[rev(swap)]
        proposal_score <- circle_crossing_count(proposal, component_edges)
        delta <- proposal_score - current_score
        temperature <- max(0.04, 1.8 * (1 - iteration / iterations))
        if (delta <= 0 || stats::runif(1) < exp(-delta / temperature)) {
          current <- proposal; current_score <- proposal_score
        }
        if (current_score < best_score) {
          best <- current; best_score <- current_score
          if (best_score == 0L) return(best)
        }
      }
    }
    stop(sprintf(
      "component %d cannot be rendered without straight-edge crossings (minimum %d)",
      component_rank, best_score
    ))
  }
  trim_component_edges <- function(coords, component_edges, radii) {
    index <- setNames(seq_len(nrow(coords)), coords$id)
    rows <- lapply(seq_len(nrow(component_edges)), function(i) {
      s <- component_edges$source[i]; t <- component_edges$target[i]
      a <- coords[index[[s]], ]; b <- coords[index[[t]], ]
      dx <- b$x - a$x; dy <- b$y - a$y; distance <- sqrt(dx^2 + dy^2)
      visible <- distance - radii[[s]] - radii[[t]]
      if (!is.finite(visible) || visible <= 2 * PPI_EDGE_EDGE_CLEARANCE) {
        return(NULL)
      }
      ux <- dx / distance; uy <- dy / distance
      data.frame(
        source = s, target = t,
        x = a$x + ux * radii[[s]], y = a$y + uy * radii[[s]],
        xend = b$x - ux * radii[[t]], yend = b$y - uy * radii[[t]],
        weight = component_edges$weight[i], stringsAsFactors = FALSE
      )
    })
    if (any(vapply(rows, is.null, logical(1)))) return(NULL)
    do.call(rbind, rows)
  }
  component_geometry_clear <- function(coords, edge_rows, radii) {
    if (is.null(edge_rows)) return(FALSE)
    if (nrow(coords) > 1) {
      pairs <- utils::combn(seq_len(nrow(coords)), 2)
      gaps <- sqrt((coords$x[pairs[1, ]] - coords$x[pairs[2, ]])^2 +
                     (coords$y[pairs[1, ]] - coords$y[pairs[2, ]])^2) -
        radii[coords$id[pairs[1, ]]] - radii[coords$id[pairs[2, ]]]
      if (any(gaps < PPI_NODE_CLEARANCE - 1e-9)) return(FALSE)
    }
    if (nrow(edge_rows)) {
      for (edge_i in seq_len(nrow(edge_rows))) {
        e <- edge_rows[edge_i, ]
        other <- setdiff(coords$id, c(e$source, e$target))
        for (id in other) {
          p <- coords[coords$id == id, ]
          clearance <- point_segment_distance(p$x, p$y, e$x, e$y, e$xend, e$yend) - radii[[id]]
          if (clearance < PPI_EDGE_NODE_CLEARANCE - 1e-9) return(FALSE)
        }
      }
    }
    if (nrow(edge_rows) > 1) {
      pairs <- utils::combn(seq_len(nrow(edge_rows)), 2)
      for (pair_i in seq_len(ncol(pairs))) {
        a <- edge_rows[pairs[1, pair_i], ]; b <- edge_rows[pairs[2, pair_i], ]
        if (length(intersect(c(a$source, a$target), c(b$source, b$target)))) next
        if (segment_distance(a, b) < PPI_EDGE_EDGE_CLEARANCE - 1e-9) return(FALSE)
      }
    }
    TRUE
  }
  make_component_layout <- function(component_ids, component_rank) {
    component_graph <- igraph::induced_subgraph(g, vids = component_ids)
    component_edges <- edges_df[
      edges_df$source %in% component_ids & edges_df$target %in% component_ids,
      , drop = FALSE
    ]
    component_edges <- component_edges[order(
      pmin(component_edges$source, component_edges$target),
      pmax(component_edges$source, component_edges$target), method = "radix"
    ), , drop = FALSE]
    vertex_order <- optimise_circle_order(component_graph, component_edges, component_rank)
    if (circle_crossing_count(vertex_order, component_edges) != 0L) {
      stop(sprintf("component %d retained an edge crossing", component_rank))
    }
    radii <- setNames(
      PPI_NODE_LAYOUT_RADIUS[as.character(node_plot$degree_band[match(vertex_order, node_plot$id)])],
      vertex_order
    )
    n <- length(vertex_order)
    if (n == 1L) {
      unit_coords <- data.frame(id = vertex_order, x = 0, y = 0, stringsAsFactors = FALSE)
      base_radius <- 1
    } else if (n == 2L) {
      unit_coords <- data.frame(id = vertex_order, x = c(-1, 1), y = 0, stringsAsFactors = FALSE)
      base_radius <- (sum(radii) + PPI_NODE_CLEARANCE) / 2
    } else {
      theta <- pi / 2 + 2 * pi * (seq_len(n) - 1) / n
      unit_coords <- data.frame(id = vertex_order, x = cos(theta), y = sin(theta),
                                stringsAsFactors = FALSE)
      pair <- utils::combn(seq_len(n), 2)
      step <- abs(pair[1, ] - pair[2, ])
      step <- pmin(step, n - step)
      chord <- 2 * sin(pi * step / n)
      needed <- radii[vertex_order[pair[1, ]]] + radii[vertex_order[pair[2, ]]] +
        PPI_NODE_CLEARANCE
      base_radius <- max(needed / chord)
    }
    for (growth in 0:28) {
      scale <- base_radius * 1.13^growth
      coords <- unit_coords
      coords$x <- coords$x * scale; coords$y <- coords$y * scale
      edge_rows <- trim_component_edges(coords, component_edges, radii)
      if (component_geometry_clear(coords, edge_rows, radii)) {
        extent <- PPI_COMPONENT_CLEARANCE / 2
        bbox <- c(xmin = min(coords$x - radii[coords$id]) - extent,
                  xmax = max(coords$x + radii[coords$id]) + extent,
                  ymin = min(coords$y - radii[coords$id]) - extent,
                  ymax = max(coords$y + radii[coords$id]) + extent)
        return(list(nodes = coords, edges = edge_rows, radii = radii, bbox = bbox,
                    order = vertex_order))
      }
    }
    stop(sprintf("component %d could not meet node/edge clearance gates", component_rank))
  }

  node_plot <- nodes_df
  node_plot$degree_band <- factor(
    ifelse(node_plot$degree >= 4, "4+", ifelse(node_plot$degree >= 2, "2-3", "1")),
    levels = names(PPI_NODE_DIAMETER_MM)
  )
  component_membership <- igraph::components(g)$membership
  component_ids <- split(names(component_membership), component_membership)
  component_order <- order(
    -vapply(component_ids, length, integer(1)),
    vapply(component_ids, function(x) min(x), character(1)), method = "radix"
  )
  component_ids <- component_ids[component_order]
  component_layouts <- lapply(seq_along(component_ids), function(i) {
    make_component_layout(sort(component_ids[[i]], method = "radix"), i)
  })

  component_width <- vapply(component_layouts, function(x) x$bbox[["xmax"]] - x$bbox[["xmin"]], numeric(1))
  component_height <- vapply(component_layouts, function(x) x$bbox[["ymax"]] - x$bbox[["ymin"]], numeric(1))
  shelf_target <- max(max(component_width), sqrt(sum(
    (component_width + PPI_COMPONENT_CLEARANCE) *
      (component_height + PPI_COMPONENT_CLEARANCE)
  )) * 1.32)
  cursor_x <- 0; cursor_y <- 0; row_height <- 0
  packed_nodes <- list(); packed_edges <- list()
  for (i in seq_along(component_layouts)) {
    item <- component_layouts[[i]]
    width <- component_width[i]; height <- component_height[i]
    if (cursor_x > 0 && cursor_x + width > shelf_target) {
      cursor_x <- 0
      cursor_y <- cursor_y + row_height + PPI_COMPONENT_CLEARANCE
      row_height <- 0
    }
    dx <- cursor_x - item$bbox[["xmin"]]
    dy <- cursor_y - item$bbox[["ymin"]]
    item$nodes$x <- item$nodes$x + dx; item$nodes$y <- item$nodes$y + dy
    if (nrow(item$edges)) {
      item$edges$x <- item$edges$x + dx; item$edges$xend <- item$edges$xend + dx
      item$edges$y <- item$edges$y + dy; item$edges$yend <- item$edges$yend + dy
      packed_edges[[length(packed_edges) + 1L]] <- item$edges
    }
    item$nodes$component_rank <- i
    packed_nodes[[length(packed_nodes) + 1L]] <- item$nodes
    cursor_x <- cursor_x + width + PPI_COMPONENT_CLEARANCE
    row_height <- max(row_height, height)
  }
  packed_node_coords <- do.call(rbind, packed_nodes)
  edge_plot <- do.call(rbind, packed_edges)
  layout_x_center <- mean(range(packed_node_coords$x))
  layout_y_center <- mean(range(packed_node_coords$y))
  packed_node_coords$x <- packed_node_coords$x - layout_x_center
  packed_node_coords$y <- packed_node_coords$y - layout_y_center
  edge_plot$x <- edge_plot$x - layout_x_center
  edge_plot$xend <- edge_plot$xend - layout_x_center
  edge_plot$y <- edge_plot$y - layout_y_center
  edge_plot$yend <- edge_plot$yend - layout_y_center
  node_plot <- merge(node_plot, packed_node_coords, by = "id", all.x = TRUE, sort = FALSE)
  node_plot <- node_plot[match(nodes_df$id, node_plot$id), , drop = FALSE]
  node_plot$layout_radius <- PPI_NODE_LAYOUT_RADIUS[as.character(node_plot$degree_band)]
  if (any(!is.finite(node_plot$x)) || any(!is.finite(node_plot$y))) {
    stop("component packing produced non-finite node coordinates")
  }

  # Global fail-closed topology gates. Component boxes already include node and
  # antialiasing clearance, but verify the packed result rather than trusting the
  # packer. Every topology edge is tested against every non-owner node and edge.
  global_radii <- setNames(node_plot$layout_radius, node_plot$id)
  node_pairs <- utils::combn(seq_len(nrow(node_plot)), 2)
  node_gaps <- sqrt((node_plot$x[node_pairs[1, ]] - node_plot$x[node_pairs[2, ]])^2 +
                      (node_plot$y[node_pairs[1, ]] - node_plot$y[node_pairs[2, ]])^2) -
    node_plot$layout_radius[node_pairs[1, ]] - node_plot$layout_radius[node_pairs[2, ]]
  if (any(node_gaps < PPI_NODE_CLEARANCE - 1e-9)) {
    stop("component packing failed: node disks overlap or lack the required margin")
  }
  for (edge_i in seq_len(nrow(edge_plot))) {
    e <- edge_plot[edge_i, ]
    for (id in setdiff(node_plot$id, c(e$source, e$target))) {
      pnode <- node_plot[node_plot$id == id, ]
      clearance <- point_segment_distance(pnode$x, pnode$y, e$x, e$y, e$xend, e$yend) -
        global_radii[[id]]
      if (clearance < PPI_EDGE_NODE_CLEARANCE - 1e-9) {
        stop(sprintf("component packing failed: edge %s--%s approaches non-owner node %s",
                     e$source, e$target, id))
      }
    }
  }
  if (nrow(edge_plot) > 1) {
    edge_pairs <- utils::combn(seq_len(nrow(edge_plot)), 2)
    for (pair_i in seq_len(ncol(edge_pairs))) {
      a <- edge_plot[edge_pairs[1, pair_i], ]; b <- edge_plot[edge_pairs[2, pair_i], ]
      if (length(intersect(c(a$source, a$target), c(b$source, b$target)))) next
      if (segment_distance(a, b) < PPI_EDGE_EDGE_CLEARANCE - 1e-9) {
        stop(sprintf("component packing failed: unrelated edges %s--%s and %s--%s touch",
                     a$source, a$target, b$source, b$target))
      }
    }
  }
  state$layout_method <- "deterministic component-wise crossing-optimised circular shelf"
  state$layout_fallback_reason <- NA_character_

  band_boundaries <- sort(unique(c(
    score_thr,
    PPI_EDGE_BAND_CUTS[PPI_EDGE_BAND_CUTS > score_thr & PPI_EDGE_BAND_CUTS <= 1000L],
    1001L
  )))
  if (length(band_boundaries) < 2) stop("configured STRING score threshold exceeds 1000")
  band_labels <- sprintf("%d-%d", head(band_boundaries, -1), tail(band_boundaries, -1) - 1L)
  edge_plot$score_band <- cut(
    edge_plot$weight * 1000,
    breaks = band_boundaries,
    labels = band_labels,
    right = FALSE,
    include.lowest = TRUE
  )
  if (any(is.na(edge_plot$score_band))) stop("an edge did not map to a confidence band")
  occupied_bands <- levels(droplevels(edge_plot$score_band))
  state$edge_visual_bands <- occupied_bands
  band_lower <- as.numeric(sub("-.*$", "", occupied_bands))
  band_width <- ifelse(
    band_lower >= 900, PPI_EDGE_BAND_WIDTH_MM[3],
    ifelse(band_lower >= 700, PPI_EDGE_BAND_WIDTH_MM[2], PPI_EDGE_BAND_WIDTH_MM[1])
  )
  names(band_width) <- occupied_bands
  if (min(ppi_linewidth_mm_to_report_px(band_width)) < PPI_MIN_REPORT_STROKE_PX) {
    stop("the thinnest confidence band is below one pixel at the report width")
  }
  if (length(band_width) > 1 &&
      any(diff(ppi_linewidth_mm_to_report_px(sort(unique(band_width)))) < PPI_MIN_REPORT_STROKE_PX)) {
    stop("occupied confidence-band linewidths are not distinguishable at the report width")
  }
  if (ppi_linewidth_mm_to_report_px(PPI_NODE_OUTLINE_STROKE_MM) < PPI_MIN_REPORT_STROKE_PX) {
    stop("node outlines are below one pixel at the report width")
  }

  state$hub_label_count <- 0L
  lfc <- node_plot$log2FC
  fin <- lfc[is.finite(lfc)]
  zlim <- if (length(fin)) max(abs(fin)) else 1
  if (!is.finite(zlim) || zlim <= 0) zlim <- 1
  network_x <- range(c(node_plot$x - node_plot$layout_radius,
                       node_plot$x + node_plot$layout_radius))
  network_y <- range(c(node_plot$y - node_plot$layout_radius,
                       node_plot$y + node_plot$layout_radius))
  x_span <- diff(network_x); y_span <- diff(network_y)
  if (!is.finite(x_span) || x_span <= 0 || !is.finite(y_span) || y_span <= 0) {
    stop("packed network has a degenerate plotting extent")
  }
  plot_xlim <- c(network_x[1] - 0.03 * x_span, network_x[2] + 0.03 * x_span)
  plot_ylim <- c(network_y[1] - 0.03 * y_span, network_y[2] + 0.03 * y_span)
  circle_angles <- seq(0, 2 * pi, length.out = 73)[-73]
  node_polygon <- do.call(rbind, lapply(seq_len(nrow(node_plot)), function(i) {
    data.frame(
      id = node_plot$id[i],
      x = node_plot$x[i] + node_plot$layout_radius[i] * cos(circle_angles),
      y = node_plot$y[i] + node_plot$layout_radius[i] * sin(circle_angles),
      log2FC = node_plot$log2FC[i],
      stringsAsFactors = FALSE
    )
  }))
  degree_legend <- data.frame(
    x = plot_xlim[1], y = plot_ylim[1],
    degree_band = factor(names(PPI_NODE_DIAMETER_MM), levels = names(PPI_NODE_DIAMETER_MM))
  )
  p <- ggplot() +
    geom_segment(
      data = edge_plot,
      aes(x = x, y = y, xend = xend, yend = yend, linewidth = score_band),
      colour = PPI_EDGE_COLOUR, alpha = PPI_EDGE_ALPHA, lineend = "round"
    ) +
    scale_linewidth_manual(
      values = band_width,
      name = "STRING combined score",
      drop = TRUE
    ) +
    geom_polygon(
      data = node_polygon,
      aes(x = x, y = y, group = id, fill = log2FC),
      colour = PPI_NODE_OUTLINE_COLOUR,
      linewidth = PPI_NODE_OUTLINE_STROKE_MM,
      linejoin = "round"
    ) +
    geom_point(
      data = degree_legend,
      aes(x = x, y = y, size = degree_band),
      shape = 21, fill = "#D9E2EC", colour = PPI_NODE_OUTLINE_COLOUR,
      stroke = PPI_NODE_OUTLINE_STROKE_MM, alpha = 0,
      show.legend = TRUE, inherit.aes = FALSE
    ) +
    scale_size_manual(values = PPI_NODE_DIAMETER_MM, name = "Node degree", drop = FALSE) +
    scale_fill_gradientn(
      colours = pal_spec$div(255), limits = c(-zlim, zlim),
      oob = scales::squish, na.value = "grey78", name = "log2 fold change"
    ) +
    scale_x_continuous(limits = plot_xlim, expand = expansion(mult = 0)) +
    scale_y_continuous(limits = plot_ylim, expand = expansion(mult = 0)) +
    coord_equal(clip = "off") +
    style_theme(theme_void) +
    labs(caption = paste(
      "Static view emphasizes topology; node identities and exact hub metrics are available",
      "in the interactive PPI view and results/networks/ppi_hub_genes.csv."
    )) +
    guides(
      fill = guide_colorbar(order = 1, title.position = "top",
                            barheight = grid::unit(34, "mm")),
      size = guide_legend(order = 2, title.position = "top",
                          override.aes = list(fill = "#D9E2EC", colour = PPI_NODE_OUTLINE_COLOUR,
                                              alpha = 1)),
      linewidth = guide_legend(order = 3, title.position = "top")
    ) +
    theme(
      panel.grid = element_blank(),
      panel.grid.major = element_blank(),
      panel.grid.minor = element_blank(),
      panel.background = element_rect(fill = "white", colour = NA),
      plot.background = element_rect(fill = "white", colour = NA),
      legend.background = element_rect(fill = "white", colour = NA),
      legend.key = element_rect(fill = "white", colour = NA),
      legend.position = "right",
      legend.box = "vertical",
      legend.title = element_text(size = max(9, base_size - 1), face = "bold"),
      legend.text = element_text(size = max(8, base_size - 2)),
      plot.caption = element_text(size = max(8, base_size - 2), hjust = 0,
                                  colour = "#374151", margin = margin(t = 8)),
      plot.margin = margin(18, 24, 18, 18)
    )
  validate_exported_ppi_svg <- function(path) {
    svg_lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
    attr_value <- function(tag, name) {
      pattern <- sprintf(".*\\b%s='([^']+)'.*", name)
      if (!grepl(pattern, tag, perl = TRUE)) stop(sprintf("SVG tag lacks %s", name))
      sub(pattern, "\\1", tag, perl = TRUE)
    }
    attr_number <- function(tag, name) as.numeric(attr_value(tag, name))
    style_number <- function(tag, name) {
      pattern <- sprintf(".*%s: ([0-9.]+);.*", name)
      if (!grepl(pattern, tag, perl = TRUE)) stop(sprintf("SVG style lacks %s", name))
      as.numeric(sub(pattern, "\\1", tag, perl = TRUE))
    }
    svg_tag <- svg_lines[grepl("^<svg ", svg_lines)][1]
    viewbox <- as.numeric(strsplit(attr_value(svg_tag, "viewBox"), " +")[[1]])
    if (length(viewbox) != 4 || any(!is.finite(viewbox))) stop("invalid PPI SVG viewBox")
    svg_width <- viewbox[3]; svg_height <- viewbox[4]
    report_scale <- PPI_REPORT_WIDTH_PX / svg_width

    line_tags <- svg_lines[grepl("^<line ", svg_lines)]
    if (length(line_tags) < nrow(edge_plot)) stop("exported SVG is missing topology edges")
    edge_tags <- line_tags[seq_len(nrow(edge_plot))]
    svg_edges <- data.frame(
      x = vapply(edge_tags, attr_number, numeric(1), name = "x1"),
      y = vapply(edge_tags, attr_number, numeric(1), name = "y1"),
      xend = vapply(edge_tags, attr_number, numeric(1), name = "x2"),
      yend = vapply(edge_tags, attr_number, numeric(1), name = "y2"),
      width = vapply(edge_tags, style_number, numeric(1), name = "stroke-width")
    )
    dx <- edge_plot$xend - edge_plot$x
    dy <- edge_plot$yend - edge_plot$y
    sx <- stats::median((svg_edges$xend[abs(dx) > 1e-8] - svg_edges$x[abs(dx) > 1e-8]) /
                          dx[abs(dx) > 1e-8])
    sy <- stats::median((svg_edges$yend[abs(dy) > 1e-8] - svg_edges$y[abs(dy) > 1e-8]) /
                          dy[abs(dy) > 1e-8])
    if (!is.finite(sx) || !is.finite(sy) || abs(abs(sx) - abs(sy)) > 0.03) {
      stop("exported PPI SVG does not preserve the equal-aspect topology transform")
    }
    intercept_x <- stats::median(c(svg_edges$x - sx * edge_plot$x,
                                    svg_edges$xend - sx * edge_plot$xend))
    intercept_y <- stats::median(c(svg_edges$y - sy * edge_plot$y,
                                    svg_edges$yend - sy * edge_plot$yend))
    line_residual <- max(abs(c(
      svg_edges$x - (intercept_x + sx * edge_plot$x),
      svg_edges$xend - (intercept_x + sx * edge_plot$xend),
      svg_edges$y - (intercept_y + sy * edge_plot$y),
      svg_edges$yend - (intercept_y + sy * edge_plot$yend)
    )))
    if (!is.finite(line_residual) || line_residual > 0.08) {
      stop("exported PPI SVG topology coordinates differ from the gated layout")
    }

    polygon_tags <- svg_lines[
      grepl("^<polygon ", svg_lines) &
        grepl(sprintf("stroke: %s", PPI_NODE_OUTLINE_COLOUR), svg_lines, fixed = TRUE)
    ]
    if (length(polygon_tags) != nrow(node_plot)) {
      stop(sprintf("exported PPI SVG has %d node polygons; expected %d",
                   length(polygon_tags), nrow(node_plot)))
    }
    polygon_box <- function(tag) {
      tokens <- strsplit(attr_value(tag, "points"), " +")[[1]]
      tokens <- tokens[nzchar(tokens)]
      values <- do.call(rbind, strsplit(tokens, ",", fixed = TRUE))
      values <- matrix(as.numeric(values), ncol = 2)
      c(cx = mean(range(values[, 1])), cy = mean(range(values[, 2])),
        rx = diff(range(values[, 1])) / 2, ry = diff(range(values[, 2])) / 2)
    }
    actual_boxes <- t(vapply(polygon_tags, polygon_box, numeric(4)))
    predicted <- data.frame(
      id = node_plot$id,
      cx = intercept_x + sx * node_plot$x,
      cy = intercept_y + sy * node_plot$y,
      radius = abs(sx) * node_plot$layout_radius,
      stringsAsFactors = FALSE
    )
    remaining <- seq_len(nrow(actual_boxes))
    matched <- integer(nrow(predicted))
    for (i in seq_len(nrow(predicted))) {
      distance <- sqrt((actual_boxes[remaining, "cx"] - predicted$cx[i])^2 +
                         (actual_boxes[remaining, "cy"] - predicted$cy[i])^2)
      nearest <- which.min(distance)
      if (!length(nearest) || distance[nearest] > 0.12) {
        stop(sprintf("exported PPI SVG is missing node %s at its gated coordinate", predicted$id[i]))
      }
      matched[i] <- remaining[nearest]
      remaining <- remaining[-nearest]
    }
    node_svg <- data.frame(
      id = predicted$id,
      cx = actual_boxes[matched, "cx"], cy = actual_boxes[matched, "cy"],
      radius = rowMeans(actual_boxes[matched, c("rx", "ry"), drop = FALSE]),
      stringsAsFactors = FALSE
    )
    if (max(abs(actual_boxes[matched, "rx"] - actual_boxes[matched, "ry"])) > 0.08 ||
        max(abs(node_svg$radius - predicted$radius)) > 0.12) {
      stop("exported PPI node polygons are not the gated circular disks")
    }
    outline_width <- style_number(polygon_tags[1], "stroke-width")
    aa_margin <- 0.35
    node_index_svg <- setNames(seq_len(nrow(node_svg)), node_svg$id)
    node_pairs_svg <- utils::combn(seq_len(nrow(node_svg)), 2)
    node_gap_svg <- sqrt(
      (node_svg$cx[node_pairs_svg[1, ]] - node_svg$cx[node_pairs_svg[2, ]])^2 +
        (node_svg$cy[node_pairs_svg[1, ]] - node_svg$cy[node_pairs_svg[2, ]])^2
    ) - node_svg$radius[node_pairs_svg[1, ]] - node_svg$radius[node_pairs_svg[2, ]] -
      outline_width
    if (any(node_gap_svg < aa_margin)) stop("exported PPI SVG contains overlapping node disks")
    if (any(node_svg$cx - node_svg$radius - outline_width / 2 < viewbox[1] + aa_margin) ||
        any(node_svg$cx + node_svg$radius + outline_width / 2 > viewbox[1] + svg_width - aa_margin) ||
        any(node_svg$cy - node_svg$radius - outline_width / 2 < viewbox[2] + aa_margin) ||
        any(node_svg$cy + node_svg$radius + outline_width / 2 > viewbox[2] + svg_height - aa_margin)) {
      stop("exported PPI SVG clips a node disk")
    }
    if (outline_width * report_scale < PPI_MIN_REPORT_STROKE_PX) {
      stop("exported PPI node outline is below one pixel at report width")
    }

    edge_width_report <- svg_edges$width * report_scale
    if (min(edge_width_report) < PPI_MIN_REPORT_STROKE_PX) {
      stop("exported PPI confidence edge is below one pixel at report width")
    }
    observed_widths <- sort(unique(round(edge_width_report, 3)))
    if (length(observed_widths) != length(occupied_bands) ||
        (length(observed_widths) > 1 && any(diff(observed_widths) < PPI_MIN_REPORT_STROKE_PX))) {
      stop("exported PPI confidence bands are not visibly distinguishable")
    }
    min_visible_edge_px <- 2
    for (i in seq_len(nrow(svg_edges))) {
      e <- svg_edges[i, ]
      s <- node_svg[node_index_svg[[edge_plot$source[i]]], ]
      t <- node_svg[node_index_svg[[edge_plot$target[i]]], ]
      if (abs(sqrt((e$x - s$cx)^2 + (e$y - s$cy)^2) - s$radius) > 0.15 ||
          abs(sqrt((e$xend - t$cx)^2 + (e$yend - t$cy)^2) - t$radius) > 0.15) {
        stop(sprintf("exported PPI edge %s--%s does not meet its owner-node boundaries",
                     edge_plot$source[i], edge_plot$target[i]))
      }
      visible_px <- sqrt((e$xend - e$x)^2 + (e$yend - e$y)^2) * report_scale
      if (visible_px < min_visible_edge_px) {
        stop(sprintf("exported PPI edge %s--%s has only %.2f visible report pixels",
                     edge_plot$source[i], edge_plot$target[i], visible_px))
      }
      other <- setdiff(node_svg$id, c(edge_plot$source[i], edge_plot$target[i]))
      for (id in other) {
        node <- node_svg[node_index_svg[[id]], ]
        clearance <- point_segment_distance(node$cx, node$cy, e$x, e$y, e$xend, e$yend) -
          node$radius - outline_width / 2 - e$width / 2
        if (clearance < aa_margin) {
          stop(sprintf("exported PPI edge %s--%s contacts non-owner node %s",
                       edge_plot$source[i], edge_plot$target[i], id))
        }
      }
    }
    if (nrow(svg_edges) > 1) {
      edge_pairs_svg <- utils::combn(seq_len(nrow(svg_edges)), 2)
      for (pair_i in seq_len(ncol(edge_pairs_svg))) {
        i <- edge_pairs_svg[1, pair_i]; j <- edge_pairs_svg[2, pair_i]
        if (length(intersect(c(edge_plot$source[i], edge_plot$target[i]),
                             c(edge_plot$source[j], edge_plot$target[j])))) next
        clearance <- segment_distance(svg_edges[i, ], svg_edges[j, ]) -
          (svg_edges$width[i] + svg_edges$width[j]) / 2
        if (clearance < aa_margin) {
          stop(sprintf("exported PPI unrelated edges %d and %d contact", i, j))
        }
      }
    }

    caption_fragments <- c(
      "Static view emphasizes topology",
      "interactive PPI view",
      "results/networks/ppi_hub_genes.csv"
    )
    if (!all(vapply(caption_fragments, function(fragment) {
      any(grepl(fragment, svg_lines, fixed = TRUE))
    }, logical(1)))) {
      stop("exported PPI SVG lacks the static-topology identity disclosure")
    }
    band_present <- vapply(occupied_bands, function(label) {
      any(grepl(label, svg_lines, fixed = TRUE))
    }, logical(1))
    if (!all(band_present) ||
        !any(grepl("STRING combined score", svg_lines, fixed = TRUE))) {
      stop("exported PPI SVG lacks its occupied confidence-band legend")
    }
    TRUE
  }

    ggsave(out[["png"]], p, width = render_w, height = render_h, units = "in",
           dpi = fig_dpi, bg = "white")
    ggsave(out[["svg"]], p, width = render_w, height = render_h, units = "in",
           bg = "white")
    validate_exported_ppi_svg(out[["svg"]])
    TRUE
  }, error = function(e) {
    fig_error <<- conditionMessage(e)
    message("PPI figure failed: ", fig_error)
    FALSE
  })
  check_status <- "PASS"
  check_message <- sprintf("STRING PPI (taxid %d, combined_score >= %d): %d nodes, %d edges, %d modules from %d seed genes.",
                           tax, score_thr, igraph::vcount(g), igraph::ecount(g),
                           length(unique(V(g)$module)), length(seed))
  if (!fig_ok) {
    state$layout_method <- "static topology unavailable"
    state$layout_fallback_reason <- fig_error
    placeholder_fig(if (grepl("dense component", fig_error, fixed = TRUE)) {
      PPI_DENSE_STATIC_PLACEHOLDER
    } else {
      paste(
        "PPI topology could not be established as a crossing-free static view within deterministic limits.",
        "\nUse the interactive PPI view, results/networks/ppi_hub_genes.csv, and the network tables."
      )
    })
    check_status <- "WARNING"
    check_message <- paste(check_message, "Static figure WARNING:", fig_error,
                           "Network tables and interactive exports remain complete.")
  }
  write_check(check_status, check_message)
  write_provenance(check_status, if (fig_ok) NA_character_ else fig_error)
  TRUE
}, error = function(e) { skip(paste("PPI skipped:", conditionMessage(e))); FALSE })

sink(type = "message")
close(log_con)
