from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = REPO_ROOT / "workflow" / "rules"
_BENCHMARK = re.compile(r'"benchmarks/([^"]+)\.tsv"')
# Wildcards a benchmark path may carry; substituted with a concrete token so the stem the
# report will see at run time is what gets classified.
_WILDCARDS = {"sample": "S1", "prefix": "SRR1"}


def _timing_module():
    path = REPO_ROOT / "workflow" / "scripts" / "make_timing_summary.py"
    spec = importlib.util.spec_from_file_location("make_timing_summary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared_stems() -> dict[str, str]:
    """Every benchmark stem the rules can write, mapped to the .smk that declares it."""
    stems: dict[str, str] = {}
    for rules_file in sorted(RULES_DIR.glob("*.smk")):
        for stem in _BENCHMARK.findall(rules_file.read_text(encoding="utf-8")):
            for name, token in _WILDCARDS.items():
                stem = stem.replace("{" + name + "}", token)
            stems.setdefault(stem, rules_file.name)
    return stems


def test_the_rules_declare_benchmarks_to_classify():
    # Guards the two regexes above: if the scan silently matched nothing, every coverage
    # assertion below would pass while checking no rule at all.
    stems = _declared_stems()
    assert len(stems) > 30
    assert "star_align_S1" in stems and "deseq2" in stems


def test_every_declared_benchmark_maps_to_a_named_phase():
    phase_for = _timing_module().phase_for
    unmapped = {stem: src for stem, src in _declared_stems().items() if phase_for(stem) == "Other"}
    assert not unmapped, (
        "these benchmark stems fall into 'Other' in the timing summary; add a PHASES prefix "
        f"in workflow/scripts/make_timing_summary.py: {unmapped}"
    )


def test_other_is_still_reachable_for_an_unknown_step():
    # Negative control for the assertion above: the classifier must be able to report a
    # miss, otherwise the coverage test could never fail.
    assert _timing_module().phase_for("a_rule_nobody_has_written_yet") == "Other"


@pytest.mark.parametrize(
    ("stem", "phase"),
    [
        ("voom_de", "Differential expression"),
        ("ingest_deseq2_results", "Differential expression"),
        ("ingest_geo", "Quantification"),
        ("filter_organellar", "Quantification"),
        ("meta_per_study_enrichment", "Meta-analysis"),
        ("genes_of_interest", "Figures"),
        ("custom_enrichment_figure", "Enrichment"),
        ("sanity_checks", "Sanity checks"),
        # Order-sensitive: the generic prefix that follows must not capture these.
        ("salmon_index", "Reference"),
        ("hisat2_index", "Reference"),
        ("hisat2_align_S1", "Alignment"),
        ("fastq_screen_S1", "QC"),
    ],
)
def test_phase_assignments(stem, phase):
    assert _timing_module().phase_for(stem) == phase
