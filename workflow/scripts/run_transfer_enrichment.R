# Muffle only the benign "package X was built under R version 4.5.3" load warning: the r45 ABI
# is stable, so the 4.5.3-built conda packages run correctly under the pinned r-base 4.5.2;
# real warnings still surface. Shadow library()/require() so it works under Snakemake's
# script runner at any call-stack depth (a top-level globalCallingHandlers does not).
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})

# Annotation-transfer enrichment for organisms without a curated annotation package.
# Term sets come from STRING's per-organism, orthology-transferred annotation
# (Szklarczyk et al. 2023) and, optionally, from eggNOG-mapper or KofamScan/KofamKOALA
# output the user supplies. ORA and GSEA run through clusterProfiler's TERM2GENE
# interface with the tested genes as the universe. Separate from run_enrichment.R, so
# the curated routes and their outputs are unchanged. Best-effort: a failure writes
# empty tables and a REVIEW_REQUIRED check, so the run still completes.

STRING_CATEGORIES <- c(
  "Biological Process (Gene Ontology)" = "GO BP",
  "Molecular Function (Gene Ontology)" = "GO MF",
  "Cellular Component (Gene Ontology)" = "GO CC",
  "Reactome Pathways" = "Reactome",
  "Protein Domains and Features (InterPro)" = "InterPro")
# Same bands as run_enrichment.R's mapping_gate.
MAPPING_WARNING_FRACTION <- 0.80
MAPPING_REVIEW_FRACTION <- 0.50
# Tested genes are normally better annotated than the whole proteome; falling well
# below the proteome's own coverage points at an identifier or file problem.
COVERAGE_RATIO_WARNING <- 0.80
MIN_GS <- 10L
MAX_GS <- 500L
STATUS_PRIORITY <- c(PASS = 1L, WARNING = 2L, REVIEW_REQUIRED = 3L, FAIL = 4L)

status_max <- function(...) {
  values <- unlist(list(...))
  values <- values[values %in% names(STATUS_PRIORITY)]
  if (!length(values)) "PASS" else values[which.max(STATUS_PRIORITY[values])]
}

mapping_status <- function(fraction) {
  if (!is.finite(fraction) || fraction < MAPPING_REVIEW_FRACTION) "REVIEW_REQUIRED"
  else if (fraction < MAPPING_WARNING_FRACTION) "WARNING" else "PASS"
}

# Same normalisation as run_custom_enrichment.R: Ensembl version suffix stripped;
# LOC<GeneID> to the bare GeneID except where the gene ids are symbols.
normalize_ids <- function(id, keytype) {
  id <- as.character(id)
  v <- !is.na(id) & grepl("^ENS", id); id[v] <- sub("\\.\\d+$", "", id[v])
  if (!identical(keytype, "SYMBOL")) {
    l <- !is.na(id) & grepl("^LOC[0-9]+$", id); id[l] <- sub("^LOC", "", id[l])
  }
  id
}

select_rank_values <- function(res) {
  stat <- if ("stat" %in% names(res)) suppressWarnings(as.numeric(res$stat)) else NULL
  if (!is.null(stat) && any(is.finite(stat))) return(list(values = stat, name = "stat"))
  list(values = suppressWarnings(as.numeric(res$log2FoldChange)), name = "log2FoldChange")
}

# Alias -> STRING protein lookup. BLAST-derived aliases are sequence-similarity
# transfers, not identifiers of this organism's genes, so they never decide a mapping.
build_alias_lookup <- function(aliases) {
  aliases <- aliases[!grepl("^BLAST_", aliases$source), c("alias", "protein"), drop = FALSE]
  aliases <- unique(aliases)
  n <- table(aliases$alias)
  list(unique = setNames(aliases$protein, aliases$alias)[aliases$alias %in% names(n)[n == 1L]],
       ambiguous = names(n)[n > 1L])
}

