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

# Cytoscape-compatible export of the enrichment networks (0.6.0): a GO
# term-similarity (enrichment-map) graph and a gene-concept (term->gene) graph,
# written as GraphML + SIF + cytoscape.js JSON + node/edge CSVs from the persisted
# clusterProfiler objects. Offline, no new heavy dependency. Best-effort: empty
# but valid files when enrichment was skipped or had < 2 terms.

suppressMessages({
  library(jsonlite)
})
# One ORA scope rule shared with make_enrichment_figures.R (separate BH families).
source(file.path(snakemake@scriptdir, "enrichment_scope.R"))

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

out <- snakemake@output
obj <- tryCatch(readRDS(snakemake@input[["objects"]]), error = function(e) list())
have_ig <- requireNamespace("igraph", quietly = TRUE)
have_ep <- requireNamespace("enrichplot", quietly = TRUE)
`%||%` <- function(a, b) if (is.null(a)) b else a

# Shared multi-format writer: GraphML (igraph), SIF (hand-written; write_graph has
# no sif format), cytoscape.js JSON (jsonlite), and node/edge CSVs.
write_network <- function(g, graphml, sif, cyjs, nodes_csv, edges_csv, scope = "none") {
  empty <- is.null(g) || !have_ig || igraph::vcount(g) == 0
  if (empty) {
    # Valid empty GraphML (not a 0-byte file) so degraded runs still import.
    if (have_ig) {
      igraph::write_graph(igraph::make_empty_graph(directed = FALSE), graphml, format = "graphml")
    } else {
      writeLines(paste0('<?xml version="1.0" encoding="UTF-8"?>\n',
                        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
                        '<graph edgedefault="undirected"></graph></graphml>'), graphml)
    }
    writeLines(character(0), sif)
    writeLines('{"elements":{"nodes":[],"edges":[]}}', cyjs)
    # Same header as a populated export, so the node schema is stable across degraded runs.
    write.csv(data.frame(id = character(0), scope = character(0)), nodes_csv, row.names = FALSE)
    write.csv(data.frame(source = character(0), target = character(0)), edges_csv, row.names = FALSE)
    return(invisible())
  }
  igraph::write_graph(g, graphml, format = "graphml")
  el <- igraph::as_edgelist(g)
  writeLines(apply(el, 1, function(r) paste(r[1], "interacts", r[2], sep = "\t")), sif)
  nodes_df <- data.frame(id = igraph::V(g)$name %||% as.character(seq_len(igraph::vcount(g))),
                         scope = scope, stringsAsFactors = FALSE)
  write.csv(nodes_df, nodes_csv, row.names = FALSE)
  edges_df <- data.frame(source = el[, 1], target = el[, 2], stringsAsFactors = FALSE)
  if ("weight" %in% igraph::edge_attr_names(g)) edges_df$weight <- igraph::E(g)$weight
  write.csv(edges_df, edges_csv, row.names = FALSE)
  nodes_j <- lapply(nodes_df$id, function(x) list(data = list(id = x, scope = scope)))
  edges_j <- lapply(seq_len(nrow(edges_df)), function(i) list(data = as.list(edges_df[i, , drop = FALSE])))
  writeLines(toJSON(list(elements = list(nodes = nodes_j, edges = edges_j)), auto_unbox = TRUE), cyjs)
}

# Source object: the ORA scope make_enrichment_figures.R draws, so the export and the
# figure describe the same BH family. Term similarity needs >= 2 terms; when the selected
# ORA has fewer and GSEA does not, fall back to the GSEA result -- a different test, so it
# is exported under its own scope label rather than as the ORA.
selected <- select_enrichment_scope(obj)
pick <- NULL
scope <- "none"
if (selected$selected_n >= 2) {
  pick <- selected$object
  scope <- sprintf("GO BP ORA (%s)", selected$scope)
} else if (!is.null(obj[["gse"]]) &&
           tryCatch(nrow(as.data.frame(obj[["gse"]])) >= 2, error = function(e) FALSE)) {
  pick <- obj[["gse"]]
  scope <- "GO BP GSEA (ranked list)"
}
message(sprintf(paste0("Enrichment network scope: %s; adjusted-significant terms - ",
                       "combined: %d; up: %d; down: %d; selected ORA (%s): %d"),
                scope, selected$combined_n, selected$up_n, selected$down_n,
                selected$scope, selected$selected_n))

emap_g <- NULL
genemap_g <- NULL
if (have_ep && have_ig && !is.null(pick)) {
  ts <- tryCatch(enrichplot::pairwise_termsim(pick), error = function(e) NULL)  # default Jaccard
  if (!is.null(ts)) {
    sim <- ts@termsim
    sim[is.na(sim)] <- 0
    sim <- pmax(sim, t(sim))
    emap_g <- tryCatch(igraph::graph_from_adjacency_matrix(sim, mode = "undirected",
                       weighted = TRUE, diag = FALSE), error = function(e) NULL)
  }
  po <- pick
  if (inherits(po, "gseaResult")) {
    po <- tryCatch(DOSE::setReadable(po, OrgDb = obj$orgdb, keyType = "ENTREZID"), error = function(e) po)
  }
  pdf <- as.data.frame(po)
  gcol <- if ("core_enrichment" %in% names(pdf)) "core_enrichment" else "geneID"
  ed <- do.call(rbind, lapply(seq_len(nrow(pdf)), function(i) {
    genes <- strsplit(as.character(pdf[[gcol]][i]), "/", fixed = TRUE)[[1]]
    if (length(genes) == 0) return(NULL)
    data.frame(term = pdf$Description[i], gene = genes, stringsAsFactors = FALSE)
  }))
  if (!is.null(ed) && nrow(ed) > 0) {
    genemap_g <- tryCatch(igraph::graph_from_data_frame(ed, directed = FALSE), error = function(e) NULL)
  }
}

write_network(emap_g, out[["emap_graphml"]], out[["emap_sif"]], out[["emap_cyjs"]],
              out[["emap_nodes"]], out[["emap_edges"]], scope = scope)
write_network(genemap_g, out[["genemap_graphml"]], out[["genemap_sif"]], out[["genemap_cyjs"]],
              out[["genemap_nodes"]], out[["genemap_edges"]], scope = scope)

sink(type = "message")
close(log_con)
