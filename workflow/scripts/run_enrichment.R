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

# Functional enrichment (protocol section 8): GO + KEGG ORA and GSEA via
# clusterProfiler. GO / disease-ontology need a Bioconductor OrgDb (human, mouse,
# fly, worm, zebrafish, yeast, Arabidopsis). KEGG runs for any organism with a
# KEGG organism code (e.g. fungi such as Fusarium graminearum, code "fgr"),
# mapping the gene ids directly, so enrichment still works where no OrgDb exists.
# Best-effort: any failure degrades to empty outputs + a REVIEW_REQUIRED check so
# the pipeline still completes.

log_con <- file(snakemake@log[[1]], open = "wt")
sink(log_con, type = "message")

results_file <- snakemake@input[["results"]]
up_file <- snakemake@input[["up"]]
down_file <- snakemake@input[["down"]]
orgdb_name <- snakemake@params[["orgdb"]]
keytype <- snakemake@params[["keytype"]]
kegg_org <- snakemake@params[["kegg"]]
backend <- snakemake@params[["backend"]]
gprofiler_org <- snakemake@params[["gprofiler_organism"]]
alpha <- as.numeric(snakemake@params[["alpha"]])
out <- snakemake@output

write_check <- function(path, status, message) {
  msg <- gsub('"', '\\\\"', message)
  json <- sprintf('{\n  "check": "10_enrichment_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                  status, status, msg)
  writeLines(json, path)
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)

# Strip a trailing version suffix ONLY from Ensembl-style ids (ENSG00000123.4 ->
# ENSG00000123). A naive sub("\\..*$","",id) corrupts PomBase ids whose ordinal is
# a structural dot (SPOM_SPAC212.11 -> SPOM_SPAC212), so the strip is gated on shape:
# locus tags (FGSG_*, ANIA_*, SPOM_*), TAIR (AT#G#####) and ORF ids pass through.
strip_version <- function(id) {
  v <- grepl("^ENS", id)
  id[v] <- sub("\\.\\d+$", "", id[v])
  # NCBI RefSeq crop/plant gene ids are LOC<GeneID> (e.g. rice LOC4326813); KEGG keys
  # on the bare NCBI GeneID (osa:4326813), so strip the LOC prefix. Shape-gated to
  # LOC + digits only, so MSU/TIGR locus tags (LOC_Os01g01010, underscore) and any
  # other id pass through unchanged.
  # SYMBOL-keyed runs (microarray) can carry legitimate gene symbols like "LOC101927877";
  # only strip the LOC prefix on NCBI-GeneID / KEGG key routes, never for SYMBOL.
  if (!identical(keytype, "SYMBOL")) {
    l <- grepl("^LOC[0-9]+$", id)
    id[l] <- sub("^LOC", "", id[l])
  }
  id
}

# Always create the output files first so the rule succeeds even on failure.
for (k in c("go", "go_up", "go_down", "gsea", "kegg", "kegg_gsea")) writeLines("", out[[k]])
# id bridge (gene_id, base_id, symbol, entrez) so the app can resolve an enrichment term's
# genes (entrez on the KEGG-OrgDb / GSEA routes) back to symbols/ids without re-deriving them.
# Written with a header up front; the OrgDb branch fills it in, other branches leave entrez blank.
write.csv(data.frame(gene_id = character(0), base_id = character(0),
                     symbol = character(0), entrez = character(0)),
          out[["id_map"]], row.names = FALSE)
write_id_map <- function(res) {
  tryCatch(write.csv(data.frame(
      gene_id = res$gene_id,
      base_id = if (!is.null(res$base_id)) res$base_id else res$gene_id,
      symbol  = if (!is.null(res$symbol)) res$symbol else NA_character_,
      entrez  = if (!is.null(res$ENTREZID)) res$ENTREZID else ""),
    out[["id_map"]], row.names = FALSE), error = function(e) NULL)
}
# Persist an (empty) objects RDS up front so the enrichment_figures rule always
# has an input, even when enrichment is skipped or fails. Overwritten on success.
saveRDS(list(), out[["objects"]])
summary_lines <- c("Functional enrichment summary", "=============================", "")

has_orgdb     <- !is.null(orgdb_name)    && nzchar(orgdb_name)
has_kegg      <- !is.null(kegg_org)      && nzchar(kegg_org)
has_gprofiler <- !is.null(gprofiler_org) && nzchar(gprofiler_org)

# No usable enrichment route (no OrgDb, no KEGG code, no g:Profiler organism):
# skip cleanly rather than risk running against the wrong species' database.
if (!has_orgdb && !has_kegg && !has_gprofiler) {
  writeLines(c(summary_lines,
               "Skipped: no Bioconductor OrgDb, no KEGG code and no g:Profiler organism mapped.",
               "GO supports human, mouse, fly, worm, zebrafish, yeast, Arabidopsis;",
               "KEGG needs a KEGG organism code (set enrichment.kegg_organism, e.g. 'fgr');",
               "g:Profiler needs enrichment.gprofiler_organism (e.g. 'anidulans')."),
             out[["summary"]])
  write_check(out[["check"]], "PASS",
              "Enrichment skipped: organism not mapped (gene-level DE is unaffected).")
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}

# Read a deseq2 up/down CSV and return its gene_id column (Ensembl version stripped).
read_ids_csv <- function(path) {
  if (!file.exists(path)) return(character(0))
  df <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(df) || !"gene_id" %in% names(df) || nrow(df) == 0) return(character(0))
  unique(strip_version(df$gene_id))
}

