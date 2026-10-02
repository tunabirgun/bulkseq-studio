from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from _runtime import rscript_runtime


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "workflow" / "scripts"


def _runtime():
    runtime = rscript_runtime("ggplot2", "svglite", "scales", "RColorBrewer", "ggrepel", "jsonlite")
    if runtime is None:
        reason = "R comparative-figure packages are unavailable"
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R_META") else pytest.skip)(reason)
    return runtime


def _fixture(project: Path, recorded_alpha, *, crowded: bool = False,
             all_zero: bool = False, near_guide: bool = False) -> None:
    meta = project / "results" / "meta"
    meta.mkdir(parents=True)
    columns = ("gene_id,combined_pvalue,combined_padj,combined_z,common_direction,n_studies_sig,"
               "rem_log2FC,rem_ci_lo,rem_ci_hi,rem_pvalue,rem_padj,tau2,I2,QEp,meta_sig,"
               "study_A_log2FC,study_A_padj,study_B_log2FC,study_B_padj\n")
    rows = (
        "g03,0.01,0.03,2.5,up,0,0.5,0.1,0.9,0.01,0.03,NA,NA,NA,TRUE,0.4,0.2,0.6,0.2\n"
        "g07,0.04,0.07,2.0,up,0,0.7,0.2,1.2,0.06,0.1,NA,NA,NA,FALSE,0.6,0.2,0.8,0.2\n"
        "g20,0.15,0.2,1.0,up,0,0.1,-0.3,0.5,0.3,0.3,NA,NA,NA,FALSE,0.1,0.5,0.1,0.5\n"
    )
    if crowded:
        rows += (
            "gzero,0,0,9,up,0,0.9,0.4,1.4,0,0,NA,NA,NA,TRUE,0.8,0.01,1.0,0.01\n"
            "gdiscord,0.3,NA,0.8,discordant,0,0.2,-0.2,0.6,0.4,0.5,NA,NA,NA,FALSE,0.5,0.5,-0.5,0.5\n"
        )
    if all_zero:
        rows = (
            "g03,0,0,9,up,0,0.5,0.1,0.9,0.01,0.03,NA,NA,NA,TRUE,0.4,0.2,0.6,0.2\n"
            "g07,0,0,9,up,0,0.7,0.2,1.2,0.06,0.1,NA,NA,NA,TRUE,0.6,0.2,0.8,0.2\n"
            "g20,0,0,9,up,0,0.1,-0.3,0.5,0.3,0.3,NA,NA,NA,TRUE,0.1,0.5,0.1,0.5\n"
        )
    if near_guide:
        rows = (
            "g15,0.02,0.0308359045157523,2.2,up,0,1.16778869315851,0.7,1.6,0.02,0.04,NA,NA,NA,TRUE,1.0,0.1,1.2,0.1\n"
            "g2,0.03,0.0380873330103224,2.1,up,0,1.07347461546307,0.6,1.5,0.03,0.05,NA,NA,NA,TRUE,0.9,0.1,1.1,0.1\n"
            "gmin,0.5,0.5,0.6,down,0,-1.03327715952093,-1.4,-0.6,0.5,0.5,NA,NA,NA,FALSE,-1.0,0.5,-1.1,0.5\n"
            "gmid,0.9,0.978194,0.1,up,0,0.2,-0.2,0.6,0.9,0.9,NA,NA,NA,FALSE,0.1,0.8,0.3,0.8\n"
        )
    (meta / "meta_analysis_results.csv").write_text(columns + rows, encoding="utf-8")
    study_effects = (("A", (1.0, 0.9, -1.0, 0.1)),
                     ("B", (1.2, 1.1, -1.1, 0.3))) if near_guide else (
                         ("A", (0.4, 0.6, 0.1, 0.8, 0.5)),
                         ("B", (0.6, 0.8, 0.1, 1.0, -0.5)))
    for study, effects in study_effects:
        with (meta / f"per_study_{study}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("gene_id", "log2FoldChange", "lfcSE", "padj"))
            genes = (("g15", "g2", "gmin", "gmid") if near_guide else
                     ("g03", "g07", "g20", "gzero", "gdiscord") if crowded else
                     ("g03", "g07", "g20"))
            for gene, effect in zip(genes, effects):
                writer.writerow((gene, effect, 0.2, 0.2))
    ledger = {"execution": {"combined_alpha": recorded_alpha}} if recorded_alpha is not None else {}
    (meta / "meta_eligibility.json").write_text(json.dumps(ledger), encoding="utf-8")


def _render(project: Path, runtime, script_name: str = "make_meta_figures.R") -> None:
    command, convert = runtime
    figures = project / "results" / "figures"
    reports = project / "results" / "reports"
    figures.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    names = {"volcano": "meta_volcano", "forest": "meta_forest",
             "scatter": "meta_concordance_scatter", "heatmap": "meta_convergent_heatmap",
             "hetero": "meta_heterogeneity", "phist": "meta_combined_p_hist",
             "gain": "meta_integration_gain"}
    paths = {f"{key}_{ext}": convert(figures / f"{name}.{ext}")
             for key, name in names.items() for ext in ("png", "svg")}
    paths.update(convergent=convert(project / "results/meta/meta_convergent_genes.csv"),
                 study_summary=convert(project / "results/meta/meta_study_summary.csv"),
                 summary_json=convert(reports / "meta_analysis_summary.json"))
    output = ",\n  ".join(f"{key}={json.dumps(value)}" for key, value in paths.items())
    harness = project / "render.R"
    harness.write_text(
        "setClass('FigureInput', slots=c(input='list', output='list', params='list', log='list', scriptdir='character'))\n"
        "snakemake <- new('FigureInput',\n"
        f"  input=list(results={json.dumps(convert(project / 'results/meta/meta_analysis_results.csv'))}, "
        f"eligibility={json.dumps(convert(project / 'results/meta/meta_eligibility.json'))}),\n"
        f"  output=list({output}),\n"
        "  params=list(style=list(), alpha=0.1, lfc_threshold=1, n_forest=6),\n"
        f"  log=list({json.dumps(convert(project / 'figure.log'))}),\n"
        f"  scriptdir={json.dumps(convert(SCRIPTS))})\n"
        f"source({json.dumps(convert(project / script_name if script_name != 'make_meta_figures.R' else SCRIPTS / script_name))})\n",
        encoding="utf-8",
    )
    result = subprocess.run([*command, convert(harness)], cwd=ROOT, capture_output=True,
                            text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _guide_and_points(svg: Path):
    root = ElementTree.parse(svg).getroot()
    lines = [element for element in root.iter() if element.tag.endswith("}line")
             and "stroke-dasharray" in element.attrib.get("style", "")
             and element.attrib.get("y1") == element.attrib.get("y2")
             and element.attrib.get("x1") != element.attrib.get("x2")]
    circles = [element for element in root.iter() if element.tag.endswith("}circle")
               and "fill-opacity: 0.60" in element.attrib.get("style", "")]
    return lines, circles, " ".join(root.itertext())


def _assert_recorded_guide(svg: Path) -> None:
    lines, circles, text = _guide_and_points(svg)
    assert len(lines) == 1, len(lines)
    guide = lines[0]
    right = float(guide.attrib["x2"])
    data_points = [circle for circle in circles if float(circle.attrib["cx"]) < right]
    assert len(data_points) == 3, len(data_points)
    g07 = max(data_points, key=lambda circle: float(circle.attrib["cx"]))
    assert min(float(point.attrib["cy"]) for point in data_points) < float(guide.attrib["y1"]) < float(g07.attrib["cy"])
    assert "recorded run alpha 0.05" in text


def _subtitle_rows(svg: Path):
    root = ElementTree.parse(svg).getroot()
    width = float(root.attrib["width"].removesuffix("pt"))
    phrases = ("off-scale", "Combined-FDR guide", "matching-sign family exclusion",
               "Combined-FDR threshold not recorded")
    rows = [node for node in root.iter() if node.tag.endswith("}text")
            and any(phrase in (node.text or "") for phrase in phrases)]
    return width, rows


def _fold_guide_label_boxes(svg: Path):
    root = ElementTree.parse(svg).getroot()
    guides = [float(node.attrib["x1"]) for node in root.iter()
              if node.tag.endswith("}line") and node.attrib.get("x1") == node.attrib.get("x2")
              and "stroke-dasharray" in node.attrib.get("style", "")]
    assert len(guides) == 2
    boxes = []
    for node in root.iter():
        if node.tag.endswith("}text") and node.text in ("g2", "g15"):
            center = float(node.attrib["x"])
            width = float(node.attrib["textLength"].removesuffix("px"))
            boxes.append((node.text, center - width / 2, center + width / 2))
    assert len(boxes) == 2
    return max(guides), boxes


def test_volcano_guide_uses_result_alpha_and_rejects_figure_alpha(tmp_path: Path) -> None:
    runtime = _runtime()
    project = tmp_path / "recorded"
    _fixture(project, 0.05)
    _render(project, runtime)
    svg = project / "results/figures/meta_volcano.svg"
    _assert_recorded_guide(svg)
    shutil.copy2(svg, project / "recorded-guide.svg")
    shutil.copy2(project / "results/figures/meta_volcano.png", project / "recorded-guide.png")
    results = list(csv.DictReader((project / "results/meta/meta_analysis_results.csv").open(encoding="utf-8")))
    assert next(row for row in results if row["gene_id"] == "g07")["meta_sig"] == "FALSE"
    assert json.loads((project / "results/reports/meta_analysis_summary.json").read_text())["alpha"] == 0.1

    source = (SCRIPTS / "make_meta_figures.R").read_text(encoding="utf-8")
    old_guide = "geom_hline(yintercept = -log10(recorded_alpha)"
    assert source.count(old_guide) == 1
    (project / "old-guide.R").write_text(source.replace(old_guide,
        "geom_hline(yintercept = -log10(alpha)"), encoding="utf-8")
    _render(project, runtime, "old-guide.R")
    with pytest.raises(AssertionError):
        _assert_recorded_guide(svg)


def test_volcano_disclosures_fit_canvas_with_exclusions_and_offscale(tmp_path: Path) -> None:
    runtime = _runtime()
    project = tmp_path / "wrapped"
    _fixture(project, 0.05, crowded=True)
    _render(project, runtime)
    svg = project / "results/figures/meta_volcano.svg"
    width, rows = _subtitle_rows(svg)
    assert len(rows) == 3, [node.text for node in rows]
    assert all(float(node.attrib["x"]) + float(node.attrib["textLength"].removesuffix("px")) < width - 4
               for node in rows), [(node.text, node.attrib.get("textLength")) for node in rows]
    assert len({float(node.attrib["y"]) for node in rows}) == 3
    shutil.copy2(svg, project / "wrapped-guide.svg")
    shutil.copy2(project / "results/figures/meta_volcano.png", project / "wrapped-guide.png")

    source = (SCRIPTS / "make_meta_figures.R").read_text(encoding="utf-8")
    wrapped = 'collapse = "\\n")) +'
    assert source.count(wrapped) == 1
    (project / "old-subtitle.R").write_text(source.replace(wrapped, 'collapse = "; ")) +'),
                                                   encoding="utf-8")
    _render(project, runtime, "old-subtitle.R")
    old_width, old_rows = _subtitle_rows(svg)
    assert any(float(node.attrib["x"]) + float(node.attrib["textLength"].removesuffix("px")) > old_width
               for node in old_rows), [(node.text, node.attrib.get("textLength")) for node in old_rows]


def test_labels_near_fold_guide_have_clearance(tmp_path: Path) -> None:
    runtime = _runtime()
    project = tmp_path / "near_guide"
    _fixture(project, 0.05, near_guide=True)
    _render(project, runtime)
    svg = project / "results/figures/meta_volcano.svg"
    guide, boxes = _fold_guide_label_boxes(svg)
    assert all(right + 2 < guide or left - 2 > guide for _, left, right in boxes), (guide, boxes)
    shutil.copy2(svg, project / "clear-guide.svg")
    shutil.copy2(project / "results/figures/meta_volcano.png", project / "clear-guide.png")

    source = (SCRIPTS / "make_meta_figures.R").read_text(encoding="utf-8")
    assert source.count("nudge_x = label_nudge") == 1
    (project / "old-placement.R").write_text(
        source.replace("nudge_x = label_nudge", "nudge_x = 0"), encoding="utf-8")
    _render(project, runtime, "old-placement.R")
    guide, old_boxes = _fold_guide_label_boxes(svg)
    assert any(left <= guide <= right for _, left, right in old_boxes), (guide, old_boxes)


@pytest.mark.parametrize("recorded_alpha", (0.05, None))
def test_all_zero_combined_fdr_keeps_offscale_triangles(tmp_path: Path, recorded_alpha) -> None:
    runtime = _runtime()
    project = tmp_path / "all_zero"
    _fixture(project, recorded_alpha, all_zero=True)
    _render(project, runtime)
    svg = project / "results/figures/meta_volcano.svg"
    root = ElementTree.parse(svg).getroot()
    text = " ".join(root.itertext())
    assert "3 genes off-scale" in text
    triangles = [node for node in root.iter() if node.tag.endswith("}polygon")]
    assert len(triangles) >= 3, len(triangles)
    lines, _, _ = _guide_and_points(svg)
    assert len(lines) == (1 if recorded_alpha is not None else 0)
    if recorded_alpha is None:
        assert "threshold not recorded; no significance guide" in text


@pytest.mark.parametrize("recorded_alpha", (None, "0.05"))
def test_volcano_without_valid_recorded_alpha_has_no_guide(tmp_path: Path, recorded_alpha) -> None:
    runtime = _runtime()
    project = tmp_path / "legacy"
    _fixture(project, recorded_alpha)
    _render(project, runtime)
    lines, _, text = _guide_and_points(project / "results/figures/meta_volcano.svg")
    assert not lines
    assert "Combined-FDR threshold not recorded; no significance guide" in text
    assert json.loads((project / "results/reports/meta_analysis_summary.json").read_text())["alpha"] == 0.1
