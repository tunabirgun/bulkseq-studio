# Validates the S. pombe / non-OrgDb KEGG locus-tag-to-GeneID bridge (A1) and the
# padj_lfc_ge_threshold companion column (S5) without depending on the snakemake object.
# Run in the bulkseq env: micromamba run -n bulkseq Rscript tests/test_kegg_geneid_bridge.R

extract_function <- function(path, name) {
  lines <- readLines(path)
  start <- grep(paste0("^", name, " <- function"), lines)[1]
  stopifnot(!is.na(start))
  depth <- 0L
  end <- start
  for (i in seq(start, length(lines))) {
    depth <- depth + lengths(regmatches(lines[i], gregexpr("\\{", lines[i]))) -
      lengths(regmatches(lines[i], gregexpr("\\}", lines[i])))
    if (i > start && depth == 0L) { end <- i; break }
  }
  eval(parse(text = paste(lines[start:end], collapse = "\n")), envir = globalenv())
}

extract_value <- function(path, name) {
  lines <- readLines(path)
  i <- grep(paste0("^", name, " <- "), lines)[1]
  stopifnot(!is.na(i))
  eval(parse(text = lines[i]), envir = globalenv())
}

extract_function("workflow/scripts/de_common.R", "annotate_from_gtf")
extract_function("workflow/scripts/run_enrichment.R", "bridge_kegg_geneid")
extract_function("workflow/scripts/run_enrichment.R", "kegg_geneid_lookup_for")
for (fn in c("kegg_probe_sources", "classify_kegg_keys", "probe_kegg_key_form",
             "resolve_kegg_key_form",
             "format_kegg_key_form", "kegg_route_gap", "geneid_column_usable"))
  extract_function("workflow/scripts/run_enrichment.R", fn)
extract_value("workflow/scripts/run_enrichment.R", "KEGG_KEY_FORMS_OBSERVABLE")

ok <- TRUE
check <- function(cond, msg) {
  if (!isTRUE(cond)) { cat("FAIL:", msg, "\n"); ok <<- FALSE } else cat("ok:", msg, "\n")
}

## ---- annotate_from_gtf: db_xref GeneID parsing (S. pombe RefSeq shape) ----
gtf <- tempfile(fileext = ".gtf")
writeLines(c(
  paste0('NC_003424.3\tRefSeq\tgene\t1\t100\t.\t-\t.\tgene_id "SPOM_SPAC212.11"; ',
         'db_xref "GeneID:2541932"; gene_biotype "protein_coding";'),
  paste0('NC_003424.3\tRefSeq\tgene\t200\t300\t.\t+\t.\tgene_id "SPOM_SPNCRNA.2000"; ',
         'gene_biotype "lncRNA";')  # no db_xref: must stay NA, not crash
), gtf)
annot <- annotate_from_gtf(gtf, c("SPOM_SPAC212.11", "SPOM_SPNCRNA.2000", "unknown_id"))
check(annot$geneid[["SPOM_SPAC212.11"]] == "2541932", "GeneID parsed from db_xref")
check(is.na(annot$geneid[["SPOM_SPNCRNA.2000"]]), "gene without db_xref stays NA")
check(is.na(annot$geneid[["unknown_id"]]), "gene absent from the GTF stays NA")
unlink(gtf)

## ---- bridge_kegg_geneid: locus tag -> GeneID, rice no-op, negative case ----
lookup <- c(SPOM_SPAC212.11 = "2541932", SPOM_SPNCRNA.2000 = NA_character_)
check(identical(bridge_kegg_geneid("SPOM_SPAC212.11", lookup), "2541932"),
      "locus tag bridged to its NCBI GeneID")
check(identical(bridge_kegg_geneid("SPOM_SPNCRNA.2000", lookup), "SPOM_SPNCRNA.2000"),
      "unresolved lookup entry (NA) passes the original id through unchanged")
