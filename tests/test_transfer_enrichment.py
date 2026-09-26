from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import random
import subprocess
from pathlib import Path

import pytest

from _runtime import rscript_runtime

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "workflow" / "scripts" / "run_transfer_enrichment.R"
HELPER = REPO / "workflow" / "scripts" / "enrichment_eligibility.R"
TAXON = "9999"
BP = "Biological Process (Gene Ontology)"


def _r_runtime(script: Path):
    runtime = rscript_runtime("clusterProfiler")
    if runtime is None:
        pytest.skip("Rscript with clusterProfiler is unavailable for the annotation-transfer regression")
    command, convert = runtime
    return command, convert(script), convert


def _gz(path: Path, header: str, rows: list[list[str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as fh:
        fh.write(header + "\n")
        for row in rows:
            fh.write("\t".join(row) + "\n")
    manifest = {"url": f"synthetic://{path.name}", "bytes": path.stat().st_size,
                "md5": hashlib.md5(path.read_bytes()).hexdigest(), "retrieved_utc": "2026-09-26T00:00:00Z"}
    Path(str(path) + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _project(root: Path, alias_prefix: str = "G") -> dict:
    """202 tested genes; G201 only has a BLAST alias and G202 an ambiguous one. Set PLANT
    holds 20 of the 30 up-regulated genes; 30 null sets are drawn from genes 21-200."""
    rng = random.Random(7)
    genes = [f"G{i:03d}" for i in range(1, 203)]
    protein = {g: f"{TAXON}.P{g[1:]}" for g in genes[:200]}
    up, down = genes[:30], genes[30:50]
    (root / "results/deseq2").mkdir(parents=True)
    with (root / "results/deseq2/deseq2_results.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gene_id", "symbol", "log2FoldChange", "stat", "pvalue", "padj"])
        for i, g in enumerate(genes):
            stat = 8 - i * 0.05 if g in up else (-6 + (i - 30) * 0.05 if g in down else rng.uniform(-1, 1))
            w.writerow([g, "NA", stat / 4, stat, 0.001 if g in up + down else 0.5, 0.01 if g in up + down else 0.9])
    for name, rows in (("upregulated_genes.csv", up), ("downregulated_genes.csv", down)):
        with (root / "results/deseq2" / name).open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["gene_id", "padj"])
            for g in rows:
                w.writerow([g, 0.01])
    cache = root / "results/enrichment/transfer/string_cache"
    cache.mkdir(parents=True)
    aliases = [[protein[g], g.replace("G", alias_prefix), "Ensembl_gene"] for g in genes[:200]]
    aliases += [[f"{TAXON}.P201", "G201", "BLAST_UniProt_GN"],
                [f"{TAXON}.P202", "G202", "Ensembl_gene"], [f"{TAXON}.P203", "G202", "Ensembl_gene"]]
    _gz(cache / f"{TAXON}.protein.aliases.v12.0.txt.gz", "#string_protein_id\talias\tsource", aliases)
    sets = {"GO:PLANT": up[:20]}
    # The null pool holds the ten up genes outside PLANT, so several sets are tested and
    # the BH adjustment differs from the raw p-value.
    pool = genes[20:200]
    for j in range(30):
        sets[f"GO:NULL{j:02d}"] = rng.sample(pool, 15)
    terms = [[protein[g], BP, term, f"{term} description"] for term, members in sets.items() for g in members]
    terms += [[protein[g], "Local Network Cluster (STRING)", "CL:1", "circular"] for g in up[:15]]
    _gz(cache / f"{TAXON}.protein.enrichment.terms.v12.0.txt.gz",
        "#string_protein_id\tcategory\tterm\tdescription", terms)
    return {"sets": sets, "up": up, "down": down, "universe": genes[:200]}


def _run(tmp_path: Path, project: Path) -> subprocess.CompletedProcess:
    command, script, convert = _r_runtime(SCRIPT)
    out = {k: convert(project / v) for k, v in {
        "ora": "results/enrichment/transfer/transfer_ora.csv", "gsea": "results/enrichment/transfer/transfer_gsea.csv",
        "id_map": "results/enrichment/transfer/transfer_id_map.csv", "summary": "results/enrichment/transfer/transfer_summary.txt",
        "provenance": "results/enrichment/transfer/transfer_provenance.json",
        "objects": "results/enrichment/transfer/transfer_objects.rds", "check": "checks/25_transfer_enrichment_qc.json"}.items()}
    (project / "checks").mkdir(exist_ok=True)
    (project / "logs").mkdir(exist_ok=True)
    rlist = lambda d: "list(" + ", ".join(f"{k} = {v!r}" for k, v in d.items()) + ")"
    code = f'''
suppressMessages(library(clusterProfiler))
source({script!r})
source({convert(HELPER)!r})
setClass("FakeSnakemake", representation(input = "list", output = "list", params = "list"))
snakemake <- new("FakeSnakemake",
  input = list(results = {convert(project / "results/deseq2/deseq2_results.csv")!r},
               up = {convert(project / "results/deseq2/upregulated_genes.csv")!r},
               down = {convert(project / "results/deseq2/downregulated_genes.csv")!r}),
  output = {rlist(out)},
  params = list(taxon = {TAXON!r}, string_version = "12.0", keytype = "", alpha = 0.05,
                cache_dir = {convert(project / "results/enrichment/transfer/string_cache")!r},
                emapper = "", ko_table = "", annotation = ""))
main()
cat("main done\\n")
'''
    harness = tmp_path / f"{project.name}.R"
    harness.write_text(code, encoding="utf-8", newline="\n")
    return subprocess.run([*command, convert(harness)], capture_output=True, text=True, timeout=300, check=False)


def _hyper_upper(k: int, M: int, N: int, n: int) -> float:
    return sum(math.comb(M, i) * math.comb(N - M, n - i) for i in range(k, min(M, n) + 1)) / math.comb(N, n)


def _bh(p: dict[str, float]) -> dict[str, float]:
    order = sorted(p, key=p.get)
    m = len(order)
    adj, running = {}, 1.0
    for rank in range(m, 0, -1):
        term = order[rank - 1]
        running = min(running, p[term] * m / rank)
        adj[term] = running
    return adj


def test_transfer_route_recovers_a_planted_set_and_matches_an_independent_hypergeometric(tmp_path):
    project = tmp_path / "match"
    truth = _project(project)
    done = _run(tmp_path, project)
    assert done.returncode == 0, done.stdout + done.stderr
    check = json.loads((project / "checks/25_transfer_enrichment_qc.json").read_text(encoding="utf-8"))
    assert check["status"] == "PASS", check
    assert "200 of 202 tested genes" in check["messages"][0]["message"]
    assert "1 ambiguous gene ids excluded" in check["messages"][0]["message"]
    rows = list(csv.DictReader((project / "results/enrichment/transfer/transfer_ora.csv").open(encoding="utf-8")))
    assert rows, "planted set not recovered"
    assert {r["category"] for r in rows} == {"GO BP"}, "only the configured categories may appear"
    assert not any(r["ID"].startswith("GO:NULL") for r in rows), "a null set met the criterion"
    up_rows = {r["ID"]: r for r in rows if r["foreground"] == "up"}
    assert "GO:PLANT" in up_rows
    assert up_rows["GO:PLANT"]["geneID"].split("/")[0].startswith("G"), "gene ids, not protein ids, in geneID"

    # Independent oracle: exact hypergeometric upper tail and BH over the sets the
    # foreground touches, in protein space after the size filter.
    universe = set(truth["universe"])
    annotated = set().union(*truth["sets"].values()) & universe
    fg = set(truth["up"]) & annotated
    pvalues = {}
    for term, members in truth["sets"].items():
        members = set(members) & universe
        k = len(members & fg)
        if k and 10 <= len(members) <= 500:
            pvalues[term] = _hyper_upper(k, len(members), len(annotated), len(fg))
    adjusted = _bh(pvalues)
    assert len(pvalues) > 1 and adjusted["GO:PLANT"] > pvalues["GO:PLANT"] * 1.5
    assert math.isclose(float(up_rows["GO:PLANT"]["pvalue"]), pvalues["GO:PLANT"], rel_tol=1e-9)
    assert math.isclose(float(up_rows["GO:PLANT"]["p.adjust"]), adjusted["GO:PLANT"], rel_tol=1e-9)
    provenance = json.loads((project / "results/enrichment/transfer/transfer_provenance.json").read_text(encoding="utf-8"))
    assert provenance["string"]["mapped_genes"] == 200
    assert "Local Network Cluster (STRING)" in provenance["string"]["excluded_categories"]


def test_a_wrong_organism_alias_file_fails_the_mapping_gate(tmp_path):
    project = tmp_path / "wrong"
    _project(project, alias_prefix="Y")  # aliases of another organism's genes
    done = _run(tmp_path, project)
    assert done.returncode == 0, done.stdout + done.stderr
    check = json.loads((project / "checks/25_transfer_enrichment_qc.json").read_text(encoding="utf-8"))
    assert check["status"] == "REVIEW_REQUIRED", check
    assert "0 of 202 tested genes" in check["messages"][0]["message"]


def test_parsers_protein_bridge_and_protein_rank(tmp_path):
    command, script, convert = _r_runtime(SCRIPT)
    emapper = tmp_path / "x.emapper.annotations"
    emapper.write_text(
        "## emapper-2.1.12\n## command: emapper.py\n"
        "#query\tseed_ortholog\tevalue\tscore\teggNOG_OGs\tmax_annot_lvl\tCOG_category\tDescription\tPreferred_name\tGOs\tEC\tKEGG_ko\tKEGG_Pathway\n"
        "XP_1.1\ts\t1e-50\t200\tOG\tlvl\tC\tdesc\tname\tGO:0006096,GO:0005737\t-\tko:K00844,ko:K01810\tmap00010\n"
        "XP_2.1\ts\t1e-40\t100\tOG\tlvl\tC\tdesc\tname\t-\t-\t-\t-\n"
        "## 2 queries scanned\n", encoding="utf-8")
    kofam = tmp_path / "kofam.txt"
    kofam.write_text("# gene name   KO     thrshld  score  E-value KO definition\n"
                     "#----------- ------ ------- ------ ------- -------------\n"
                     "* XP_1.1      K00844  100.0  300.0  1e-90  hexokinase\n"
                     "  XP_1.1      K99999  500.0   10.0  1       below threshold\n"
                     "  XP_3.1      K00001  400.0   20.0  1       below threshold\n", encoding="utf-8")
    mapper = tmp_path / "mapper.txt"
    mapper.write_text("G1\tK00844\tK01810\nG2\n", encoding="utf-8")
    gtf = tmp_path / "a.gtf"
    gtf.write_text('chr1\tRefSeq\tCDS\t1\t9\t.\t+\t0\tgene_id "G1"; transcript_id "T1"; protein_id "XP_1.1";\n'
                   'chr1\tRefSeq\tCDS\t20\t29\t.\t+\t0\tgene_id "G3"; transcript_id "T3"; protein_id "XP_3.1";\n',
                   encoding="utf-8")
    code = f'''
source({script!r})
e <- read_emapper({convert(emapper)!r})
stopifnot(identical(sort(e$go$value), c("GO:0005737", "GO:0006096")), identical(sort(e$ko$value), c("K00844", "K01810")),
          identical(e$queries, c("XP_1.1", "XP_2.1")))
k <- read_ko_table({convert(kofam)!r})
stopifnot(nrow(k$ko) == 1L, k$ko$query == "XP_1.1", k$ko$value == "K00844", setequal(k$queries, c("XP_1.1", "XP_3.1")))
m <- read_ko_table({convert(mapper)!r})
stopifnot(nrow(m$ko) == 2L, setequal(m$queries, c("G1", "G2")))
b <- protein_gene_bridge({convert(gtf)!r})
r <- resolve_import_queries(c("G1", "XP_3", "XP_9.1"), c("G1", "G3"), b)
stopifnot(identical(r$gene_id[order(r$query)], c("G1", "G3")))
rank <- build_protein_rank(c("P2", "P1", "P1", "P3", "P3", NA), c(1, 3, 1, 2, -2, 9))
stopifnot(identical(names(rank$values), c("P1", "P2")), rank$values[["P1"]] == 2, rank$conflicts == 1L)
tie <- build_protein_rank(c("Pb", "Pa"), c(1, 1))
stopifnot(identical(names(tie$values), c("Pa", "Pb")))
lookup <- build_alias_lookup(data.frame(protein = c("P1", "P2", "P3", "P4"), alias = c("a", "b", "b", "c"),
                                        source = c("Ensembl", "Ensembl", "Ensembl", "BLAST_KEGG")))
stopifnot(identical(unname(lookup$unique["a"]), "P1"), "b" %in% lookup$ambiguous, !"c" %in% names(lookup$unique))
res <- data.frame(gene_id = c("LOC101", "X1", "X2"), ncbi_geneid = c("101", "7", NA), symbol = c(NA, NA, "a"))
mp <- map_genes_to_proteins(res, list(unique = c("101" = "P9", "a" = "P1"), ambiguous = "X1"), "")
stopifnot(identical(mp$protein, c("P9", NA, "P1")), identical(mp$ambiguous, c(FALSE, TRUE, FALSE)))
cat("parsers OK\\n")
'''
    harness = tmp_path / "parsers.R"
    harness.write_text(code, encoding="utf-8", newline="\n")
    done = subprocess.run([*command, convert(harness)], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "parsers OK" in done.stdout
