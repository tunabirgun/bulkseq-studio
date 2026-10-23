from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from test_report_presentation import GENERATOR, load_generator, write_fixture


def write_checks(project: Path, status: str, message: str = "Synthetic check message") -> None:
    checks = project / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "sanity_checks.txt").write_text(
        f"Overall: {status}\n09_deseq2_qc: {status}\n  - {status}: {message}\n", encoding="utf-8")


@pytest.mark.parametrize("status,title", [
    ("PASS", "Recorded checks passed"),
    ("WARNING", "Advisory checks need review"),
    ("REVIEW_REQUIRED", "Review required"),
    ("FAIL", "Failed checks need attention"),
])
def test_check_outcome_precedes_findings_and_preserves_messages(tmp_path, status, title):
    write_fixture(tmp_path, "counts")
    write_checks(tmp_path, status, "Review <synthetic> evidence")
    rendered = load_generator().build(tmp_path)
    assert rendered.index(f"<h2 id='outcome-title'>{title}</h2>") < rendered.index('<h2>Result summary</h2>')
    assert "Review &lt;synthetic&gt; evidence" in rendered
    assert "results are usable" not in rendered
    if status in ("FAIL", "REVIEW_REQUIRED"):
        assert "their presence does not establish a valid run" in rendered


def test_unavailable_and_empty_results_remain_distinct(tmp_path):
    module = load_generator()
    missing = tmp_path / "missing"
    missing.mkdir()
    rendered = module.build(missing)
    assert "Check evidence unavailable" in rendered
    assert "missing output is not a zero-result finding" in rendered
    assert "<b>no</b> genes met" not in rendered
    empty = tmp_path / "empty"
    write_fixture(empty, "counts")
    write_checks(empty, "PASS")
    for name in ("deseq2_results", "upregulated_genes", "downregulated_genes"):
        (empty / f"results/deseq2/{name}.csv").write_text("gene_id,log2FoldChange,padj\n", encoding="utf-8")
    rendered = module.build(empty)
    assert "<b>no</b> genes met" in rendered
    assert "missing output is not a zero-result finding" not in rendered


def test_imported_settings_keep_source_method_and_unconfirmed_direction(tmp_path):
    run = write_fixture(tmp_path, "imported")
    run["input"]["deseq2_results_direction"]["confirmed"] = False
    (tmp_path / "results/reports/run_summary.json").write_text(json.dumps(run), encoding="utf-8")
    rendered = load_generator().build(tmp_path)
    settings = rendered.split("<section id='run-settings'>", 1)[1].split("</section>", 1)[0]
    assert "Group orientation not recorded or not confirmed" in settings
    assert "<dt>Upstream DE method</dt><dd>limma</dd>" in settings
    assert "<dt>Upstream p-adjustment</dt><dd>Holm</dd>" in settings
    assert "~ condition" not in rendered


def test_complete_check_messages_and_saved_output_links_are_reachable(tmp_path):
    write_fixture(tmp_path, "counts")
    write_checks(tmp_path, "WARNING", "First message")
    check_path = tmp_path / "checks/sanity_checks.txt"
    check_path.write_text(check_path.read_text(encoding="utf-8") + "\n" * 220 + "  - WARNING: Final evidence message\n", encoding="utf-8")
    path = tmp_path / "results/enrichment/set #1.csv"
    path.write_text("ID,p.adjust\n", encoding="utf-8")
    rendered = load_generator().build(tmp_path)
    assert "Final evidence message" in rendered
    assert "../../results/enrichment/set%20%231.csv" in rendered
    assert "../../results/deseq2/deseq2_results.csv" in rendered
    assert "Links to complete saved outputs require the project files" in rendered
    assert "tabindex='0' role='region' aria-label='Scrollable table'" in rendered


