from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _ingest_rule_source(source: str) -> str:
    start = source.index("    rule ingest_counts:")
    end = source.index("\nelif USE_SALMON:", start)
    rule = source[start:end]
    required = (
        'script="workflow/scripts/ingest_counts.py"',
        'validator="workflow/scripts/count_matrix_validation.py"',
        "python {input.script:q}",
    )
    missing = [entry for entry in required if entry not in rule]
    assert not missing, f"ingest_counts does not track or invoke: {missing}"
    return rule


def test_ingest_counts_tracks_its_entrypoint_and_validator() -> None:
    source = (ROOT / "workflow" / "rules" / "quantification.smk").read_text(encoding="utf-8")
    _ingest_rule_source(source)


def test_ingest_counts_dependency_gate_rejects_the_old_untracked_rule() -> None:
    source = (ROOT / "workflow" / "rules" / "quantification.smk").read_text(encoding="utf-8")
    old_rule = source.replace(
        '            script="workflow/scripts/ingest_counts.py",\n'
        '            validator="workflow/scripts/count_matrix_validation.py",\n',
        "",
        1,
    ).replace("python {input.script:q}", "python workflow/scripts/ingest_counts.py", 1)
    with pytest.raises(AssertionError, match="does not track or invoke"):
        _ingest_rule_source(old_rule)
