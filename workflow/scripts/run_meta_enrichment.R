# Cross-study functional enrichment for the meta-analysis (0.21.0). clusterProfiler::compareCluster
# over a NAMED gene list {study_<S>_up, study_<S>_down for each study} + {convergent_up,
# convergent_down = meta_sig genes by common_direction}, on ONE shared universe (the intersected
# tested set) so the term-by-study columns are comparable. Org-db-gated exactly like run_enrichment.R:
# unmapped organisms write empty outputs + a PASS-skip check (not a failure). Persists the
# compareClusterResult to RDS so the figure rule restyles without re-running enrichment.
local({
  .m <- function(f) function(...) withCallingHandlers(f(...), warning = function(w) if (grepl("built under R version", conditionMessage(w), fixed = TRUE)) invokeRestart("muffleWarning"))
  assign("library", .m(base::library), envir = globalenv())
  assign("require", .m(base::require), envir = globalenv())
})
source(file.path(snakemake@scriptdir, "enrichment_mapping.R"))
source(file.path(snakemake@scriptdir, "meta_de_selection.R"))
log_con <- file(snakemake@log[[1]], open = "wt"); sink(log_con, type = "message")
results_file <- snakemake@input[["results"]]
orgdb_name <- snakemake@params[["orgdb"]]; keytype <- snakemake@params[["keytype"]]
ont <- tryCatch(snakemake@params[["ont"]], error = function(e) "BP")
alpha <- as.numeric(snakemake@params[["alpha"]])
lfc_threshold <- as.numeric(snakemake@params[["lfc_threshold"]])
out <- snakemake@output

write_check <- function(status, messages) {
  payload <- list(check = "18_meta_enrichment_qc", status = status,
                  messages = messages)
  jsonlite::write_json(payload, out[["check"]], auto_unbox = TRUE,
                       pretty = TRUE, na = "null")
}
nrows <- function(x) if (is.null(x)) 0 else tryCatch(nrow(as.data.frame(x)), error = function(e) 0)
strip_version <- function(id) { v <- grepl("^ENS", id); id[v] <- sub("\\.\\d+$", "", id[v]); id }
status_rank <- c(PASS = 0L, WARNING = 1L, REVIEW_REQUIRED = 2L, FAIL = 3L)
check_status <- "PASS"
check_messages <- list()
add_check_message <- function(status, message) {
  if (status_rank[[status]] > status_rank[[check_status]]) check_status <<- status
  check_messages[[length(check_messages) + 1L]] <<- list(status = status, message = message)
}
empty_mapping_evidence <- function() data.frame(
  input_id = character(0), mapping_status = character(0), accepted_entrez = character(0),
  resolution_keytype = character(0), resolution = character(0),
  exclusion_reason = character(0), routed_keytype = character(0),
  candidate_entrez = character(0), foreground_clusters = character(0),
  stringsAsFactors = FALSE)
write_mapping_evidence <- function(evidence) {
  write.table(evidence, out[["mapping"]], sep = "\t", quote = TRUE,
              qmethod = "double", row.names = FALSE, na = "")
}
skip <- function(reason, status = "PASS") {
  write.csv(data.frame(Cluster = character(0), ID = character(0), Description = character(0)),
            out[["ora"]], row.names = FALSE)
  saveRDS(NULL, out[["objects"]])
  add_check_message(status, reason)
  write_check(check_status, check_messages)
  sink(type = "message"); close(log_con); quit(save = "no", status = 0)
}
# Always create outputs first so the rule succeeds even on an early failure.
write.csv(data.frame(Cluster = character(0), ID = character(0), Description = character(0)),
          out[["ora"]], row.names = FALSE)
saveRDS(NULL, out[["objects"]])
write_mapping_evidence(empty_mapping_evidence())

build_meta_enrichment_sets <- function(res, ids, resolved, alpha, lfc_threshold,
                                       min_size = 5L) {
  accepted <- resolved$map
  res$entrez <- accepted$ENTREZID[match(ids, accepted$input_id)]
  source_set <- function(idx) unique(ids[idx][!is.na(ids[idx]) & nzchar(ids[idx])])
  study_cols <- grep("^study_.*_log2FC$", colnames(res), value = TRUE)
  studies <- sub("^study_(.*)_log2FC$", "\\1", study_cols)
  source_sets <- list()
  for (s in studies) {
    lfc <- res[[paste0("study_", s, "_log2FC")]]
    padj <- res[[paste0("study_", s, "_padj")]]
    direction <- meta_de_direction(padj, lfc, alpha, lfc_threshold)
    source_sets[[paste0(s, "_up")]] <- source_set(which(direction == "Up"))
    source_sets[[paste0(s, "_down")]] <- source_set(which(direction == "Down"))
  }
  source_sets[["convergent_up"]] <- source_set(
    which(res$meta_sig %in% TRUE & res$common_direction == "up"))
  source_sets[["convergent_down"]] <- source_set(
    which(res$meta_sig %in% TRUE & res$common_direction == "down"))
  to_entrez <- function(source_ids) {
    mapped <- accepted$ENTREZID[match(source_ids, accepted$input_id)]
    unique(mapped[!is.na(mapped) & nzchar(mapped)])
  }
  mapped_sets <- lapply(source_sets, to_entrez)
  lists <- mapped_sets[vapply(mapped_sets, length, integer(1)) >= min_size]
  selected_source <- unique(unlist(source_sets, use.names = FALSE))
  selected_mapped <- accepted$input_id[accepted$input_id %in% selected_source]
  list(
    res = res,
    universe = unique(accepted$ENTREZID),
    source_sets = source_sets,
    lists = lists,
    foreground_source_n = length(selected_source),
    foreground_mapped_n = length(unique(selected_mapped)))
}