# Each gene takes the first of its keys (normalised gene id, NCBI GeneID, symbol) that
# is a STRING alias; a key naming more than one protein leaves the gene unmapped.
map_genes_to_proteins <- function(res, lookup, keytype) {
  keys <- list(gene_id = normalize_ids(res$gene_id, keytype))
  if ("ncbi_geneid" %in% names(res)) keys$ncbi_geneid <- as.character(res$ncbi_geneid)
  if ("symbol" %in% names(res)) keys$symbol <- as.character(res$symbol)
  protein <- rep(NA_character_, nrow(res)); key_used <- rep(NA_character_, nrow(res))
  ambiguous <- rep(FALSE, nrow(res))
  for (k in names(keys)) {
    open <- is.na(protein) & !ambiguous & !is.na(keys[[k]]) & nzchar(keys[[k]]) & keys[[k]] != "NA"
    amb <- open & keys[[k]] %in% lookup$ambiguous
    hit <- open & !amb & keys[[k]] %in% names(lookup$unique)
    protein[hit] <- unname(lookup$unique[keys[[k]][hit]])
    key_used[hit] <- k
    ambiguous <- ambiguous | amb
  }
  data.frame(gene_id = as.character(res$gene_id), base_id = keys$gene_id, protein = protein,
             key = key_used, ambiguous = ambiguous, stringsAsFactors = FALSE)
}

# Protein-level GSEA rank: median statistic over the genes on one protein, proteins
# whose genes disagree in sign excluded, then the deterministic order used by the
# curated routes (statistic descending, exact ties by id in bytewise order).
build_protein_rank <- function(protein, statistic) {
  keep <- !is.na(protein) & is.finite(statistic)
  protein <- protein[keep]; statistic <- statistic[keep]
  if (!length(protein)) return(list(values = numeric(0), conflicts = 0L, collapsed = 0L))
  groups <- split(statistic, protein)
  conflict <- vapply(groups, function(x) any(x > 0) && any(x < 0), logical(1))
  values <- vapply(groups[!conflict], stats::median, numeric(1))
  ids <- names(values)
  ord <- order(-values, ids, method = "radix")
  list(values = setNames(unname(values[ord]), ids[ord]), conflicts = sum(conflict),
       collapsed = sum(lengths(groups) > 1L))
}

read_gene_table <- function(path) {
  if (!file.exists(path)) return(character(0))
  d <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(d) || !"gene_id" %in% names(d) || !nrow(d)) return(character(0))
  unique(as.character(d$gene_id))
}

string_url <- function(kind, taxon, version) {
  sprintf("https://stringdb-downloads.org/download/%s.v%s/%s.%s.v%s.txt.gz",
          kind, version, taxon, kind, version)
}