# KEGG ORA (combined significant set) + GSEA (ranked list). `kegg_keytype` is
# "ncbi-geneid" when ENTREZ ids are supplied (OrgDb organisms) or "kegg" when the
# raw gene ids are KEGG gene ids (e.g. FGSG locus tags for Fusarium). Each call is
# wrapped so a flaky rest.kegg.jp lookup degrades to empty instead of failing.
run_kegg <- function(genes_all, ranked, kegg_keytype) {
  genes_all <- unique(genes_all[!is.na(genes_all)])
  ek <- if (length(genes_all) >= 1) tryCatch(
    enrichKEGG(gene = genes_all, organism = kegg_org, keyType = kegg_keytype,
               pAdjustMethod = "BH", pvalueCutoff = alpha, qvalueCutoff = 0.20,
               minGSSize = 10, maxGSSize = 500),
    error = function(e) { message("enrichKEGG failed: ", conditionMessage(e)); NULL }) else NULL
  if (nrows(ek) > 0) write.csv(as.data.frame(ek), out[["kegg"]], row.names = FALSE)
  kg <- NULL
  if (length(ranked) > 0) {
    set.seed(42)
    kg <- tryCatch(
      gseKEGG(geneList = ranked, organism = kegg_org, keyType = kegg_keytype,
              pvalueCutoff = alpha, pAdjustMethod = "BH", minGSSize = 10, maxGSSize = 500,
              eps = 0, seed = TRUE, verbose = FALSE),
      error = function(e) { message("gseKEGG failed: ", conditionMessage(e)); NULL })
    if (nrows(kg) > 0) write.csv(as.data.frame(kg), out[["kegg_gsea"]], row.names = FALSE)
  }
  list(ekegg_all = ek, kegg_gse = kg, n_ora = nrows(ek), n_gsea = nrows(kg))
}

# GO-route selection. clusterProfiler KEGG is always-on (the proven path) and is the
# SOLE source of ekegg_all/kegg_gse for EVERY route, so the figures rule keeps rendering
# the KEGG S4 plots regardless of backend. The GO route is chosen as:
#   1. OrgDb   — backend != "gprofiler" AND OrgDb loads AND bitr maps > 0 ids
#   2. gProf   — else if backend == "gprofiler" OR a g:Profiler organism is set
#   3. none    — KEGG-only (e.g. Fusarium graminearum: g:Profiler rejects FGSG_ ids)
# Routes 1/3 set gprofiler_table = NULL; route 2 sets gse/kegg via clusterProfiler still.

