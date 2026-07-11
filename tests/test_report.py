from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "make_html_report", Path(__file__).resolve().parent.parent / "workflow" / "scripts" / "make_html_report.py")
mh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mh)


def test_sym_or_blank_normalizes_na_tokens():
    for na in ("NA", "nan", "NaN", "N/A", "null", "None", ".", "", "  "):
        assert mh._sym_or_blank(na) == "", na
    for real in ("FKBP5", " KLF15 ", "FGSG_03153"):
        assert mh._sym_or_blank(real) == real.strip()


def test_de_table_renders_blank_not_literal_na_for_missing_symbol():
    d = Path(tempfile.mkdtemp())
    csv = d / "upregulated_genes.csv"
    csv.write_text("gene_id,symbol,log2FoldChange,padj,baseMean\n"
                   "FGSG_03153,NA,2.9,1e-8,1200\n"
                   "FGSG_00001,,1.5,1e-4,300\n")
    html = mh._de_table(csv, top=25)
    assert "<i>NA</i>" not in html          # the literal NA is never shown as a gene symbol
    assert "FGSG_03153" in html             # the gene_id column is still present


def test_enrichment_section_distinguishes_go_skipped_from_ran_empty():
    d = Path(tempfile.mkdtemp())
    enr = d / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "enrichment_summary.txt").write_text(
        "GO/disease enrichment: skipped (no Bioconductor OrgDb for this organism)\nKEGG: ran")
    for f in ("go_ora_up.csv", "go_ora_down.csv", "gsea.csv"):
        (enr / f).write_text("ID,Description,p.adjust\n")            # present but empty (skipped)
    (enr / "kegg_ora.csv").write_text(
        "ID,Description,p.adjust,GeneRatio\nko03030,DNA replication,0.001,5/50\n")
    (enr / "kegg_gsea.csv").write_text("ID,Description,p.adjust\n")  # ran, nothing significant
    html = mh._enrichment_section(d)
    assert "not run for this organism" in html                       # GO skip is labelled honestly
    assert "DNA replication" in html                                 # KEGG real term rendered
    assert "No terms passed the significance threshold" in html      # KEGG ran-empty wording kept


def test_enrichment_section_fully_skipped_says_not_run():
    d = Path(tempfile.mkdtemp())
    enr = d / "results" / "enrichment"
    enr.mkdir(parents=True)
    (enr / "enrichment_summary.txt").write_text("Enrichment skipped: no Bioconductor OrgDb for this organism")
    html = mh._enrichment_section(d)
    # No blocks at all -> honest "not run" message, not "nothing passed the threshold".
    assert "was not run for this organism" in html


def test_microarray_tech_captions_rewrite_count_wording():
    # Microarray (limma on intensities) has no counts / VST / LFC shrinkage; the shared figure tech
    # captions must be rewritten so they don't assert a DESeq2/count pipeline that never ran.
    assert "array intensity" in mh._micro_tech("Principal components of variance-stabilised counts")
    out = mh._micro_tech("x: mean normalised counts (log); y: shrunken log2 fold change.")
    assert "mean log2 expression" in out and "unshrunken" in out
    assert "variance-stabilised counts" not in out and "shrunken log2 fold change" not in out
    # a caption with no count wording is unchanged
    assert mh._micro_tech("Distribution of raw p-values.") == "Distribution of raw p-values."