meta_mapping_evidence <- function(ids, resolved, source_sets) {
  input_ids <- unique(as.character(ids[!is.na(ids) & nzchar(as.character(ids))]))
  accepted <- resolved$map
  excluded <- resolved$exclusions
  cluster_membership <- vapply(input_ids, function(id) {
    paste(sort(names(source_sets)[vapply(source_sets, function(values) id %in% values,
                                         logical(1))]), collapse = ";")
  }, character(1))
  accepted_row <- match(input_ids, accepted$input_id)
  excluded_row <- match(input_ids, excluded$input_id)
  reason <- excluded$reason[excluded_row]
  mapping_status <- ifelse(!is.na(accepted_row), "accepted",
                           ifelse(reason %in% c("routed_one_to_many",
                                               "unresolved_cross_keytype"),
                                  "ambiguous", "unmapped"))
  data.frame(
    input_id = input_ids,
    mapping_status = mapping_status,
    accepted_entrez = accepted$ENTREZID[accepted_row],
    resolution_keytype = accepted$keytype[accepted_row],
    resolution = accepted$resolution[accepted_row],
    exclusion_reason = reason,
    routed_keytype = excluded$routed_keytype[excluded_row],
    candidate_entrez = excluded$candidate_entrez[excluded_row],
    foreground_clusters = cluster_membership,
    stringsAsFactors = FALSE)
}

# check.names = FALSE: keep hyphens/dots in study names intact so the study_<S>_log2FC /
# study_<S>_padj lookups below resolve (default repair turns E-MTAB-2523 into E.MTAB.2523).
res <- tryCatch(read.csv(results_file, stringsAsFactors = FALSE, check.names = FALSE), error = function(e) NULL)
if (is.null(res) || nrow(res) == 0 || !"meta_sig" %in% colnames(res))
  skip("Cross-study enrichment skipped: the meta-analysis produced no shared-gene result.")
if (is.null(orgdb_name) || !nzchar(orgdb_name))
  skip("Cross-study enrichment skipped: organism has no Bioconductor OrgDb (gene-level meta is unaffected).")

suppressMessages({ library(clusterProfiler); ok <- require(orgdb_name, character.only = TRUE) })
if (!isTRUE(ok)) skip(sprintf("Cross-study enrichment skipped: OrgDb %s not installed.", orgdb_name))
orgdb <- get(orgdb_name)

ids <- strip_version(res$gene_id)
resolved <- tryCatch(
  map_ids_with_routing(unique(ids), orgdb, keytype, orgdb_name),
  error = function(e) {
    message("identifier mapping failed: ", conditionMessage(e))
    NULL
  })
if (is.null(resolved)) {
  skip(sprintf("Cross-study enrichment skipped: identifier mapping failed for keytype %s.",
               keytype), "REVIEW_REQUIRED")
}
sets <- build_meta_enrichment_sets(res, ids, resolved, alpha, lfc_threshold)
res <- sets$res
universe <- sets$universe
lists <- sets$lists
write_mapping_evidence(meta_mapping_evidence(ids, resolved, sets$source_sets))
mapping_message <- sprintf(
  paste0("Meta enrichment identifier mapping: %d/%d source ids accepted into %d unique ",
         "Entrez ids; %d/%d selected foreground source ids accepted; %d ambiguous and %d ",
         "unmapped ids excluded (requested keytype %s; effective keytype %s)."),
  resolved$mapped_inputs, resolved$total_inputs, length(universe),
  sets$foreground_mapped_n, sets$foreground_source_n,
  resolved$ambiguous_excluded, resolved$unmapped_inputs,
  resolved$requested_keytype, resolved$effective_keytype)
add_check_message(if (resolved$ambiguous_excluded > 0L) "REVIEW_REQUIRED" else "PASS",
                  mapping_message)
if (!length(universe))
  skip(sprintf("Cross-study enrichment skipped: 0 of %d gene ids mapped to ENTREZ (keytype %s).",
               resolved$total_inputs, resolved$effective_keytype))
if (length(lists) < 2)
  skip("Cross-study enrichment skipped: fewer than 2 gene sets have >=5 mapped genes.")

set.seed(42)
cc <- tryCatch(
  compareCluster(geneClusters = lists, fun = "enrichGO", OrgDb = orgdb, keyType = "ENTREZID",
                 ont = ont, universe = universe, pAdjustMethod = "BH",
                 pvalueCutoff = alpha, qvalueCutoff = 0.20, minGSSize = 10, maxGSSize = 500),
  error = function(e) { message("compareCluster failed: ", conditionMessage(e)); NULL })
if (is.null(cc) || nrows(cc) == 0)
  skip("Cross-study enrichment: no GO terms passed the FDR cutoff in any study/convergent set.")

# Attach readable gene symbols (setReadable) then persist.
cc <- tryCatch(setReadable(cc, OrgDb = orgdb, keyType = "ENTREZID"), error = function(e) cc)
saveRDS(cc, out[["objects"]])
df <- as.data.frame(cc)
write.csv(df, out[["ora"]], row.names = FALSE)
n_terms <- length(unique(df$ID)); n_clusters <- length(unique(df$Cluster))
add_check_message("PASS", sprintf("Cross-study GO enrichment: %d terms across %d gene sets (%s).",
                                  n_terms, n_clusters,
                                  paste(unique(df$Cluster), collapse = ", ")))
write_check(check_status, check_messages)
sink(type = "message"); close(log_con)