# Probe the OrgDb: load the package and map the result ids. On any failure (package
# not installed, ~0 ids mapped) orgdb_ok stays FALSE so the run falls through to the
# g:Profiler or KEGG-only route instead of aborting (W1 load-bearing fix).
orgdb_ok <- FALSE
orgdb_probe <- NULL
if (has_orgdb && !identical(backend, "gprofiler")) {
  orgdb_probe <- tryCatch({
    suppressMessages({
      library(clusterProfiler)
      library(orgdb_name, character.only = TRUE)
    })
    orgdb <- get(orgdb_name)
    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj), ]
    ids <- strip_version(res$gene_id)
    map <- bitr(ids, fromType = keytype, toType = "ENTREZID", OrgDb = orgdb)
    n_mapped <- length(unique(map[[keytype]][!is.na(map$ENTREZID)]))
    list(orgdb = orgdb, res = res, ids = ids, map = map,
         n_in = length(unique(ids)), n_mapped = n_mapped)
  }, error = function(e) {
    message("OrgDb route unavailable (", orgdb_name, "): ", conditionMessage(e))
    NULL
  })
  if (!is.null(orgdb_probe) && orgdb_probe$n_mapped > 0) {
    orgdb_ok <- TRUE
  } else if (!is.null(orgdb_probe)) {
    # OrgDb loaded but ~0 ids mapped: do not silently run an empty GO route. Fall
    # through to KEGG/g:Profiler; the ID-conversion message is recorded if no route hits.
    message(sprintf("OrgDb bitr mapped %d/%d ids for keytype %s; falling through.",
                    orgdb_probe$n_mapped, orgdb_probe$n_in, keytype))
  }
}

