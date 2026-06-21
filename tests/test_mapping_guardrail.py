from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BULKSEQ_SKIP_READINESS_DIALOG", "1")

from app.ui.main_window import MainWindow  # noqa: E402

# A trimmed STAR Log.final.out (the relevant line is "Uniquely mapped reads %").
STAR_LOG = (
    "                                 Started job on |\tJun 21\n"
    "                     Number of input reads |\t55000\n"
    "                  Uniquely mapped reads number |\t4286\n"
    "                       Uniquely mapped reads % |\t7.79%\n"
    "       % of reads unmapped: too short |\t91.82%\n"
)


def test_parse_unique_mapped_pct(tmp_path: Path) -> None:
    log = tmp_path / "SRR1_Log.final.out"
    log.write_text(STAR_LOG, encoding="utf-8")
    assert MainWindow._parse_unique_mapped_pct(log) == 7.79


def test_parse_unique_mapped_pct_high(tmp_path: Path) -> None:
    log = tmp_path / "SRR2_Log.final.out"
    log.write_text(STAR_LOG.replace("7.79%", "88.40%"), encoding="utf-8")
    assert MainWindow._parse_unique_mapped_pct(log) == 88.40


def test_parse_unique_mapped_pct_missing(tmp_path: Path) -> None:
    log = tmp_path / "empty_Log.final.out"
    log.write_text("no mapping stats yet\n", encoding="utf-8")
    assert MainWindow._parse_unique_mapped_pct(log) is None