check(identical(bridge_kegg_geneid("4326813", c("4326813" = "999999")), "4326813"),
      "already-numeric id (rice post-LOC-strip) is a no-op even with a lookup hit available")
check(identical(bridge_kegg_geneid(character(0), lookup), character(0)),
      "empty id vector passes through")
check(identical(bridge_kegg_geneid("SPOM_SPAC212.11", NULL), "SPOM_SPAC212.11"),
      "no lookup table (non-'kegg' keytype route) is a no-op")
# Negative case: feeding the bridge a lookup keyed by the WRONG id space (symbols
# instead of locus tags) must not spuriously "map" anything -- the id passes through.
wrong_space_lookup <- c(some_symbol = "123")
check(identical(bridge_kegg_geneid("SPOM_SPAC212.11", wrong_space_lookup), "SPOM_SPAC212.11"),
      "negative: a lookup in the wrong id space leaves the id unbridged, not silently wrong")

## ---- kegg_geneid_lookup_for: gate the bridge on the measured KEGG key form (H2) ----
# Fusarium (fgr): locus-tag key form -> no lookup is built, so bridging is a no-op
# even though a GeneID lookup is available from the GTF db_xref.
fgsg_lookup <- kegg_geneid_lookup_for("kegg", "locus_tag", "FGSG_00001", "2673421")
check(is.null(fgsg_lookup), "locus-tag key form organism (fgr) builds no lookup")
check(identical(bridge_kegg_geneid("FGSG_00001", fgsg_lookup), "FGSG_00001"),
      "FGSG id with a GeneID lookup but a locus-tag key form passes through unchanged")

# S. pombe (spo): numeric/geneid key form -> the lookup is built and bridges.
spom_lookup <- kegg_geneid_lookup_for("kegg", "geneid", "SPOM_SPAC212.11", "2541932")
check(identical(unname(spom_lookup["SPOM_SPAC212.11"]), "2541932"),
      "geneid key form organism (spo) builds the lookup")
check(identical(bridge_kegg_geneid("SPOM_SPAC212.11", spom_lookup), "2541932"),
      "SPOM id with a numeric key form bridges to its NCBI GeneID")

# Negative cases: an unmeasured/failed KEGG query, or a non-'kegg' keytype route,
# must not build a lookup either.
check(is.null(kegg_geneid_lookup_for("kegg", "unknown", "SPOM_SPAC212.11", "2541932")),
      "negative: unknown key form (failed KEGGREST query) builds no lookup")
check(is.null(kegg_geneid_lookup_for("ncbi-geneid", "geneid", "SPOM_SPAC212.11", "2541932")),
      "negative: non-'kegg' keytype (OrgDb ENTREZ route) builds no lookup")

## ---- classify_kegg_keys: majority over the full key list, not all() over a head ----
# osa carries 2 non-numeric keys among 32,578 and ssc 24 among 21,008 (measured
# 2026-09-13); an all() rule over a 50-key head flips those organisms to locus_tag and
# suppresses the bridge for every gene.
osa_keys <- c("osa:LOC_Os01g01010", paste0("osa:", 1:100), "osa:ChrSy.fgenesh.mRNA.1")
# The fixture is only a control over head-sampling while a non-numeric key sits inside
# any head a sampling rule would take, so that property is asserted, not assumed.
check(!all(grepl("^[0-9]+$", sub("^[^:]+:", "", utils::head(osa_keys, 50)))),
      "the fixture places a non-numeric key inside a sampled head")
check(identical(classify_kegg_keys(osa_keys)$form, "geneid"),
      "majority-numeric key list classifies as geneid despite non-numeric keys")
check(classify_kegg_keys(osa_keys)$numeric_n == 100L &&
      classify_kegg_keys(osa_keys)$total_n == 102L,
      "the numeric fraction is reported as measured")
check(identical(classify_kegg_keys(c("fgr:FGSG_00001", "fgr:FGSG_00002"))$form, "locus_tag"),
      "locus-tag key list classifies as locus_tag")
