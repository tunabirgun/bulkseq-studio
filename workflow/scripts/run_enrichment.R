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
alpha <- as.numeric(snakemake@params[["alpha"]])
out <- snakemake@output

write_check <- function(path, status, message) {
  msg <- gsub('"', '\\\\"', message)
  json <- sprintf('{\n  "check": "10_enrichment_qc",\n  "status": "%s",\n  "messages": [\n    {"status": "%s", "message": "%s"}\n  ]\n}',
                  status, status, msg)
  writeLines(json, path)
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)

# Always create the output files first so the rule succeeds even on failure.
for (k in c("go", "go_up", "go_down", "gsea", "kegg", "kegg_gsea")) writeLines("", out[[k]])
# Persist an (empty) objects RDS up front so the enrichment_figures rule always
# has an input, even when enrichment is skipped or fails. Overwritten on success.
saveRDS(list(), out[["objects"]])
summary_lines <- c("Functional enrichment summary", "=============================", "")

has_orgdb <- !is.null(orgdb_name) && nzchar(orgdb_name)
has_kegg  <- !is.null(kegg_org)  && nzchar(kegg_org)

# Neither GO nor KEGG mapped (e.g. an organism with no OrgDb and no KEGG code):
# skip cleanly rather than risk running against the wrong species' database.
if (!has_orgdb && !has_kegg) {
  writeLines(c(summary_lines,
               "Skipped: no Bioconductor OrgDb and no KEGG code mapped for this organism.",
               "GO supports human, mouse, fly, worm, zebrafish, yeast, Arabidopsis;",
               "KEGG needs a KEGG organism code (set enrichment.kegg_organism, e.g. 'fgr')."),
             out[["summary"]])
  write_check(out[["check"]], "PASS",
              "Enrichment skipped: organism not mapped (gene-level DE is unaffected).")
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}

# Read a deseq2 up/down CSV and return its gene_id column (version stripped).
read_ids_csv <- function(path) {
  if (!file.exists(path)) return(character(0))
  df <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(df) || !"gene_id" %in% names(df) || nrow(df) == 0) return(character(0))
  unique(sub("\\..*$", "", df$gene_id))
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

if (has_orgdb) {
  result <- tryCatch({
    suppressMessages({
      library(clusterProfiler)
      library(orgdb_name, character.only = TRUE)
    })
    orgdb <- get(orgdb_name)

    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj), ]
    ids <- sub("\\..*$", "", res$gene_id)  # strip version suffix if any
    map <- bitr(ids, fromType = keytype, toType = "ENTREZID", OrgDb = orgdb)
    res$base_id <- ids
    res$ENTREZID <- map$ENTREZID[match(ids, map[[keytype]])]
    res <- res[!is.na(res$ENTREZID), ]
    res <- res[!duplicated(res$ENTREZID), ]
    universe <- unique(res$ENTREZID)

    # Map a gene_id list (from the deseq2 up/down CSVs) to ENTREZ via res.
    to_entrez <- function(path) {
      if (!file.exists(path)) return(character(0))
      df <- tryCatch(read.csv(path, stringsAsFactors = FALSE), error = function(e) NULL)
      if (is.null(df) || !"gene_id" %in% names(df) || nrow(df) == 0) return(character(0))
      base <- sub("\\..*$", "", df$gene_id)
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
    saveRDS(list(ego_all = ego_all, ego_up = ego_up, ego_down = ego_down,
                 gse = gse, ego_do = ego_do,
                 ekegg_all = kegg$ekegg_all, kegg_gse = kegg$kegg_gse,
                 geneList = gene_list, orgdb = orgdb_name),
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
} else {
  # KEGG-only path: no OrgDb for this organism, but a KEGG code exists. KEGG keys
  # genes by their native locus-tag ids (e.g. FGSG_xxxxx for Fusarium), so the
  # deseq2 gene ids are passed straight through with keyType = "kegg".
  result <- tryCatch({
    suppressMessages({ library(clusterProfiler) })
    res <- read.csv(results_file, stringsAsFactors = FALSE)
    res <- res[!is.na(res$padj) & !is.na(res$log2FoldChange), ]
    res$base_id <- sub("\\..*$", "", res$gene_id)
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
                 geneList = gene_list, orgdb = ""),
            out[["objects"]])

    summary_lines <<- c(summary_lines,
      "GO/disease enrichment: skipped (no Bioconductor OrgDb for this organism).",
      sprintf("KEGG organism: %s", kegg_org),
      sprintf("Ranked genes (GSEA input): %d", length(gene_list)),
      sprintf("Significant genes (ORA input): %d", length(all_ids)),
      sprintf("KEGG pathways: %d (ORA), %d (GSEA)", kegg$n_ora, kegg$n_gsea))
    list(status = if (kegg$n_ora > 0 || kegg$n_gsea > 0) "PASS" else "REVIEW_REQUIRED",
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
