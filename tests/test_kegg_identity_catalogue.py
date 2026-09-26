from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import pytest
import yaml

from _runtime import rscript_runtime

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "workflow" / "scripts" / "run_enrichment.R"
RECORDS = REPO / "workflow" / "scripts" / "kegg_identity_records.tsv"
SNAPSHOT = REPO / "tests" / "fixtures" / "kegg_registry_snapshot.tsv"
CATALOGUE = REPO / "app" / "data" / "reference_catalog.yaml"


def _catalogue_kegg_entries() -> list[dict]:
    data = yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    entries = data["references"] if isinstance(data, dict) else data
    return [e for e in entries if e.get("kegg_organism")]


def _r_runtime(script: Path):
    runtime = rscript_runtime("clusterProfiler")
    if runtime is None:
        pytest.skip("Rscript with clusterProfiler is unavailable for the KEGG identity regression")
    command, convert = runtime
    return command, convert(script), convert


def _records() -> list[dict]:
    lines = [ln for ln in RECORDS.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def test_every_identity_record_matches_its_catalogue_entry():
    by_code = {e["kegg_organism"]: e for e in _catalogue_kegg_entries()}
    for record in _records():
        entry = by_code[record["kegg_code"]]
        assert entry["organism_name"] in record["accepted_names"].split("|"), record
        assert str(entry["taxon_id"]) in record["accepted_taxa"].split("|"), record
        assert record["evidence"].startswith("NCBI Taxonomy "), record
        assert f"gn:{record['kegg_code']}" in record["evidence"], record


def test_every_catalogue_kegg_organism_passes_identity_and_the_gate_still_fails_closed(tmp_path):
    command, _, convert = _r_runtime(SCRIPT)
    catalogue = tmp_path / "catalogue.tsv"
    with catalogue.open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        writer.writerow(["kegg_code", "organism_name", "taxon_id"])
        for entry in _catalogue_kegg_entries():
            writer.writerow([entry["kegg_organism"], entry["organism_name"], entry["taxon_id"]])
    code = f'''
exprs <- parse(file={convert(SCRIPT)!r})
wanted <- c("normalize_species_name", "species_names_share_prefix", "load_kegg_identity_records",
            "match_kegg_identity_record", "validate_kegg_identity")
for (expr in exprs) {{
  if (is.call(expr) && identical(as.character(expr[[1]]), "<-") &&
      as.character(expr[[2]]) %in% wanted) eval(expr, envir=.GlobalEnv)
}}
snap <- read.delim({convert(SNAPSHOT)!r}, comment.char="#", colClasses="character")
registry <- data.frame(kegg.code=snap$kegg_code, kegg.name=snap$registry_name,
                       kegg.taxa=ifelse(snap$offline_taxon == "NA", NA, snap$offline_taxon),
                       stringsAsFactors=FALSE)
genome <- function(code) list(status="PASS", reason="", code=code,
                              taxon=snap$genome_taxon[snap$kegg_code == code], source="snapshot")
records <- load_kegg_identity_records({convert(RECORDS)!r})
cat_tab <- read.delim({convert(catalogue)!r}, colClasses="character")
check <- function(code, name, taxon, recs=records)
  validate_kegg_identity(code, name, taxon, registry, taxon_resolver=genome, records=recs)
failed <- character(0)
for (i in seq_len(nrow(cat_tab))) {{
  r <- check(cat_tab$kegg_code[i], cat_tab$organism_name[i], cat_tab$taxon_id[i])
  if (r$status != "PASS") failed <- c(failed, paste(cat_tab$kegg_code[i], r$reason))
}}
if (length(failed)) stop(paste(c("catalogue organisms failing:", failed), collapse="\\n"))
cat("catalogue PASS", nrow(cat_tab), "\\n")
without <- sum(vapply(seq_len(nrow(cat_tab)), function(i)
  check(cat_tab$kegg_code[i], cat_tab$organism_name[i], cat_tab$taxon_id[i], NULL)$status != "PASS",
  logical(1)))
cat("without records failing", without, "\\n")
negatives <- list(
  wrong_species=check("fox", "Fusarium graminearum PH-1", "5518"),
  epithet_token=check("ztr", "Zymoseptoria triticiae", "1047171"),
  genus_only=check("fgr", "Fusarium", "5518"),
  unrelated_taxon=check("fgr", "Fusarium graminearum PH-1", "9606"),
  wrong_code=check("zzz", "Fusarium graminearum PH-1", "5518"),
  strain_vs_species=check("sce", "Saccharomyces cerevisiae", "559292"),
  mutated_taxon=check("sbi", "Sorghum bicolor", "4559"),
  recorded_name_other_taxon=check("mgr", "Magnaporthe oryzae", "318830"),
  unrecorded_synonym=check("uma", "Ustilago zeae", "237631"))
passed <- names(negatives)[vapply(negatives, function(x) x$status == "PASS", logical(1))]
if (length(passed)) stop(paste("negative controls passed:", paste(passed, collapse=", ")))
drift <- registry; drift$kegg.taxa[drift$kegg.code == "mgr"] <- "242507"
r <- validate_kegg_identity("mgr", "Magnaporthe oryzae", "242507", drift,
                            taxon_resolver=genome, records=records)
if (r$status == "PASS") stop("a recorded name was accepted with an unrecorded registry taxon")
cat("negative controls failed closed\\n")
'''
    harness = tmp_path / "kegg_catalogue_identity.R"
    harness.write_text(code, encoding="utf-8", newline="\n")
    done = subprocess.run([*command, convert(harness)], capture_output=True, text=True,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    total = len(_catalogue_kegg_entries())
    assert f"catalogue PASS {total}" in done.stdout
    # The recorded identities are what rescue the rank and synonym cases: without them
    # exactly the recorded codes fail again.
    assert f"without records failing {len(_records())}" in done.stdout
    assert "negative controls failed closed" in done.stdout