# Download once into the project cache; the manifest keeps the original retrieval
# date, size and checksum so a rerun reuses exactly the recorded snapshot.
fetch_string_file <- function(kind, taxon, version, cache_dir) {
  dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
  url <- string_url(kind, taxon, version)
  path <- file.path(cache_dir, basename(url))
  manifest_path <- paste0(path, ".manifest.json")
  if (!file.exists(path) || !file.exists(manifest_path) || file.size(path) == 0) {
    tmp <- paste0(path, ".part")
    old_timeout <- getOption("timeout"); options(timeout = max(600, old_timeout))
    on.exit(options(timeout = old_timeout), add = TRUE)
    status <- utils::download.file(url, tmp, mode = "wb", quiet = TRUE)
    if (status != 0 || !file.exists(tmp) || file.size(tmp) == 0) stop(sprintf("download failed: %s", url))
    file.rename(tmp, path)
    jsonlite::write_json(list(url = url, bytes = file.size(path),
                              md5 = unname(tools::md5sum(path)),
                              retrieved_utc = format(Sys.time(), tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ")),
                         manifest_path, auto_unbox = TRUE, pretty = TRUE)
  }
  manifest <- jsonlite::read_json(manifest_path)
  if (!identical(unname(tools::md5sum(path)), manifest$md5)) stop(sprintf("cached file changed since download: %s", path))
  c(list(path = path), manifest)
}

read_string_table <- function(path, columns) {
  d <- utils::read.delim(gzfile(path), header = FALSE, skip = 1L, quote = "", comment.char = "",
                         colClasses = "character", col.names = columns)
  d
}

# eggNOG-mapper .emapper.annotations: '##' comment lines, one '#query' header, "-" for none.
read_emapper <- function(path) {
  lines <- readLines(path, warn = FALSE)
  header <- grep("^#query", lines)
  if (length(header) != 1L) stop("not an eggNOG-mapper annotations file (no single #query header)")
  body <- lines[seq.int(header + 1L, length(lines))]
  body <- body[nzchar(body) & !startsWith(body, "#")]
  cols <- strsplit(sub("^#", "", lines[header]), "\t", fixed = TRUE)[[1]]
  d <- utils::read.delim(text = body, header = FALSE, quote = "", comment.char = "",
                         colClasses = "character", col.names = cols)
  explode <- function(column, prefix = "") {
    if (!column %in% names(d)) return(data.frame(query = character(0), value = character(0)))
    parts <- strsplit(ifelse(d[[column]] == "-", "", d[[column]]), ",", fixed = TRUE)
    out <- data.frame(query = rep(d$query, lengths(parts)), value = unlist(parts), stringsAsFactors = FALSE)
    out$value <- sub(prefix, "", trimws(out$value))
    out[nzchar(out$value), , drop = FALSE]
  }
  list(go = explode("GOs"), ko = explode("KEGG_ko", "^ko:"), queries = unique(d$query))
}

# KofamScan/KofamKOALA: the detail format marks threshold-passing hits with '*';
# the mapper format is "gene<TAB>KO[<TAB>KO...]".
read_ko_table <- function(path) {
  lines <- readLines(path, warn = FALSE)
  lines <- lines[nzchar(trimws(lines))]
  if (any(startsWith(lines, "*"))) {
    hits <- strsplit(trimws(sub("^\\*", "", lines[startsWith(lines, "*")])), "\\s+")
    d <- data.frame(query = vapply(hits, `[`, "", 1L), value = vapply(hits, `[`, "", 2L),
                    stringsAsFactors = FALSE)
    queries <- unique(c(d$query, vapply(strsplit(trimws(sub("^\\*", "", lines[!startsWith(lines, "#")])), "\\s+"), `[`, "", 1L)))
  } else {
    parts <- strsplit(lines[!startsWith(lines, "#")], "\t", fixed = TRUE)
    queries <- vapply(parts, `[`, "", 1L)
    d <- data.frame(query = rep(queries, pmax(lengths(parts) - 1L, 0L)),
                    value = unlist(lapply(parts, `[`, -1L)), stringsAsFactors = FALSE)
  }
  d <- d[grepl("^K[0-9]{5}$", d$value), , drop = FALSE]
  list(ko = unique(d), queries = unique(queries))
}

# Imported ids may be gene ids or protein ids; protein ids are bridged to genes through
# the project annotation (GTF/GFF protein_id on CDS records).
protein_gene_bridge <- function(annotation) {
  if (!nzchar(annotation) || !file.exists(annotation)) return(NULL)
  con <- if (grepl("\\.gz$", annotation)) gzfile(annotation) else file(annotation)
  lines <- readLines(con, warn = FALSE); close(con)
  lines <- lines[grepl("\tCDS\t", lines, fixed = TRUE) & grepl("protein_id", lines, fixed = TRUE)]
  if (!length(lines)) return(NULL)
  attr <- sub("^([^\t]*\t){8}", "", lines)
  grab <- function(key) {
    gtf <- regmatches(attr, regexpr(sprintf('%s "[^"]+"', key), attr))
    gff <- regmatches(attr, regexpr(sprintf("%s=[^;]+", key), attr))
    value <- rep(NA_character_, length(attr))
    has_gtf <- grepl(sprintf('%s "', key), attr)
    value[has_gtf] <- sub(sprintf('^%s "([^"]+)"$', key), "\\1", gtf)
    has_gff <- !has_gtf & grepl(sprintf("%s=", key), attr)
    value[has_gff] <- sub(sprintf("^%s=", key), "", gff)
    value
  }
  gene <- grab("gene_id")
  if (all(is.na(gene))) gene <- grab("locus_tag")
  bridge <- unique(data.frame(protein = grab("protein_id"), gene = gene, stringsAsFactors = FALSE))
  bridge[!is.na(bridge$protein) & !is.na(bridge$gene), , drop = FALSE]
}

# Import queries to tested gene ids: direct match first, else through the protein bridge
# (with and without the protein version suffix). One query may name several genes only
# through distinct proteins; a query naming two genes is dropped as ambiguous.
resolve_import_queries <- function(queries, gene_ids, bridge) {
  direct <- queries[queries %in% gene_ids]
  out <- data.frame(query = direct, gene_id = direct, stringsAsFactors = FALSE)
  rest <- setdiff(queries, direct)
  if (length(rest) && !is.null(bridge) && nrow(bridge)) {
    b <- rbind(bridge, transform(bridge, protein = sub("\\.[0-9]+$", "", protein)))
    b <- unique(b[b$gene %in% gene_ids, , drop = FALSE])
    hit <- b[b$protein %in% rest, , drop = FALSE]
    n <- table(hit$protein)
    hit <- hit[hit$protein %in% names(n)[n == 1L], , drop = FALSE]
    out <- rbind(out, data.frame(query = hit$protein, gene_id = hit$gene, stringsAsFactors = FALSE))
  }
  unique(out)
}

propagate_go <- function(pairs) {
  suppressMessages(library(GO.db))
  ont <- suppressMessages(AnnotationDbi::Ontology(unique(pairs$term)))
  known <- names(ont)[!is.na(ont)]
  pairs <- pairs[pairs$term %in% known, , drop = FALSE]
  out <- list()
  for (o in c("BP", "MF", "CC")) {
    terms <- known[ont[known] == o]
    if (!length(terms)) next
    anc <- AnnotationDbi::as.list(switch(o, BP = GO.db::GOBPANCESTOR, MF = GO.db::GOMFANCESTOR,
                                         CC = GO.db::GOCCANCESTOR)[terms])
    sub <- pairs[pairs$term %in% terms, , drop = FALSE]
    expanded <- lapply(seq_len(nrow(sub)), function(i)
      c(sub$term[i], setdiff(anc[[sub$term[i]]], "all")))
    full <- unique(data.frame(term = unlist(expanded), gene = rep(sub$gene, lengths(expanded)),
                              stringsAsFactors = FALSE))
    names_ <- suppressMessages(AnnotationDbi::Term(unique(full$term)))
    out[[paste("GO", o)]] <- list(t2g = full, t2n = data.frame(term = names(names_), name = unname(names_),
                                                               stringsAsFactors = FALSE))
  }
  list(sets = out, unknown_terms = length(setdiff(unique(pairs$term), known)))
}

# KO to KEGG reference pathways ("map" ids) through the live KEGG REST API, the only
# access KEGG's licence allows; nothing KEGG-derived is bundled.
ko_to_pathway_sets <- function(ko_pairs) {
  link <- KEGGREST::keggLink("pathway", "ko")
  link <- data.frame(ko = sub("^ko:", "", names(link)), path = sub("^path:", "", unname(link)),
                     stringsAsFactors = FALSE)
  link <- link[grepl("^map", link$path), , drop = FALSE]
  names_ <- KEGGREST::keggList("pathway")
  t2g <- merge(ko_pairs, link, by = "ko")
  t2g <- unique(data.frame(term = t2g$path, gene = t2g$gene, stringsAsFactors = FALSE))
  map_names <- sub("^path:", "", names(names_))
  list(t2g = t2g, t2n = data.frame(term = map_names, name = unname(names_), stringsAsFactors = FALSE),
       retrieved = format(Sys.time(), tz = "UTC", format = "%Y-%m-%d"),
       kegg_links = nrow(link))
}

# enricher restricts each set to the universe and GSEA to the ranked ids before the
# size limits apply, so the sets are passed whole.
run_category <- function(label, t2g, t2n, universe, foregrounds, ranked, alpha, id_to_genes, rank_info) {
  t2g <- unique(t2g[t2g$gene %in% union(universe, names(ranked)), c("term", "gene"), drop = FALSE])
  ora <- list(); gsea <- NULL
  back <- function(ids) vapply(strsplit(ids, "/", fixed = TRUE), function(x)
    paste(unique(unlist(id_to_genes[x])), collapse = "/"), "")
  for (fg in names(foregrounds)) {
    genes <- intersect(foregrounds[[fg]], universe)
    if (!length(genes) || !nrow(t2g)) next
    e <- tryCatch(clusterProfiler::enricher(
      gene = genes, universe = universe, TERM2GENE = t2g, TERM2NAME = t2n, pvalueCutoff = alpha,
      pAdjustMethod = "BH", qvalueCutoff = 0.20, minGSSize = MIN_GS, maxGSSize = MAX_GS),
      error = function(e) { message(label, " ", fg, " ORA failed: ", conditionMessage(e)); NULL })
    d <- if (is.null(e)) data.frame() else as.data.frame(e)
    if (nrow(d)) {
      d$geneID <- back(d$geneID)
      ora[[fg]] <- cbind(category = label, foreground = fg, d, stringsAsFactors = FALSE)
    }
  }
  if (length(ranked) && nrow(t2g)) {
    set.seed(42)
    g <- tryCatch(withCallingHandlers(clusterProfiler::GSEA(
      geneList = ranked, TERM2GENE = t2g, TERM2NAME = t2n, pvalueCutoff = alpha,
      pAdjustMethod = "BH", minGSSize = MIN_GS, maxGSSize = MAX_GS, eps = 0, seed = TRUE,
      verbose = FALSE), warning = function(w) {
        if (grepl("ties in the preranked stats", conditionMessage(w), fixed = TRUE)) {
          message(label, " GSEA: ", rank_info, "; fgsea tie notice replaced by this policy")
          invokeRestart("muffleWarning")
        }
      }), error = function(e) { message(label, " GSEA failed: ", conditionMessage(e)); NULL })
    d <- if (is.null(g)) data.frame() else as.data.frame(g)
    if (nrow(d)) {
      d$core_enrichment <- back(d$core_enrichment)
      gsea <- cbind(category = label, d, stringsAsFactors = FALSE)
    }
  }
  in_universe <- t2g[t2g$gene %in% universe, , drop = FALSE]
  sizes <- table(in_universe$term)
  list(ora = if (length(ora)) do.call(rbind, ora) else NULL, gsea = gsea,
       annotated_universe = length(unique(in_universe$gene)),
       sets = sum(sizes >= MIN_GS & sizes <= MAX_GS))
}

write_check <- function(path, status, messages) {
  jsonlite::write_json(list(check = "25_transfer_enrichment_qc", status = status,
                            messages = lapply(messages, function(m) list(status = m[[1]], message = m[[2]]))),
                       path, auto_unbox = TRUE, pretty = TRUE)
}

main <- function() {
  p <- snakemake@params
  out <- snakemake@output
  keytype <- as.character(p[["keytype"]] %||% "")
  taxon <- as.character(p[["taxon"]] %||% "")
  version <- as.character(p[["string_version"]] %||% "12.0")
  alpha <- as.numeric(p[["alpha"]] %||% 0.05)
  cache_dir <- as.character(p[["cache_dir"]])
  emapper <- as.character(p[["emapper"]] %||% "")
  ko_table <- as.character(p[["ko_table"]] %||% "")
  annotation <- as.character(p[["annotation"]] %||% "")

  empty <- data.frame()
  write.csv(empty, out[["ora"]], row.names = FALSE); write.csv(empty, out[["gsea"]], row.names = FALSE)
  write.csv(empty, out[["id_map"]], row.names = FALSE)
  saveRDS(list(), out[["objects"]])
  summary <- c("Annotation-transfer enrichment summary", "======================================", "")
  checks <- list()
  provenance <- list(schema_version = 1L, string = NULL, imports = list())

  res <- read.csv(snakemake@input[["results"]], stringsAsFactors = FALSE)
  rank_stat <- select_rank_values(res)
  populations <- prepare_enrichment_populations(
    res, rank_stat$values, !is.na(res$padj), !is.na(res$padj) & is.finite(rank_stat$values))
  tested_rows <- which(populations$ora_mask)
  rank_rows <- which(populations$rank_mask)
  up <- read_gene_table(snakemake@input[["up"]])
  down <- read_gene_table(snakemake@input[["down"]])
  ora_all <- list(); gsea_all <- list(); objects <- list(); coverage <- list()

  if (nzchar(taxon)) {
    ok <- tryCatch({
      aliases_file <- fetch_string_file("protein.aliases", taxon, version, cache_dir)
      terms_file <- fetch_string_file("protein.enrichment.terms", taxon, version, cache_dir)
      aliases <- read_string_table(aliases_file$path, c("protein", "alias", "source"))
      terms <- read_string_table(terms_file$path, c("protein", "category", "term", "description"))
      if (!all(startsWith(aliases$protein[seq_len(min(50, nrow(aliases)))], paste0(taxon, "."))))
        stop(sprintf("STRING alias file does not belong to taxon %s", taxon))
      lookup <- build_alias_lookup(aliases)
      map <- map_genes_to_proteins(res, lookup, keytype)
      write.csv(map[!is.na(map$protein) | map$ambiguous, ], out[["id_map"]], row.names = FALSE)
      tested <- map[tested_rows, , drop = FALSE]
      mapped_fraction <- mean(!is.na(tested$protein))
      universe <- unique(tested$protein[!is.na(tested$protein)])
      up_p <- unique(map$protein[map$gene_id %in% up & !is.na(map$protein)])
      down_p <- unique(map$protein[map$gene_id %in% down & !is.na(map$protein)])
      conflicts <- intersect(up_p, down_p)
      foregrounds <- list(up = setdiff(up_p, conflicts), down = setdiff(down_p, conflicts),
                          combined = union(up_p, down_p))
      rank <- build_protein_rank(map$protein[rank_rows], rank_stat$values[rank_rows])
      id_to_genes <- split(map$gene_id[!is.na(map$protein)], map$protein[!is.na(map$protein)])
      proteome <- unique(aliases$protein)
      rank_policy <- sprintf("ranked on %s; genes sharing a STRING protein collapsed by median; exact ties by protein id", rank_stat$name)
      map_status <- mapping_status(mapped_fraction)
      checks[[length(checks) + 1L]] <- list(map_status, sprintf(
        "STRING v%s taxon %s: %d of %d tested genes (%.1f%%) mapped to a STRING protein; %d ambiguous gene ids excluded; %d proteins with discordant genes excluded from directional sets.",
        version, taxon, sum(!is.na(tested$protein)), nrow(tested), 100 * mapped_fraction,
        sum(tested$ambiguous), length(conflicts)))
      summary <- c(summary, sprintf("STRING version %s, taxon %s (orthology-transferred annotation; Szklarczyk et al. 2023, CC BY 4.0).", version, taxon),
                   sprintf("Files: %s (retrieved %s), %s (retrieved %s).", basename(aliases_file$path), aliases_file$retrieved_utc,
                           basename(terms_file$path), terms_file$retrieved_utc),
                   sprintf("Tested genes mapped: %d/%d (%.1f%%); keys used: %s.", sum(!is.na(tested$protein)), nrow(tested),
                           100 * mapped_fraction, paste(names(table(tested$key)), table(tested$key), sep = "=", collapse = ", ")),
                   sprintf("Universe: %d STRING proteins from the tested genes. Foregrounds: up %d, down %d, combined %d; %d proteins carried genes in both directions.",
                           length(universe), length(foregrounds$up), length(foregrounds$down), length(foregrounds$combined), length(conflicts)),
                   sprintf("GSEA: %d ranked proteins (%s); %d proteins with sign-discordant genes excluded.", length(rank$values), rank_policy, rank$conflicts),
                   "GO sets are used as STRING propagated them under its own GO release; they are not re-propagated.")
      for (cat_name in names(STRING_CATEGORIES)) {
        label <- STRING_CATEGORIES[[cat_name]]
        rows <- terms[terms$category == cat_name, , drop = FALSE]
        t2g <- data.frame(term = rows$term, gene = rows$protein, stringsAsFactors = FALSE)
        t2n <- unique(data.frame(term = rows$term, name = rows$description, stringsAsFactors = FALSE))
        proteome_cov <- length(unique(rows$protein)) / length(proteome)
        tested_cov <- if (length(universe)) mean(universe %in% rows$protein) else 0
        ratio <- if (proteome_cov > 0) tested_cov / proteome_cov else NA
        coverage[[label]] <- list(proteome = proteome_cov, tested = tested_cov, ratio = ratio)
        r <- run_category(label, t2g, t2n, universe, foregrounds, rank$values, alpha, id_to_genes, rank_policy)
        objects[[label]] <- r
        if (!is.null(r$ora)) ora_all[[label]] <- r$ora
        if (!is.null(r$gsea)) gsea_all[[label]] <- r$gsea
        n_ora <- if (is.null(r$ora)) 0L else nrow(r$ora[r$ora$foreground == "combined", , drop = FALSE])
        summary <- c(summary, sprintf("%s: %d sets of %d-%d genes; annotated tested proteins %.1f%% (proteome %.1f%%); ORA combined %d, GSEA %d adjusted terms.",
                                      label, r$sets, MIN_GS, MAX_GS, 100 * tested_cov, 100 * proteome_cov, n_ora,
                                      if (is.null(r$gsea)) 0L else nrow(r$gsea)))
      }
      bp <- coverage[["GO BP"]]
      if (!is.null(bp) && is.finite(bp$ratio) && bp$ratio < COVERAGE_RATIO_WARNING) {
        checks[[length(checks) + 1L]] <- list("WARNING", sprintf(
          "GO BP covers %.1f%% of tested proteins against %.1f%% of the STRING proteome; tested genes are usually better annotated, so check the gene identifiers.",
          100 * bp$tested, 100 * bp$proteome))
      }
      provenance$string <- list(version = version, taxon = taxon,
                                files = list(aliases_file[c("url", "bytes", "md5", "retrieved_utc")],
                                             terms_file[c("url", "bytes", "md5", "retrieved_utc")]),
                                categories = unname(STRING_CATEGORIES),
                                excluded_categories = c("Local Network Cluster (STRING)", "Subcellular localization (COMPARTMENTS)",
                                                        "Protein Domains (SMART)", "Protein Domains (Pfam)", "Annotated Keywords (UniProt)"),
                                tested_genes = nrow(tested), mapped_genes = sum(!is.na(tested$protein)),
                                ambiguous_genes = sum(tested$ambiguous), universe_proteins = length(universe),
                                direction_conflicts = length(conflicts), gsea_rank_statistic = rank_stat$name,
                                coverage = coverage)
      TRUE
    }, error = function(e) {
      checks[[length(checks) + 1L]] <<- list("REVIEW_REQUIRED", paste("STRING annotation route could not run:", conditionMessage(e)))
      summary <<- c(summary, paste("STRING annotation route failed:", conditionMessage(e)))
      FALSE
    })
  }

  imports <- list(emapper = emapper, ko_table = ko_table)
  imports <- imports[nzchar(unlist(imports))]
  if (length(imports)) {
    gene_ids <- unique(res$gene_id[tested_rows])
    bridge <- tryCatch(protein_gene_bridge(annotation), error = function(e) NULL)
    for (kind in names(imports)) {
      tryCatch({
        parsed <- if (identical(kind, "emapper")) read_emapper(imports[[kind]]) else read_ko_table(imports[[kind]])
        resolved <- resolve_import_queries(parsed$queries, gene_ids, bridge)
        fraction <- length(unique(resolved$gene_id)) / length(gene_ids)
        sets <- list()
        if (!is.null(parsed$go) && nrow(parsed$go)) {
          pairs <- merge(parsed$go, resolved, by = "query")
          prop <- propagate_go(data.frame(term = pairs$value, gene = pairs$gene_id, stringsAsFactors = FALSE))
          for (o in names(prop$sets)) sets[[paste("eggNOG", o)]] <- prop$sets[[o]]
        }
        if (!is.null(parsed$ko) && nrow(parsed$ko)) {
          pairs <- merge(parsed$ko, resolved, by = "query")
          kp <- ko_to_pathway_sets(data.frame(ko = pairs$value, gene = pairs$gene_id, stringsAsFactors = FALSE))
          sets[["KEGG pathway via KO"]] <- kp
        }
        universe <- unique(resolved$gene_id)
        foregrounds <- list(up = intersect(up, universe), down = intersect(down, universe),
                            combined = intersect(union(up, down), universe))
        rank <- rank_stat$values[rank_rows]; names(rank) <- res$gene_id[rank_rows]
        rank <- rank[is.finite(rank) & names(rank) %in% universe]
        rank <- rank[order(-rank, names(rank), method = "radix")]
        id_to_genes <- setNames(as.list(universe), universe)
        for (label in names(sets)) {
          r <- run_category(label, sets[[label]]$t2g, sets[[label]]$t2n, universe, foregrounds, rank,
                            alpha, id_to_genes, sprintf("ranked on %s; exact ties by gene id", rank_stat$name))
          objects[[label]] <- r
          if (!is.null(r$ora)) ora_all[[label]] <- r$ora
          if (!is.null(r$gsea)) gsea_all[[label]] <- r$gsea
          summary <- c(summary, sprintf("%s (%s): %d sets; ORA combined %d, GSEA %d adjusted terms.", label, basename(imports[[kind]]),
                                        r$sets, if (is.null(r$ora)) 0L else sum(r$ora$foreground == "combined"),
                                        if (is.null(r$gsea)) 0L else nrow(r$gsea)))
        }
        checks[[length(checks) + 1L]] <- list(mapping_status(fraction), sprintf(
          "%s import %s: %d of %d tested genes (%.1f%%) carry an imported annotation.",
          kind, basename(imports[[kind]]), length(universe), length(gene_ids), 100 * fraction))
        provenance$imports[[kind]] <- list(file = basename(imports[[kind]]), md5 = unname(tools::md5sum(imports[[kind]])),
                                           queries = length(parsed$queries), genes = length(universe),
                                           kegg_rest_retrieved = if (!is.null(sets[["KEGG pathway via KO"]])) sets[["KEGG pathway via KO"]]$retrieved else NULL)
      }, error = function(e) {
        checks[[length(checks) + 1L]] <<- list("REVIEW_REQUIRED", sprintf("%s import could not be used: %s", kind, conditionMessage(e)))
      })
    }
  }

  if (!length(checks)) checks <- list(list("PASS", "Annotation-transfer enrichment not applicable: no STRING taxon or annotation import configured."))
  ora <- if (length(ora_all)) do.call(rbind, unname(ora_all)) else data.frame()
  gsea <- if (length(gsea_all)) do.call(rbind, unname(gsea_all)) else data.frame()
  write.csv(ora, out[["ora"]], row.names = FALSE)
  write.csv(gsea, out[["gsea"]], row.names = FALSE)
  saveRDS(list(categories = objects, ora = ora, gsea = gsea), out[["objects"]])
  status <- status_max(vapply(checks, `[[`, "", 1L))
  summary <- c(summary, "", sprintf("Check 25 status: %s", status), vapply(checks, function(m) paste0("  - ", m[[1]], ": ", m[[2]]), ""))
  writeLines(summary, out[["summary"]])
  jsonlite::write_json(provenance, out[["provenance"]], auto_unbox = TRUE, pretty = TRUE, null = "null", na = "null")
  write_check(out[["check"]], status, checks)
}

`%||%` <- function(a, b) if (is.null(a) || !length(a) || (length(a) == 1L && is.na(a))) b else a

if (exists("snakemake")) {
  source(file.path(snakemake@scriptdir, "enrichment_eligibility.R"))
  log_con <- file(snakemake@log[[1]], open = "wt")
  sink(log_con, type = "message")
  suppressMessages(library(clusterProfiler))
  main()
  sink(type = "message"); close(log_con)
}
