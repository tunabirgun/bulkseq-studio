from __future__ import annotations

import csv
import html
import importlib.util
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

GENERATOR = Path(__file__).resolve().parents[1] / "workflow/scripts/make_html_report.py"
ROUTES = ("counts", "alternative", "microarray", "imported", "empty")
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="600">'
       '<rect width="1200" height="600" fill="white"/>'
       '<path d="M80 500H1120M80 500V80" stroke="black" fill="none"/>'
       '<circle cx="600" cy="300" r="28" fill="#2c6fb6"/>'
       '<text x="100" y="50" font-family="Times New Roman" fill="black">Synthetic fixture</text></svg>')


def load_generator(path=GENERATOR):
    spec = importlib.util.spec_from_file_location("report_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_fixture(project: Path, route: str) -> dict:
    project.mkdir(parents=True, exist_ok=True)
    if route == "empty":
        return {}
    run = {
        "app_version": "0.30.1", "workflow_version": "0.30.1",
        "workflow_digest": "d" * 64, "workflow_copied_at": "2026-09-01T12:00:00Z",
        "run_date": "2026-09-13T12:00:00Z", "workflow_git_commit": "a" * 40,
        "environment_spec": {"source": "fixture", "file": "environment.yml", "sha256": "c" * 64},
        "environment_lock_md5": "b" * 32,
        "input": {"type": "count_matrix", "samples": "config/samples.tsv"},
        "workflow": {"de_engine": "DESeq2", "aligner": "STAR", "quantifier": "featureCounts"},
        "deseq2": {"design_formula": "~ condition", "alpha": 0.05, "lfc_threshold": 1,
                   "contrasts": [{"numerator": "stimulated", "denominator": "baseline"}]},
        "reference": {"organism": "Synthetic fixture"},
        "software_versions": {"python": "3.12", "STAR": "2.7", "snakemake": "8"},
        "r_packages": {"DESeq2": "1.0", "limma": "1.0", "apeglm": "1.0", "clusterProfiler": "4.0"},
        "session_info": {"lfc_threshold_test": "greaterAbs (H0 |log2FC| <= 1)"},
    }
    if route == "alternative":
        run["workflow"]["de_engine"] = "limma-voom"
        run["session_info"]["lfc_threshold_test"] = "treat (H0 |log2FC| <= 1)"
    elif route == "microarray":
        run["input"]["type"] = "microarray"
    elif route == "imported":
        run["input"].update({
            "type": "deseq2_results", "deseq2_results": "config/results.csv",
            "deseq2_results_direction": {"numerator": "stimulated", "denominator": "baseline", "confirmed": True},
            "deseq2_results_provenance": {"original_basename": "synthetic.tsv", "project_copy": "config/results.csv",
                "sha256": "e" * 64, "byte_size": 456, "row_count": 4,
                "upstream_method": "limma", "lfc_shrinkage": "not_applied", "p_adjustment_method": "Holm"},
        })
    for folder in ("config", "checks", "results/reports", "results/figures", "results/deseq2", "results/enrichment"):
        (project / folder).mkdir(parents=True, exist_ok=True)
    (project / "config/samples.tsv").write_text("sample_id\tcondition\na\tbaseline\nb\tbaseline\nc\tstimulated\nd\tstimulated\n", encoding="utf-8")
    (project / "results/reports/run_summary.json").write_text(json.dumps(run), encoding="utf-8")
    timing = {"wall_clock_approx_hms": "00:00:10", "cumulative_job_hms": "00:00:12",
              "configured_resources": {"snakemake_cores": 2, "memory_gb": 4},
              "per_phase_seconds": {"model": 6, "enrichment": 4},
              "per_step_seconds": {"model": 6, "enrichment": 4, "report": 2},
              "timing_scope": "Synthetic timing fixture; no workflow was executed."}
    (project / "results/reports/timing_summary.json").write_text(json.dumps(timing), encoding="utf-8")
    (project / "checks/sanity_checks.txt").write_text("Overall: WARN\n[WARN] fixture: Synthetic display check <script>unsafe</script>\n", encoding="utf-8")
    rows = [["feature-c", "C", 2, 0.03, 1000.25], ["feature-a", "A", 10, 0.0001, 25.5],
            ["feature-b", "B", -3, 0.001, 600.75], ["feature-d", '<img src=x onerror="alert(1)">', -2, 0.02, 0.125]]
    for name, selected in (("deseq2_results", rows), ("upregulated_genes", rows[:2]), ("downregulated_genes", rows[2:])):
        with (project / f"results/deseq2/{name}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["gene_id", "symbol", "log2FoldChange", "padj", "baseMean"])
            writer.writerows(selected)
    for name in ("volcano", "ma_plot", "pca", "top_deg_heatmap", "ppi_network"):
        (project / f"results/figures/{name}.svg").write_text(SVG, encoding="utf-8")
    (project / "results/enrichment/go_ora_all.csv").write_text("ID,Description,p.adjust,GeneRatio,Count\nterm-a,Synthetic term,0.002,2/4,2\n", encoding="utf-8")
    return run


class TableContent(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tables, self.table, self.cell = [], None, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
        elif tag in ("td", "th") and self.table is not None:
            self.cell = [tag, dict(attrs).get("data-sort-value"), ""]

    def handle_data(self, data):
        if self.cell is not None:
            self.cell[2] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.cell[2] = " ".join(self.cell[2].split())
            self.table.append(tuple(self.cell))
            self.cell = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


@pytest.mark.parametrize("route", ROUTES)
def test_report_preserves_sort_values_and_route_provenance(tmp_path, route):
    module = load_generator()
    run = write_fixture(tmp_path, route)
    rendered = module.build(tmp_path)
    cells = [cell for table in TableContent(rendered).tables for cell in table]
    if route != "empty":
        assert {cell[1] for cell in cells if cell[1]} >= {"1000.25", "25.5", "600.75", "0.125"}
        for label, value in module._provenance_rows(run):
            assert any(cell[2] == label for cell in cells)
            assert any(cell[2] == value for cell in cells)
    assert '<img src=x onerror=' not in rendered
    if route in ("counts", "alternative"):
        assert "not used for count-matrix input" in rendered
    if route == "alternative":
        assert module._assay_kind(run) == "log2_cpm"
        assert "log2 CPM" in rendered
    if route == "imported":
        for stale in ('data-figure="pca"', 'data-figure="top_deg_heatmap"', "~ condition", "<td>DESeq2</td>", "<td>STAR</td>"):
            assert stale not in rendered
        assert "Holm" in rendered and "e" * 64 in rendered


@pytest.mark.parametrize("route", ROUTES)
def test_report_navigation_targets_exist(tmp_path, route):
    write_fixture(tmp_path, route)
    rendered = load_generator().build(tmp_path)
    ids = re.findall(r'\bid=[\'"]([^\'"]+)', rendered)
    targets = re.findall(r'href=[\'"]#([^\'"]+)', rendered)
    assert len(ids) == len(set(ids))
    assert targets and set(targets) <= set(ids)


@pytest.mark.parametrize("route", ROUTES)
def test_recorded_fold_change_threshold_test_is_disclosed_on_local_model_routes(tmp_path, route):
    module = load_generator()
    run = write_fixture(tmp_path, route)
    rendered = module.build(tmp_path)
    if route == "empty":
        return
    recorded = html.escape(run["session_info"]["lfc_threshold_test"])
    if route == "imported":
        assert recorded not in rendered
        return
    assert f"{recorded} · companion column {module.LFC_COMPANION_COLUMN}" in rendered
    assert "Fold-change threshold test" in rendered


def test_report_omits_the_threshold_test_card_when_no_engine_recorded_one(tmp_path):
    module = load_generator()
    write_fixture(tmp_path, "counts")
    summary = tmp_path / "results/reports/run_summary.json"
    run = json.loads(summary.read_text(encoding="utf-8"))
    run["session_info"].pop("lfc_threshold_test")
    summary.write_text(json.dumps(run), encoding="utf-8")
    assert "Fold-change threshold test" not in module.build(tmp_path)


def test_companion_column_name_matches_every_engine():
    module = load_generator()
    scripts = GENERATOR.parent
    engines = sorted(scripts.glob("run_*.R"))
    threshold_columns = {}
    for engine in engines:
        assigned = re.findall(r"res_out\$([A-Za-z0-9_.]+)\s*<-", engine.read_text(encoding="utf-8"))
        named = {c for c in assigned if "lfc" in c.lower() and "threshold" in c.lower()}
        if named:
            threshold_columns[engine.name] = named
    assert len(threshold_columns) == 4, threshold_columns
    assert set.union(*threshold_columns.values()) == {module.LFC_COMPANION_COLUMN}
    summary = (scripts / "make_run_summary.py").read_text(encoding="utf-8")
    assert f"companion column {module.LFC_COMPANION_COLUMN}" in summary


def test_footer_and_header_name_the_executed_application_version(tmp_path):
    module = load_generator()
    write_fixture(tmp_path, "counts")
    summary = tmp_path / "results/reports/run_summary.json"
    run = json.loads(summary.read_text(encoding="utf-8"))
    run["app_version"] = "9.9.9-executed"
    run["project_created_app_version"] = "0.1.0-created"
    summary.write_text(json.dumps(run), encoding="utf-8")
    rendered = module.build(tmp_path)
    assert "Generated by BulkSeq Studio v9.9.9-executed" in rendered
    assert ">v9.9.9-executed<" in rendered
    assert "Generated by BulkSeq Studio v0.1.0-created" not in rendered


def test_table_content_comparison_detects_value_and_sort_changes():
    original = "<table><tr><td data-sort-value='1000.25'>1,000</td></tr></table>"
    assert TableContent(original).tables != TableContent(original.replace("1,000", "999")).tables
    assert TableContent(original).tables != TableContent(original.replace("1000.25", "1000.5")).tables


def stylesheet(rendered: str) -> str:
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", rendered, re.S))


def css_rules(css: str):
    """(selector list, declarations, at-rule or None, source order) for a flat stylesheet."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, at_rule, depth, head, i = [], None, 0, "", 0
    while i < len(css):
        char = css[i]
        if char == "{":
            selector, head = head.strip(), ""
            if selector.startswith("@"):
                at_rule, depth = selector, depth + 1
            else:
                end = css.index("}", i)
                rules.append((selector, css[i + 1:end], at_rule, len(rules)))
                i = end + 1
                continue
        elif char == "}":
            if depth:
                depth -= 1
                at_rule = at_rule if depth else None
            head = ""
        else:
            head += char
        i += 1
    return rules


def specificity(selector: str) -> tuple[int, int, int]:
    return (selector.count("#"),
            len(re.findall(r"\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+", selector)),
            len(re.findall(r"(?:^|[\s>+~])[a-zA-Z][\w-]*", selector)))


def selector_matches(selector: str, chain) -> bool:
    """Descendant-combinator match of `selector` against a [(tag, classes), ...] ancestry."""
    parts = selector.split()

    def compound(part, node) -> bool:
        tag, classes = node
        name = re.match(r"[a-zA-Z][\w-]*", part)
        if name and name.group(0) != tag:
            return False
        return {c[1:] for c in re.findall(r"\.[\w-]+", part)} <= classes

    def ancestors(part_i, node_i) -> bool:
        if part_i < 0:
            return True
        while node_i >= 0:
            if compound(parts[part_i], chain[node_i]) and ancestors(part_i - 1, node_i - 1):
                return True
            node_i -= 1
        return False

    return compound(parts[-1], chain[-1]) and ancestors(len(parts) - 2, len(chain) - 2)


def winning_value(css: str, prop: str, chain, condition: str | None = None):
    """The declaration the cascade leaves in force for an element with this ancestry."""
    best, value = None, None
    for selector, block, at_rule, order in css_rules(css):
        if at_rule and (condition is None or condition not in at_rule.replace(" ", "")):
            continue
        matching = [s.strip() for s in selector.split(",") if selector_matches(s.strip(), chain)]
        if not matching:
            continue
        declared = re.findall(rf"(?:^|;)\s*{prop}\s*:\s*([^;]+)", block)
        if not declared:
            continue
        weight = (max(specificity(s) for s in matching), order)
        if best is None or weight > best:
            best, value = weight, declared[-1].strip().replace("!important", "").strip()
    return value


class CaptionBlocks(HTMLParser):
    VOID = {"br", "img", "hr", "input", "source"}

    def __init__(self, text):
        super().__init__()
        self.depth, self.classes = 0, set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "figcaption":
            self.depth = 1
            return
        if self.depth == 1:
            self.classes.update((dict(attrs).get("class") or "").split())
        if self.depth and tag not in self.VOID:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1


DATA_CELL = [("table", {"data"}), ("td", set())]
MONO_CELL = [("table", {"data"}), ("td", {"mono"})]
PROVENANCE_CELL = [("div", {"vgrid"}), ("div", {"vcol"}), ("div", {"tablewrap"}),
                   ("table", {"data"}), ("td", {"mono"})]
PHASE_LABEL = [("div", {"bars"}), ("div", {"barrow"}), ("div", {"barlab"})]


def test_provenance_values_wrap_instead_of_being_clipped(tmp_path):
    module = load_generator()
    run = write_fixture(tmp_path, "counts")
    rendered = module.build(tmp_path)
    for label, value in module._provenance_rows(run):
        assert f"<td class='mono'>{html.escape(value)}</td>" in rendered, label
    css = stylesheet(rendered)
    assert winning_value(css, "white-space", DATA_CELL) == "nowrap"
    assert winning_value(css, "white-space", MONO_CELL) == "normal"
    for chain in (PROVENANCE_CELL, PROVENANCE_CELL[:-1] + [("td", set())]):
        assert winning_value(css, "white-space", chain) == "normal"


def test_runtime_phase_labels_are_never_truncated(tmp_path):
    module = load_generator()
    write_fixture(tmp_path, "counts")
    rendered = module.build(tmp_path)
    assert "class='barlab'" in rendered
    css = stylesheet(rendered)
    for condition in (None, "max-width:560px"):
        assert winning_value(css, "white-space", PHASE_LABEL, condition) != "nowrap"
        assert winning_value(css, "text-overflow", PHASE_LABEL, condition) != "ellipsis"
        assert winning_value(css, "overflow", PHASE_LABEL, condition) != "hidden"


def test_figure_viewer_quotes_every_caption_block_separately(tmp_path):
    module = load_generator()
    write_fixture(tmp_path, "counts")
    rendered = module.build(tmp_path)
    blocks = CaptionBlocks(rendered).classes
    zoom = re.search(r"function bsqZoom\(btn\)\{.*", rendered).group(0)
    selector = re.search(r"querySelectorAll\('([^']+)'\)", zoom)
    assert selector, "the figure viewer no longer reads the caption block by block"
    assert blocks and all(f".{name}" in selector.group(1) for name in blocks)
    assert "textContent=(caption?caption.textContent" not in zoom


def test_cascade_resolution_follows_specificity_and_order():
    css = "a td{white-space:nowrap} td.x{white-space:pre} .y td.x{white-space:normal}"
    assert winning_value(css, "white-space", [("a", set()), ("td", set())]) == "nowrap"
    assert winning_value(css, "white-space", [("a", set()), ("td", {"x"})]) == "pre"
    assert winning_value(css, "white-space", [("div", {"y"}), ("td", {"x"})]) == "normal"
    assert winning_value("@media(max-width:560px){.z{overflow:hidden}}", "overflow",
                         [("div", {"z"})]) is None
    assert winning_value("@media(max-width:560px){.z{overflow:hidden}}", "overflow",
                         [("div", {"z"})], "max-width:560px") == "hidden"