check(identical(classify_kegg_keys(character(0))$form, "unknown"),
      "an empty key list cannot be classified")
# A 50/50 split is not a majority: the bridge stays off rather than guessing.
check(identical(classify_kegg_keys(c("x:1", "x:2", "x:A", "x:B"))$form, "locus_tag"),
      "negative: an exact tie does not become geneid")

## ---- probe_kegg_key_form: the fallback chain under three network states ----
# The states below inject their own sources, so the production chain itself is
# checked here: dropping a fallback from it would otherwise go unnoticed.
check(identical(names(kegg_probe_sources()), c("link", "list", "conv", "info")),
      "the production probe chain is link, then list, then conv, then info")
sources_healthy <- list(
  link = function(org) paste0(org, ":", 1:200),
  list = function(org) stop("list must not be reached when link answers"),
  conv = function(org) stop("conv must not be reached when link answers"),
  info = function(org) character(0))
healthy <- probe_kegg_key_form("spo", sources_healthy)
check(identical(healthy$form, "geneid") && identical(healthy$probe, "link"),
      "healthy network: the gene-to-pathway table answers first")

sources_conv_only <- list(
  link = function(org) stop("HTTP 400"),
  list = function(org) stop("HTTP 400"),
  conv = function(org) paste0(org, ":", 1:50),
  info = function(org) character(0))
conv_only <- probe_kegg_key_form("hvg", sources_conv_only)
check(identical(conv_only$form, "geneid") && identical(conv_only$probe, "conv"),
      "link and list fail but conv answers: the key form is still measured")

sources_info_only <- list(
  link = function(org) stop("HTTP 400"),
  list = function(org) stop("HTTP 400"),
  conv = function(org) stop("HTTP 400"),
  info = function(org) character(0))
info_only <- probe_kegg_key_form("hvg", sources_info_only)
check(identical(info_only$form, "unknown") && identical(info_only$probe, "info"),
      "only info answers: the organism is reachable but the key form stays unknown")

# A source that hands back the other side of the pair (conv's NCBI GeneIDs rather than
# the KEGG keys) must not be read as a numeric key list: that would switch the bridge on
# for every locus-tag organism and empty its KEGG tables.
sources_wrong_side <- list(
  link = function(org) stop("HTTP 400"),
  list = function(org) stop("HTTP 400"),
  conv = function(org) paste0("ncbi-geneid:", 23547523:23547572),
  info = function(org) character(0))
wrong_side <- probe_kegg_key_form("fgr", sources_wrong_side)
check(identical(wrong_side$form, "unknown"),
      "negative: a source returning the paired NCBI GeneIDs is not read as the key form")

sources_dead <- lapply(sources_info_only, function(f) function(org) stop("connection refused"))
dead <- probe_kegg_key_form("spo", sources_dead)
check(identical(dead$form, "unknown") && identical(dead$probe, "none"),
      "total outage degrades to unknown without aborting")

## ---- resolve_kegg_key_form: catalogue first, probe only where it can matter ----
declared <- resolve_kegg_key_form("geneid", TRUE,
                                  probe = function() stop("the catalogue must win"))
check(identical(declared$form, "geneid") && identical(declared$source, "catalogue"),
      "a declared catalogue value is used without a KEGG round trip")
check(identical(resolve_kegg_key_form("LOCUS_TAG ", TRUE,
                                      probe = function() stop("unused"))$form, "locus_tag"),
      "the catalogue value is normalised before use")
live <- resolve_kegg_key_form("", TRUE,
                              probe = function() probe_kegg_key_form("spo", sources_healthy))
check(identical(live$form, "geneid") && identical(live$source, "live"),
      "an organism outside the catalogue is measured live")
check(identical(live$form, declared$form),
      "catalogue and live measurement agree for the same organism")
failed <- resolve_kegg_key_form("", TRUE,
                                probe = function() probe_kegg_key_form("spo", sources_dead))
