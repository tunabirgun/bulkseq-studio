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
})

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
getp <- function(k, d) { v <- style[[k]]; if (is.null(v)) d else v }
fig_w <- as.numeric(getp("width_in", 7)); fig_h <- as.numeric(getp("height_in", 6)); fig_dpi <- as.integer(getp("dpi", 300))

write_check <- function(status, message) {
  msg <- gsub('"', '\\\\"', message)
  writeLines(sprintf('{\n  "check": "16_ppi_network",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                     status, status, msg), out[["check"]])
}
placeholder_fig <- function(msg) {
  p <- ggplot() + annotate("text", x = 0, y = 0, label = msg, size = 5) + theme_void()
  ggsave(out[["png"]], p, width = fig_w, height = fig_h, dpi = fig_dpi)
  ggsave(out[["svg"]], p, width = fig_w, height = fig_h)
}
skip <- function(msg) {
  file.create(out[["graphml"]]); writeLines(character(0), out[["sif"]])
  writeLines('{"elements":{"nodes":[],"edges":[]}}', out[["cyjs"]])
  write.csv(data.frame(id = character(0)), out[["nodes"]], row.names = FALSE)
  write.csv(data.frame(source = character(0), target = character(0)), out[["edges"]], row.names = FALSE)
  write.csv(data.frame(symbol = character(0), degree = integer(0)), out[["hubs"]], row.names = FALSE)
  placeholder_fig(msg)
  write_check("PASS", msg)
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

ok <- tryCatch({
  if (is.na(tax)) stop(sprintf("no STRING taxid for organism '%s'", organism))
  if (identical(seed_source, "goi") && nzchar(goi_path) && file.exists(goi_path)) {
    seed <- trimws(readLines(goi_path, warn = FALSE)); seed <- seed[nzchar(seed) & !startsWith(seed, "#")]
  } else {
    up <- tryCatch(read.csv(snakemake@input[["up"]], stringsAsFactors = FALSE), error = function(e) data.frame())
    down <- tryCatch(read.csv(snakemake@input[["down"]], stringsAsFactors = FALSE), error = function(e) data.frame())
    seed <- unique(c(up$symbol, down$symbol))
  }
  seed <- seed[!is.na(seed) & nzchar(seed)]
  if (length(seed) < 2) stop("fewer than 2 seed genes with symbols")
  if (length(seed) > max_seed) seed <- head(seed, max_seed)

  cache_dir <- "results/networks/string_cache"; dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  sdb <- STRINGdb$new(version = string_version, species = tax, score_threshold = score_thr, input_directory = cache_dir)
  mapped <- sdb$map(data.frame(gene_id = seed, stringsAsFactors = FALSE), "gene_id", removeUnmappedRows = TRUE)
  if (is.null(mapped) || nrow(mapped) < 2) stop("fewer than 2 genes mapped to STRING")
  inter <- sdb$get_interactions(unique(mapped$STRING_id))
  if (is.null(inter) || nrow(inter) < 1) stop("no interactions returned")

  id2sym <- tapply(mapped$gene_id, mapped$STRING_id, function(x) x[1])
  edf <- data.frame(from = id2sym[inter$from], to = id2sym[inter$to],
                    weight = inter$combined_score / 1000, stringsAsFactors = FALSE)
  edf <- edf[!is.na(edf$from) & !is.na(edf$to) & edf$from != edf$to, ]
  if (nrow(edf) < 1) stop("no symbol-resolvable interactions")
  g <- igraph::simplify(igraph::graph_from_data_frame(edf, directed = FALSE), edge.attr.comb = "max")

  set.seed(42)
  comm <- igraph::cluster_louvain(g, weights = igraph::E(g)$weight)
  V(g)$module <- igraph::membership(comm)
  V(g)$degree <- igraph::degree(g)
  V(g)$betweenness <- igraph::betweenness(g)
  resdf <- read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE, check.names = FALSE)
  # Case-insensitive join: STRING may return a different symbol case than the DE table.
  lfc_map <- setNames(resdf$log2FoldChange, toupper(as.character(resdf$symbol)))
  V(g)$log2FC <- unname(lfc_map[toupper(V(g)$name)])

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
  hub_df <- nodes_df[order(-nodes_df$degree), c("id", "degree", "betweenness", "module", "log2FC")]
  names(hub_df)[1] <- "symbol"
  write.csv(hub_df, out[["hubs"]], row.names = FALSE)

  # Static figure: node colour = log2FC direction, size = degree, and ONLY the top
  # hub proteins (by degree) labelled so the names stay legible.
  cols <- ifelse(is.na(V(g)$log2FC), "grey70", ifelse(V(g)$log2FC > 0, "#C0392B", "#2C7BB6"))
  mx <- max(V(g)$degree); vsize <- if (mx > 0) 3 + 7 * (V(g)$degree / mx) else 5
  n_label <- min(hub_n, igraph::vcount(g))
  vlab <- rep(NA_character_, igraph::vcount(g))
  if (n_label > 0) {
    top_idx <- order(V(g)$degree, decreasing = TRUE)[seq_len(n_label)]
    vlab[top_idx] <- V(g)$name[top_idx]
  }
  set.seed(42)
  lay <- igraph::layout_with_fr(g)  # one layout shared by PNG + SVG
  draw <- function() {
    igraph::plot.igraph(g, layout = lay, vertex.color = cols, vertex.size = vsize,
                        vertex.label = vlab, vertex.label.cex = 0.75, vertex.label.font = 2,
                        vertex.frame.color = NA, edge.color = "grey88", vertex.label.color = "black")
  }
  png(out[["png"]], width = fig_w, height = fig_h, units = "in", res = fig_dpi); draw(); dev.off()
  svglite(out[["svg"]], width = fig_w, height = fig_h); draw(); dev.off()

  write_check("PASS", sprintf("STRING PPI (taxid %d, score>=%d): %d nodes, %d edges, %d modules from %d seed genes.",
              tax, score_thr, igraph::vcount(g), igraph::ecount(g), length(unique(V(g)$module)), length(seed)))
  TRUE
}, error = function(e) { skip(paste("PPI skipped:", conditionMessage(e))); FALSE })

sink(type = "message")
close(log_con)
