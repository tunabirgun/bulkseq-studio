from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from _runtime import rscript_runtime


SCRIPTS = Path(__file__).resolve().parents[1] / "workflow" / "scripts"


def test_meta_enrichment_placeholder_renders_execution_evidence(tmp_path: Path) -> None:
    runtime = rscript_runtime("ggplot2", "svglite", "scales", "RColorBrewer", "systemfonts", "jsonlite")
    if runtime is None:
        (pytest.fail if os.environ.get("BULKSEQ_REQUIRE_R") else pytest.skip)(
            "R figure packages are required for enrichment placeholder rendering")
    command, convert = runtime
    for mode in ("failed", "empty", "missing"):
        case = tmp_path / mode
        case.mkdir()
        if mode != "missing":
            message = ("Cross-study enrichment: no GO terms passed the FDR cutoff in any study/convergent set."
                       if mode == "empty" else "Cross-study enrichment unavailable: injected failure")
            (case / "check.json").write_text(json.dumps({
                "status": "PASS" if mode == "empty" else "REVIEW_REQUIRED",
                "messages": [{"message": message}],
            }), encoding="utf-8")
    harness = tmp_path / "render.R"
    harness.write_text(r'''
args <- commandArgs(trailingOnly=TRUE)
setClass("FigureRun", slots=c(input="list", output="list", params="list",
                              log="list", scriptdir="character"))
for (mode in c("failed", "empty", "missing")) {
  work <- file.path(args[[2]], mode)
  objects <- file.path(work, "objects.rds")
  saveRDS(NULL, objects)
  snakemake <- new("FigureRun",
    input=list(objects=objects, check=file.path(work, "check.json")),
    output=list(dotplot_png=file.path(work, "figure.png"),
                dotplot_svg=file.path(work, "figure.svg"),
                plotted_data=file.path(work, "plotted.csv")),
    params=list(style=list()), log=list(file.path(work, "figure.log")), scriptdir=args[[1]])
  source(file.path(args[[1]], "make_meta_enrichment_figures.R"))
}
''', encoding="utf-8")
    result = subprocess.run([*command, convert(harness), convert(SCRIPTS), convert(tmp_path)],
                            capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    for mode in ("failed", "empty", "missing"):
        case = tmp_path / mode
        root = ElementTree.parse(case / "figure.svg").getroot()
        labels = [node.text for node in root.iter() if node.tag.endswith("}text")]
        visible = " ".join(label for label in labels if label)
        assert "checks/18_meta_enrichment_qc.json" in visible
        assert "organism unmapped or no significant terms" not in visible
        if mode == "empty":
            assert "No GO terms passed the FDR cutoff" in visible
        else:
            assert "Cross-study enrichment results unavailable" in visible
            assert "No GO terms passed" not in visible
        assert (case / "figure.png").stat().st_size > 1000