check(identical(failed$form, "unknown") && identical(failed$source, "live probe failed"),
      "a failed probe is recorded as unknown, not guessed")
not_measured <- resolve_kegg_key_form("", FALSE, probe = function() stop("must not probe"))
check(identical(not_measured$form, "unknown") && identical(not_measured$source, "not measured"),
      "no probe is issued on routes where the key form cannot change the query")
# Negative cases: a value that is not an observable key form must not be trusted.
check(identical(resolve_kegg_key_form("ncbi-geneid", FALSE)$source, "not measured"),
      "negative: an enrichKEGG keyType is not an observable key form")
check(identical(resolve_kegg_key_form(NULL, FALSE)$source, "not measured"),
      "negative: a missing catalogue value does not become a key form")
check(all(KEGG_KEY_FORMS_OBSERVABLE %in% c("geneid", "locus_tag")),
      "only geneid and locus_tag are observable key forms")

## ---- format_kegg_key_form: the audit line carries source, probe and the fraction ----
evidence <- format_kegg_key_form(live)
check(grepl("geneid; source=live; probe=link", evidence, fixed = TRUE) &&
      grepl("numeric keys 200/200 (1.0000)", evidence, fixed = TRUE),
      "the evidence line records the measured numeric fraction")
check(identical(format_kegg_key_form(declared),
                "geneid; source=catalogue; probe=not attempted"),
      "a catalogue answer records its source and that no probe ran")

## ---- kegg_route_gap: an empty KEGG table that has a structural cause says so ----
locus_ids <- paste0("SPOM_SPAC212.", 1:20)
gap_unknown <- kegg_route_gap("unknown", locus_ids, TRUE, "DESeq2 engine", "kegg",
                              organism = "spo")
check(!is.null(gap_unknown) && grepl("spo", gap_unknown, fixed = TRUE) &&
      grepl("may be empty", gap_unknown, fixed = TRUE),
      "an unmeasurable key form over non-numeric ids is reported, naming the organism")
gap_route <- kegg_route_gap("geneid", locus_ids, FALSE, "edgeR engine", "kegg",
                            organism = "spo")
check(!is.null(gap_route) && grepl("edgeR engine", gap_route, fixed = TRUE) &&
      grepl("20/20", gap_route, fixed = TRUE) &&
      grepl("will be empty", gap_route, fixed = TRUE),
      "a GeneID-keyed organism with no ncbi_geneid column names the route")
check(is.null(kegg_route_gap("geneid", locus_ids, TRUE, "edgeR engine", "kegg",
                             organism = "spo")),
      "negative: the same ids with a usable ncbi_geneid column report no gap")
check(is.null(kegg_route_gap("locus_tag", locus_ids, FALSE, "edgeR engine", "kegg",
                             organism = "fgr")),
      "negative: a locus-tag organism needs no GeneID column")
check(is.null(kegg_route_gap("geneid", as.character(1:20), FALSE,
                             "count matrix input route", "kegg", organism = "osa")),
      "negative: ids already in bare-GeneID form need no bridge")
check(is.null(kegg_route_gap("unknown", locus_ids, FALSE, "DESeq2 engine", "ncbi-geneid",
                             organism = "spo")),
      "negative: the OrgDb Entrez route is not affected by the raw key form")
check(is.null(kegg_route_gap("unknown", character(0), FALSE, "DESeq2 engine", "kegg",
                             organism = "spo")),
      "negative: no significant genes is not a key-form gap")

## ---- geneid_column_usable: presence is not enough, the values must exist ----
check(isTRUE(geneid_column_usable(c("2541932", NA))), "a column with one usable value counts")
check(!isTRUE(geneid_column_usable(NULL)), "a missing column is not usable")
check(!isTRUE(geneid_column_usable(c(NA_character_, ""))),
      "negative: an all-empty ncbi_geneid column is not usable")

if (!ok) { cat("SOME TESTS FAILED\n"); quit(status = 1) } else cat("ALL TESTS PASSED\n")
