from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest


ALIGNMENT_RULES = Path(__file__).parents[1] / "workflow" / "rules" / "alignment.smk"


def _star_rule_block(source: str) -> str:
    return source.split("rule star_align:", 1)[1].split("rule samtools_index:", 1)[0]


def _assert_star_sort_memory_contract(source: str) -> None:
    block = _star_rule_block(source)
    assert "bam_sort_ram=_star_bam_sort_ram_bytes," in block
    assert "--limitBAMsortRAM {params.bam_sort_ram} " in block


def _load_star_sort_memory_function(source: str):
    match = re.search(
        r"(?m)^def _star_bam_sort_ram_bytes\([^\n]+\):\n(?:    .*\n)+",
        source,
    )
    assert match is not None, "alignment.smk must define the STAR sort-memory derivation"
    namespace: dict[str, object] = {}
    exec(match.group(0), namespace)
    return namespace["_star_bam_sort_ram_bytes"]


def test_star_sort_memory_contract_rejects_the_previous_implicit_default() -> None:
    source = ALIGNMENT_RULES.read_text(encoding="utf-8")
    _assert_star_sort_memory_contract(source)

    previous_command = source.replace(
        '"--limitBAMsortRAM {params.bam_sort_ram} "',
        "",
        1,
    )
    assert previous_command != source
    with pytest.raises(AssertionError, match="limitBAMsortRAM"):
        _assert_star_sort_memory_contract(previous_command)


def test_star_sort_memory_is_half_the_effective_rule_budget() -> None:
    derive = _load_star_sort_memory_function(ALIGNMENT_RULES.read_text(encoding="utf-8"))

    assert derive(None, SimpleNamespace(mem_mb=24_000)) == 12_000_000_000
    assert derive(None, SimpleNamespace(mem_mb=8_000)) == 4_000_000_000


@pytest.mark.parametrize("mem_mb", [0, -1, None, "not-a-number"])
def test_star_sort_memory_rejects_invalid_rule_budgets(mem_mb: object) -> None:
    derive = _load_star_sort_memory_function(ALIGNMENT_RULES.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="positive mem_mb"):
        derive(None, SimpleNamespace(mem_mb=mem_mb))
