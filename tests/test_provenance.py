from __future__ import annotations

from app.core.provenance import _drop_project, _summary_text, diff_configs


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
