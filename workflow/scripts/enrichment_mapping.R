# Shared identifier routing for OrgDb-backed enrichment.

merge_mapping_candidates <- function(ids, candidates, eligible_keytypes,
                                     configured_keytype) {
  ids <- unique(as.character(ids[!is.na(ids) & nzchar(as.character(ids))]))
  normalize <- function(mapped) {
    if (is.null(mapped) || !is.data.frame(mapped) ||
        !all(c("input_id", "ENTREZID") %in% names(mapped))) {
      return(data.frame(input_id = character(0), ENTREZID = character(0)))
    }
    mapped$input_id <- as.character(mapped$input_id)
    mapped$ENTREZID <- as.character(mapped$ENTREZID)
    unique(mapped[
      mapped$input_id %in% ids & !is.na(mapped$input_id) & nzchar(mapped$input_id) &
      !is.na(mapped$ENTREZID) & nzchar(mapped$ENTREZID),
      c("input_id", "ENTREZID"), drop = FALSE])
  }
  candidates <- lapply(candidates, normalize)
  route_for <- function(id) {
    upper <- toupper(id)
    if ("TAIR" %in% eligible_keytypes && grepl("^AT[1-5CM]G[0-9]{5}(\\.[0-9]+)?$", upper)) {
      return("TAIR")
    }
    if ("ENSEMBL" %in% eligible_keytypes && grepl("^ENS[A-Z]*G[0-9]+(\\.[0-9]+)?$", upper)) {
      return("ENSEMBL")
    }
    if (configured_keytype %in% eligible_keytypes) configured_keytype else NA_character_
  }

  accepted <- list()
  excluded <- list()
  one_to_many_observed <- 0L
  cross_discordance_observed <- 0L
  cross_discordance_resolved <- 0L
  for (id in ids) {
    by_key <- lapply(eligible_keytypes, function(key) {
      table <- candidates[[key]]
      unique(table$ENTREZID[table$input_id == id])
    })
    names(by_key) <- eligible_keytypes
    nonempty <- by_key[lengths(by_key) > 0L]
    all_hits <- unique(unlist(nonempty, use.names = FALSE))
    has_one_to_many <- any(lengths(nonempty) > 1L)
    has_cross_discordance <- length(nonempty) > 1L && length(all_hits) > 1L
    one_to_many_observed <- one_to_many_observed + as.integer(has_one_to_many)
    cross_discordance_observed <- cross_discordance_observed + as.integer(has_cross_discordance)
    route <- route_for(id)
    routed_hits <- if (!is.na(route) && route %in% names(by_key)) by_key[[route]] else character(0)

    resolution <- NULL
    if (length(routed_hits) == 1L) {
      resolution <- list(entrez = routed_hits[[1]], keytype = route,
                         method = paste0("routed:", route))
      if (has_cross_discordance) cross_discordance_resolved <- cross_discordance_resolved + 1L
    } else if (length(routed_hits) > 1L) {
      excluded[[length(excluded) + 1L]] <- data.frame(
        input_id = id, reason = "routed_one_to_many", routed_keytype = route,
        candidate_entrez = paste(sort(routed_hits), collapse = ";"))
      next
    } else if (length(all_hits) == 1L) {
      supporting <- names(nonempty)[vapply(nonempty, function(values) all_hits[[1]] %in% values,
                                           logical(1))]
      resolution <- list(entrez = all_hits[[1]], keytype = paste(supporting, collapse = "+"),
                         method = "eligible_keytypes_agree")
    } else if (length(all_hits) > 1L) {
      excluded[[length(excluded) + 1L]] <- data.frame(
        input_id = id, reason = "unresolved_cross_keytype", routed_keytype = route,
        candidate_entrez = paste(sort(all_hits), collapse = ";"))
      next
    } else {
      excluded[[length(excluded) + 1L]] <- data.frame(
        input_id = id, reason = "unmapped", routed_keytype = route,
        candidate_entrez = "")
      next
    }
    accepted[[length(accepted) + 1L]] <- data.frame(
      input_id = id, ENTREZID = as.character(resolution$entrez),
      keytype = resolution$keytype, resolution = resolution$method)
  }

  mapping <- if (length(accepted)) do.call(rbind, accepted) else
    data.frame(input_id = character(0), ENTREZID = character(0),
               keytype = character(0), resolution = character(0))
  exclusions <- if (length(excluded)) do.call(rbind, excluded) else
    data.frame(input_id = character(0), reason = character(0),
               routed_keytype = character(0), candidate_entrez = character(0))
  ambiguous_reasons <- c("routed_one_to_many", "unresolved_cross_keytype")
  list(
    map = mapping,
    exclusions = exclusions,
    eligible_keytypes = eligible_keytypes,
    configured_keytype = configured_keytype,
    total_inputs = length(ids),
    mapped_inputs = nrow(mapping),
    unmapped_inputs = sum(exclusions$reason == "unmapped"),
    ambiguous_excluded = sum(exclusions$reason %in% ambiguous_reasons),
    one_to_many_observed = one_to_many_observed,
    cross_discordance_observed = cross_discordance_observed,
    cross_discordance_resolved = cross_discordance_resolved,
    duplicate_entrez = sum(duplicated(mapping$ENTREZID))
  )
}

