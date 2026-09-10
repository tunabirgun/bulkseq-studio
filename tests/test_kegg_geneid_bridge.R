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

extract_function("workflow/scripts/run_deseq2.R", "annotate_from_gtf")
extract_function("workflow/scripts/run_enrichment.R", "bridge_kegg_geneid")
extract_function("workflow/scripts/run_enrichment.R", "kegg_geneid_lookup_for")

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

if (!ok) { cat("SOME TESTS FAILED\n"); quit(status = 1) } else cat("ALL TESTS PASSED\n")