if (orgdb_ok) {
  result <- tryCatch({
    orgdb <- orgdb_probe$orgdb
    res <- orgdb_probe$res
    ids <- orgdb_probe$ids
    map <- orgdb_probe$map
    res$base_id <- ids
    res$ENTREZID <- map$ENTREZID[match(ids, map[[keytype]])]
    res <- res[!is.na(res$ENTREZID), ]
    res <- res[!duplicated(res$ENTREZID), ]
    universe <- unique(res$ENTREZID)
    write_id_map(res)  # entrez<->symbol/gene_id bridge for term-gene extraction

    # Map a gene_id list (from the deseq2 up/down CSVs) to ENTREZ via res.
    to_entrez <- function(path) {
      if (!file.exists(path)) return(character(0))
      df <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
      if (is.null(df) || !"gene_id" %in% names(df) || nrow(df) == 0) return(character(0))
      base <- strip_version(df$gene_id)
      unique(res$ENTREZID[match(base, res$base_id)])
    }
    run_ora <- function(genes, path) {
      genes <- genes[!is.na(genes)]
      if (length(genes) < 1) return(NULL)
      # pvalueCutoff follows the user's deseq2.alpha (default 0.05); qvalueCutoff is
      # left at clusterProfiler's enrichGO default (0.20), independent of alpha.
      ego <- tryCatch(enrichGO(gene = genes, universe = universe, OrgDb = orgdb,
                      keyType = "ENTREZID", ont = "BP", pAdjustMethod = "BH",
                      pvalueCutoff = alpha, qvalueCutoff = 0.20,
                      minGSSize = 10, maxGSSize = 500, readable = TRUE),
                      error = function(e) NULL)
      if (!is.null(ego) && nrow(as.data.frame(ego)) > 0) {
        write.csv(as.data.frame(ego), path, row.names = FALSE)
      }
      ego  # return the enrichResult (or NULL) so it can be persisted for figures
    }

    up_e <- to_entrez(up_file)
    down_e <- to_entrez(down_file)
    all_sig <- unique(c(up_e, down_e))
    ego_all <- run_ora(all_sig, out[["go"]])
    ego_up <- run_ora(up_e, out[["go_up"]])
    ego_down <- run_ora(down_e, out[["go_down"]])
    n_all <- nrows(ego_all); n_up <- nrows(ego_up); n_down <- nrows(ego_down)

    gene_list <- res$log2FoldChange
    names(gene_list) <- res$ENTREZID
    gene_list <- sort(gene_list[!is.na(gene_list)], decreasing = TRUE)
    set.seed(42)
    # Gene-set size limits and BH correction are gseGO's defaults, stated explicitly.
    gse <- tryCatch(
      gseGO(geneList = gene_list, OrgDb = orgdb, ont = "BP", keyType = "ENTREZID",
            pvalueCutoff = alpha, pAdjustMethod = "BH", minGSSize = 10, maxGSSize = 500,
            eps = 0, seed = TRUE, verbose = FALSE),
      error = function(e) NULL)
    n_gsea <- nrows(gse)
    if (n_gsea > 0) write.csv(as.data.frame(gse), out[["gsea"]], row.names = FALSE)

    # Disease-ontology ORA (human/mouse only). DOSE::enrichDO uses ont="HDO" and
    # organism in {hsa, mm}, and THROWS for any other organism and on the first-run
    # HDO.sqlite fetch. It MUST have its OWN tryCatch: the saveRDS below is inside
    # the outer tryCatch, so an uncaught enrichDO error would wipe ALL persisted
    # enrichment objects and figures.
    do_org <- if (grepl("org.Hs", orgdb_name)) "hsa" else if (grepl("org.Mm", orgdb_name)) "mm" else NA_character_
    ego_do <- tryCatch(
      if (is.na(do_org)) NULL else DOSE::enrichDO(gene = all_sig, ont = "HDO", organism = do_org,
                         universe = universe, pvalueCutoff = alpha, qvalueCutoff = 0.20),
      error = function(e) { message("enrichDO skipped: ", conditionMessage(e)); NULL })
    n_do <- nrows(ego_do)

    # KEGG ORA + GSEA on the ENTREZ ids (KEGG uses NCBI GeneIDs for OrgDb species).
    kegg <- if (has_kegg) run_kegg(all_sig, gene_list, "ncbi-geneid")
            else list(ekegg_all = NULL, kegg_gse = NULL, n_ora = 0, n_gsea = 0)

    # Persist the enrichment objects (+ ranked geneList and OrgDb name) so the
    # enrichment_figures rule can render dotplot/GSEA/network plots without re-running.
    # backend/gprofiler_table let the figures rule switch on obj$backend uniformly.
    saveRDS(list(ego_all = ego_all, ego_up = ego_up, ego_down = ego_down,
                 gse = gse, ego_do = ego_do,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = gene_list, orgdb = orgdb_name,
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "clusterprofiler", gprofiler_table = NULL),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      sprintf("Universe (tested genes): %d", length(universe)),
      sprintf("Up-regulated: %d genes, %d GO BP terms (ORA)", length(up_e), n_up),
      sprintf("Down-regulated: %d genes, %d GO BP terms (ORA)", length(down_e), n_down),
      sprintf("Combined significant: %d genes, %d GO BP terms", length(all_sig), n_all),
      sprintf("GSEA GO BP gene sets (directional, full ranked list): %d", n_gsea),
      sprintf("KEGG pathways: %d (ORA), %d (GSEA)", kegg$n_ora, kegg$n_gsea))
    list(status = if (length(all_sig) >= 5) "PASS" else "REVIEW_REQUIRED",
         message = sprintf("Enrichment: GO up=%d, down=%d, combined=%d terms, GSEA=%d; KEGG ORA=%d, GSEA=%d.",
                           n_up, n_down, n_all, n_gsea, kegg$n_ora, kegg$n_gsea))
  }, error = function(e) {
    summary_lines <<- c(summary_lines, paste("Enrichment failed:", conditionMessage(e)))
    list(status = "REVIEW_REQUIRED",
         message = paste("Enrichment could not run:", conditionMessage(e)))
  })
} else if (identical(backend, "gprofiler") || has_gprofiler) {
  # g:Profiler GO route: no usable OrgDb but a g:Profiler organism is set (or the
  # user forced backend="gprofiler"). gost provides GO:BP ORA only; clusterProfiler
  # enrichKEGG/gseKEGG below remains the sole source of the KEGG S4 objects so the
  # figures rule renders the KEGG dotplot/GSEA unchanged. gost is ORA-only -> GSEA
  # keys (gse) stay NULL. Figures labelled by term Description, never raw ids.
  result <- tryCatch({
    suppressMessages({ library(clusterProfiler) })
    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj) & !is.na(res$log2FoldChange), ]
    res$base_id <- strip_version(res$gene_id)
    write_id_map(res)  # no entrez on this route; symbol/gene_id still bridge term extraction
    gene_list <- res$log2FoldChange
    names(gene_list) <- res$base_id
    gene_list <- sort(gene_list[!duplicated(names(gene_list))], decreasing = TRUE)
    tested_genes <- unique(res$base_id)  # tested-gene background for gost custom_bg

    up_ids <- read_ids_csv(up_file)
    down_ids <- read_ids_csv(down_file)
    all_ids <- unique(c(up_ids, down_ids))

    # gprofiler2 is a Stage-2 env addition and may be absent: wrap the load + gost
    # so a missing package or a network failure degrades to KEGG-only, never crashes.
    gp <- tryCatch({
      suppressMessages(library(gprofiler2))
      query <- all_ids
      gg <- gost(query = query, organism = gprofiler_org,
                 sources = c("GO:BP", "KEGG", "REAC"),
                 custom_bg = tested_genes, significant = TRUE,
                 user_threshold = alpha, correction_method = "g_SCS")
      # On a namespace mismatch (gost returns nothing because g:Profiler did not
      # recognise the query ids), retry once after gconvert maps the query into the
      # g:Profiler internal namespace.
      if (is.null(gg$result) || nrow(gg$result) == 0) {
        conv <- tryCatch(gconvert(query = query, organism = gprofiler_org),
                         error = function(e) NULL)
        if (!is.null(conv) && nrow(conv) > 0) {
          q2 <- unique(conv$target[!is.na(conv$target)])
          if (length(q2) > 0)
            gg <- gost(query = q2, organism = gprofiler_org,
                       sources = c("GO:BP", "KEGG", "REAC"),
                       custom_bg = tested_genes, significant = TRUE,
                       user_threshold = alpha, correction_method = "g_SCS")
        }
      }
      gg
    }, error = function(e) {
      message("g:Profiler gost unavailable: ", conditionMessage(e)); NULL
    })

    gprofiler_table <- if (!is.null(gp) && !is.null(gp$result) && nrow(gp$result) > 0)
                         gp$result else NULL
    # GO:BP ORA rows -> go_ora.csv. The gost result uses `term_name` as the term
    # Description; keep that column so downstream figures label by name, not GO id.
    n_go <- 0
    if (!is.null(gprofiler_table)) {
      go_rows <- gprofiler_table[gprofiler_table$source == "GO:BP", , drop = FALSE]
      n_go <- nrow(go_rows)
      # gost results carry list-columns (e.g. `parents`) that write.csv cannot
      # serialize ("unimplemented type 'list' in 'EncodeElement'"); the error would
      # otherwise abort the whole route, including the always-on KEGG block below.
      # Keep only atomic columns for the CSV (the full table is kept in the RDS).
      if (n_go > 0) {
        atomic <- vapply(go_rows, is.atomic, logical(1))
        write.csv(go_rows[, atomic, drop = FALSE], out[["go"]], row.names = FALSE)
      }
    }

    # KEGG ORA + GSEA via clusterProfiler on the raw locus-tag ids (always-on tail).
    kegg <- if (has_kegg) run_kegg(all_ids, gene_list, "kegg")
            else list(ekegg_all = NULL, kegg_gse = NULL, n_ora = 0, n_gsea = 0)

    saveRDS(list(ego_all = NULL, ego_up = NULL, ego_down = NULL,
                 gse = NULL, ego_do = NULL,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = gene_list, orgdb = "",
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "gprofiler", gprofiler_table = gprofiler_table),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      sprintf("GO route: g:Profiler (organism %s).", gprofiler_org),
      sprintf("GO BP terms (gost ORA): %d", n_go),
      sprintf("KEGG organism: %s", if (has_kegg) kegg_org else "(none)"),
      sprintf("Significant genes (ORA input): %d", length(all_ids)),
      sprintf("KEGG pathways: %d (ORA), %d (GSEA)", kegg$n_ora, kegg$n_gsea))
    n_total <- n_go + kegg$n_ora + kegg$n_gsea
    if (n_total == 0)
      list(status = "REVIEW_REQUIRED",
           message = sprintf("g:Profiler/KEGG enrichment empty: 0 GO terms (gost %s) and 0 KEGG pathways (%s) — check gene id namespace.",
                             gprofiler_org, if (has_kegg) kegg_org else "no code"))
    else
      list(status = "PASS",
           message = sprintf("g:Profiler GO=%d terms; KEGG ORA=%d, GSEA=%d.",
                             n_go, kegg$n_ora, kegg$n_gsea))
  }, error = function(e) {
    summary_lines <<- c(summary_lines, paste("g:Profiler enrichment failed:", conditionMessage(e)))
    list(status = "REVIEW_REQUIRED",
         message = paste("g:Profiler enrichment could not run:", conditionMessage(e)))
  })
} else {
  # KEGG-only path: no OrgDb and no g:Profiler organism, but a KEGG code exists. KEGG
  # keys genes by their native locus-tag ids (e.g. FGSG_xxxxx for Fusarium), so the
  # deseq2 gene ids are passed straight through with keyType = "kegg".
  result <- tryCatch({
    suppressMessages({ library(clusterProfiler) })
    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj) & !is.na(res$log2FoldChange), ]
    res$base_id <- strip_version(res$gene_id)
    write_id_map(res)  # no entrez on this route; symbol/gene_id still bridge term extraction
    gene_list <- res$log2FoldChange
    names(gene_list) <- res$base_id
    gene_list <- sort(gene_list[!duplicated(names(gene_list))], decreasing = TRUE)

    up_ids <- read_ids_csv(up_file)
    down_ids <- read_ids_csv(down_file)
    all_ids <- unique(c(up_ids, down_ids))

    kegg <- run_kegg(all_ids, gene_list, "kegg")

    saveRDS(list(ego_all = NULL, ego_up = NULL, ego_down = NULL,
                 gse = NULL, ego_do = NULL,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = gene_list, orgdb = "",
                 kegg = if (has_kegg) kegg_org else "",
                 backend = "clusterprofiler", gprofiler_table = NULL),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      "GO/disease enrichment: skipped (no Bioconductor OrgDb for this organism).",
      sprintf("KEGG organism: %s", kegg_org),
      sprintf("Ranked genes (GSEA input): %d", length(gene_list)),
      sprintf("Significant genes (ORA input): %d", length(all_ids)),
      sprintf("KEGG pathways: %d (ORA), %d (GSEA)", kegg$n_ora, kegg$n_gsea))
    # ID-conversion guard: 0 KEGG hits with non-trivial input means the raw ids are
    # almost certainly the wrong namespace for keyType="kegg" -> fail loud, not green-empty.
    if (kegg$n_ora == 0 && kegg$n_gsea == 0)
      list(status = "REVIEW_REQUIRED",
           message = sprintf("KEGG-only enrichment empty: 0/%d gene ids mapped for KEGG code %s (keyType=kegg) — wrong identifier namespace?",
                             length(all_ids), kegg_org))
    else
      list(status = "PASS",
           message = sprintf("KEGG-only enrichment (no OrgDb): ORA=%d pathways, GSEA=%d sets.",
                             kegg$n_ora, kegg$n_gsea))
  }, error = function(e) {
    summary_lines <<- c(summary_lines, paste("KEGG enrichment failed:", conditionMessage(e)))
    list(status = "REVIEW_REQUIRED",
         message = paste("KEGG enrichment could not run:", conditionMessage(e)))
  })
}

writeLines(summary_lines, out[["summary"]])
write_check(out[["check"]], result$status, result$message)
sink(type = "message")
close(log_con)