@pytest.mark.parametrize("meta", [False, True])
def test_main_and_cross_study_reports_have_complete_navigation(tmp_path, meta):
    write_fixture(tmp_path, "counts")
    module = load_generator(GENERATOR.with_name("make_meta_report.py") if meta else GENERATOR)
    rendered = module.build(tmp_path)
    ids = re.findall(r"\bid=['\"]([^'\"]+)", rendered)
    targets = re.findall(r"href=['\"]#([^'\"]+)", rendered)
    assert len(ids) == len(set(ids))
    assert set(targets) <= set(ids)
    assert len(re.findall(r"<h1[ >]", rendered)) == 1
    assert '<main id="main-content" tabindex="-1">' in rendered
    assert '<a class="skip" href="#main-content">' in rendered
    assert '<nav class="report-nav" aria-label="Report sections">' in rendered
    assert 'href="#outcome"' in rendered
    assert 'href="#outputs"' in rendered
    assert "target.tagName==='DETAILS'" in rendered


def test_meta_check_failure_remains_visible_with_populated_summary(tmp_path):
    write_fixture(tmp_path, "counts")
    (tmp_path / "results/reports/meta_analysis_summary.json").write_text(json.dumps({
        "n_shared_genes": 2, "n_studies": 2, "n_meta_sig": 1,
    }), encoding="utf-8")
    (tmp_path / "checks/17_meta_analysis_qc.json").write_text(json.dumps({
        "check": "17_meta_analysis_qc", "status": "FAIL",
        "messages": [{"status": "FAIL", "message": "Synthetic fit failure <details>"}],
    }), encoding="utf-8")
    rendered = load_generator(GENERATOR.with_name("make_meta_report.py")).build(tmp_path)
    assert "Synthetic fit failure &lt;details&gt;" in rendered
    assert rendered.index("Synthetic fit failure &lt;details&gt;") < rendered.index("<h2>Cross-study result summary</h2>")


@pytest.mark.parametrize("content", [
    "", "garbage\n", "gene_id,log2FoldChange\n", "gene_id,log2FoldChange,padj\ng1,2\n",
    "gene_id,log2FoldChange,padj\ng1,invalid,0.01\n",
    "gene_id,log2FoldChange,padj\ng1,2,invalid\n",
    "gene_id,log2FoldChange,padj\ng1,2,0.01,extra\n",
    'gene_id,log2FoldChange,padj\n"g1,2,0.01\n',
    "gene_id,log2FoldChange,padj,padj\ng1,2,0.01,0.02\n",
    "gene_id,log2FoldChange,padj,baseMean\ng1,2,0.01,invalid\n",
    "gene_id,log2FoldChange,padj\ng1,2,Infinity\n",
])
def test_malformed_de_files_never_become_empty_findings(tmp_path, content):
    write_fixture(tmp_path, "counts")
    write_checks(tmp_path, "PASS")
    module = load_generator()
    deseq = tmp_path / "results/deseq2"
    for name in ("deseq2_results", "upregulated_genes", "downregulated_genes"):
        (deseq / f"{name}.csv").write_text(content, encoding="utf-8")
    rendered = module.build(tmp_path)
    assert "differential-expression results are not available" in rendered
    assert module.res_has_de(tmp_path) is False
    assert module._count_csv_rows(deseq / "upregulated_genes.csv") is None
    assert module._de_headline_stats(tmp_path, 0.05, 1)[:3] == (None, None, None)
    assert "unavailable" in module._de_table(deseq / "upregulated_genes.csv").lower()
    assert "<b>no</b> genes met" not in rendered
    assert "No up-regulated genes passed" not in rendered
    assert "No down-regulated genes passed" not in rendered
    assert "0 up · 0 down" not in rendered


def test_partial_directional_output_does_not_invent_zero_counts(tmp_path):
    write_fixture(tmp_path, "counts")
    deseq = tmp_path / "results/deseq2"
    (deseq / "deseq2_results.csv").unlink()
    (deseq / "downregulated_genes.csv").unlink()
    module = load_generator()
    assert module._de_headline_stats(tmp_path, 0.05, 1)[:3] == (2, None, None)
    assert "unavailable" in module._de_table(deseq / "downregulated_genes.csv").lower()
    rendered = module.build(tmp_path)
    assert "No down-regulated genes passed" not in rendered
    assert "2 up · unavailable down" in rendered
    assert "0</b> clearly" not in rendered
    assert "differential-expression results are not available" in rendered