resolve_configured_keytype <- function(configured_keytype, supported, orgdb_package = "") {
  configured_keytype <- as.character(configured_keytype)
  if (configured_keytype %in% supported) return(configured_keytype)
  # org.Sc.sgd.db exposes the official yeast gene-name namespace as GENENAME
  # (and older releases may expose only COMMON), not SYMBOL.
  if (identical(configured_keytype, "SYMBOL") &&
      identical(as.character(orgdb_package), "org.Sc.sgd.db")) {
    equivalents <- c("GENENAME", "COMMON")
    available <- equivalents[equivalents %in% supported]
    if (length(available)) return(available[[1]])
  }
  configured_keytype
}

map_ids_with_routing <- function(ids, orgdb, configured_keytype, orgdb_package = "") {
  ids <- unique(as.character(ids[!is.na(ids) & nzchar(as.character(ids))]))
  supported <- AnnotationDbi::keytypes(orgdb)
  effective_keytype <- resolve_configured_keytype(
    configured_keytype, supported, orgdb_package)
  eligible <- unique(c(effective_keytype, configured_keytype, "TAIR", "ENSEMBL",
                       "ENTREZID", "SYMBOL", "ALIAS"))
  eligible <- eligible[!is.na(eligible) & nzchar(eligible) & eligible %in% supported]
  candidates <- list()
  for (candidate in eligible) {
    mapped <- if (identical(candidate, "ENTREZID")) {
      valid <- tryCatch(AnnotationDbi::keys(orgdb, keytype = "ENTREZID"),
                        error = function(e) character(0))
      hits <- intersect(ids, as.character(valid))
      data.frame(input_id = hits, ENTREZID = hits, stringsAsFactors = FALSE)
    } else tryCatch(
      suppressWarnings(clusterProfiler::bitr(
        ids, fromType = candidate, toType = "ENTREZID", OrgDb = orgdb,
        drop = TRUE)),
      error = function(e) NULL)
    if (!is.null(mapped) && nrow(mapped) > 0 && "ENTREZID" %in% names(mapped)) {
      if (candidate %in% names(mapped)) names(mapped)[names(mapped) == candidate] <- "input_id"
      candidates[[candidate]] <- mapped[, c("input_id", "ENTREZID"), drop = FALSE]
    }
  }
  resolved <- merge_mapping_candidates(ids, candidates, eligible, effective_keytype)
  resolved$requested_keytype <- configured_keytype
  resolved$effective_keytype <- effective_keytype
  resolved
}
