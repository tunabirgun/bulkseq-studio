from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core.provenance import _drop_project, _summary_text, diff_configs

# workflow/scripts/make_run_summary.py is the pipeline-side provenance writer (this module,
# app.core.provenance, is only the GUI's pre-run preview). Loaded by path like the other
# workflow/scripts/*.py tests (see tests/test_html_report_enrichment.py) since it is not
# imported as a package.
_MRS_PATH = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_run_summary.py"


def _load_make_run_summary():
    spec = importlib.util.spec_from_file_location("make_run_summary", _MRS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_diff_reports_changed_scalar() -> None:
    defaults = {"fastp": {"length_required": 36}}
    used = {"fastp": {"length_required": 50}}
    changed = diff_configs(defaults, used)
    assert changed == {"fastp.length_required": {"default": 36, "used": 50}}


def test_diff_ignores_keys_absent_from_defaults() -> None:
    changed = diff_configs({"a": 1}, {"a": 1, "b": 2})
    assert changed == {}


def test_diff_identical_configs_empty() -> None:
    cfg = {"workflow": {"aligner": "STAR"}, "deseq2": {"alpha": 0.05}}
    assert diff_configs(cfg, cfg) == {}


def test_drop_project_excludes_only_project() -> None:
    cfg = {"project": {"name": "x"}, "workflow": {"aligner": "STAR"}}
    assert _drop_project(cfg) == {"workflow": {"aligner": "STAR"}}


def test_project_identity_not_reported_as_customized() -> None:
    defaults = {"project": {"name": "example_project"}, "workflow": {"aligner": "STAR"}}
    used = {"project": {"name": "real_project"}, "workflow": {"aligner": "STAR"}}
    changed = diff_configs(_drop_project(defaults), _drop_project(used))
    assert changed == {}


def test_summary_text_reports_none_when_empty() -> None:
    text = _summary_text({"customized_parameters": {}, "software_versions": {}, "workflow": {}})
    assert "None detected" in text


def test_run_summary_tools_capture_configured_trimmer_and_rrna_filter() -> None:
    # make_run_summary.py's TOOLS dict previously never probed the trimmer / rRNA filter /
    # contamination screen a run actually used (only the fastp/sortmerna defaults were ever
    # hardcoded), so a project that enabled trim_galore, ribodetector or fastq_screen was not
    # reproducible from its own run summary. select_tools() gates the probe list on the
    # config that was actually used.
    mrs = _load_make_run_summary()
    # The contamination screen needs a FastQ Screen config path as well as the switch —
    # that is the Snakefile's own gate, and without it the rule is skipped with a warning.
    config = {"workflow": {"trimmer": "trim-galore", "rrna_filtering": True,
                           "rrna_tool": "ribodetector", "contamination_screen": True},
              "contamination": {"conf": "/path/to/fastq_screen.conf"}}
    tools = mrs.select_tools(config)
    assert "trim_galore" in tools, "configured trimmer (trim-galore) is not probed"
    assert "ribodetector" in tools, "configured rRNA filter (ribodetector) is not probed"
    assert "fastq_screen" in tools, "enabled contamination screen (fastq_screen) is not probed"
    assert "fastp" not in tools, "fastp should not be probed when trim-galore is configured"
    assert "sortmerna" not in tools, "sortmerna should not be probed when ribodetector is configured"