def test_valid_na_statistics_and_header_only_directional_tables_are_preserved(tmp_path):
    write_fixture(tmp_path, "counts")
    deseq = tmp_path / "results/deseq2"
    (deseq / "deseq2_results.csv").write_text(
        "gene_id,log2FoldChange,padj,baseMean\ng1,NA,NA,NA\ng2,1.2,NaN,0\n", encoding="utf-8")
    for name in ("upregulated_genes", "downregulated_genes"):
        (deseq / f"{name}.csv").write_text("gene_id,log2FoldChange,padj\n", encoding="utf-8")
    module = load_generator()
    assert module.res_has_de(tmp_path) is True
    assert module._de_headline_stats(tmp_path, 0.05, 1)[:3] == (0, 0, 0)
    assert ">g1</td>" in module._de_table(deseq / "deseq2_results.csv")
    rendered = module.build(tmp_path)
    assert "<b>no</b> genes met" in rendered
    assert "No up-regulated genes passed" in rendered
    assert "No down-regulated genes passed" in rendered


@pytest.mark.parametrize("aggregate,component", [("FAIL", "PASS"), ("PASS", "FAIL"), ("REVIEW_REQUIRED", "WARNING")])
def test_aggregate_component_disagreement_is_explicit_in_every_status_view(tmp_path, aggregate, component):
    write_fixture(tmp_path, "counts")
    text = f"Overall: {aggregate}\n01_input_validation: {component}\n  - {component}: Synthetic check\n"
    (tmp_path / "checks/sanity_checks.txt").write_text(text, encoding="utf-8")
    module = load_generator()
    for output in (module.build(tmp_path), module._status_sentence(text), module._sanity_section(text)):
        assert "inconsistent" in output.lower()
        assert f"aggregate records {aggregate}" in output
        assert f"listed checks resolve to {component}" in output
        assert "All 1" not in output
        assert "1 recorded automated quality check passed" not in output


def test_single_pass_message_and_navigation_labels_are_concise(tmp_path):
    write_fixture(tmp_path, "counts")
    write_checks(tmp_path, "PASS")
    module = load_generator()
    rendered = module.build(tmp_path)
    assert "1 recorded automated quality check passed." in rendered
    assert '<a href="#outcome">Run status</a>' in rendered
    meta = load_generator(GENERATOR.with_name("make_meta_report.py")).build(tmp_path)
    assert "<h1>Cross-study analysis</h1>" in meta
    assert "Comparison orientation not recorded" in meta
    assert '<a href="#comparison">Study scope</a>' in meta


def test_corruption_beyond_display_limit_and_optional_gene_table_is_unavailable(tmp_path):
    write_fixture(tmp_path, "counts")
    table = tmp_path / "results/genes_of_interest/goi_deseq2_results.csv"
    table.parent.mkdir()
    table.write_text("gene_id,log2FoldChange,padj\ng1,2,0.01\ng2,3\n", encoding="utf-8")
    module = load_generator()
    assert "table unavailable" in module._de_table(table, top=1)
    rendered = module.build(tmp_path)
    assert "goi_deseq2_results.csv</code> is missing, unreadable, or malformed" in rendered
    assert "No genes-of-interest" not in rendered


def test_unreadable_encoding_is_unavailable_not_empty(tmp_path):
    write_fixture(tmp_path, "counts")
    table = tmp_path / "results/deseq2/downregulated_genes.csv"
    table.write_bytes(b"gene_id,log2FoldChange,padj\n\xff,2,0.01\n")
    module = load_generator()
    assert module._count_csv_rows(table) is None
    rendered = module.build(tmp_path)
    assert "downregulated_genes.csv</code> is missing, unreadable, or malformed" in rendered
    assert "No down-regulated genes passed" not in rendered
