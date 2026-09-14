"""The per-sample display-label rule, pinned across the R/Python language boundary.

`library_name` is an optional sample-sheet column read by two implementations: R
(workflow/scripts/de_common.R) labels the figures from colData, Python
(workflow/scripts/_sample_labels.py) labels the reports from the sheet. A divergence would show
the same run two different sample names, so both are run over the same TSV fixtures here and
compared value by value.

The fixtures are real sample sheets parsed by each language's own reader on purpose:
`read.delim` types an all-blank column as logical NA and an all-numeric one as integer, and both
readers see the bare token NA as missing. A hand-built in-memory vector would exercise a type the
pipeline never produces.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _runtime import bash_runtime

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "workflow" / "scripts"
DE_COMMON = SCRIPTS / "de_common.R"

# Exit code the shell runner uses when no R interpreter resolves at all, kept clear of Rscript's
# own status codes so "no R here" is never read as "the script failed".
_NO_R = 97

_RUNNER = f"""\
if [ -x "$HOME/.local/bin/micromamba" ] && "$HOME/.local/bin/micromamba" run -n bulkseq Rscript --version >/dev/null 2>&1; then
  exec "$HOME/.local/bin/micromamba" run -n bulkseq Rscript "$@"
elif command -v Rscript >/dev/null 2>&1; then
  exec Rscript "$@"
else
  exit {_NO_R}
fi
"""

# Reads each fixture sheet exactly as the DE engines do (read.delim, then the whole frame becomes
# colData) and writes one line per fixture: name, then the labels, tab separated.
_HARNESS = r'''
args <- commandArgs(trailingOnly = TRUE)
source(args[[1]])
sheets <- sort(list.files(args[[2]], pattern = "[.]tsv$", full.names = TRUE))
rows <- vapply(sheets, function(path) {
  samples <- read.delim(path, stringsAsFactors = FALSE)
  labels <- sample_display_labels(samples$sample_id, samples[["library_name"]])
  paste(c(sub("[.]tsv$", "", basename(path)), labels), collapse = "\t")
}, character(1))
writeLines(rows, args[[3]])
'''

# Every shape the column can take, as sample-sheet text.
FIXTURES: dict[str, str] = {
    "absent": "sample_id\tcondition\ns1\tuntreated\ns2\ttreated\n",
    "all_blank": ("sample_id\tlibrary_name\tcondition\n"
                  "s1\t\tuntreated\ns2\t\ttreated\n"),
    "all_unique": ("sample_id\tlibrary_name\tcondition\n"
                   "s1\tLiver control\tuntreated\ns2\tLiver treated\ttreated\n"),
    "all_duplicate": ("sample_id\tlibrary_name\tcondition\n"
                      "s1\tLiver\tuntreated\ns2\tLiver\ttreated\n"),
    "partly_duplicate": ("sample_id\tlibrary_name\tcondition\n"
                         "s1\tLiver\tuntreated\ns2\tLiver\ttreated\ns3\tBrain\ttreated\n"),
    "blank_and_duplicate": ("sample_id\tlibrary_name\tcondition\n"
                            "s1\tLiver\tuntreated\ns2\t\tuntreated\ns3\tLiver\ttreated\n"),
    # read.delim types an all-numeric column as integer; both sides must coerce it to text.
    "numeric_names": ("sample_id\tlibrary_name\tcondition\n"
                      "s1\t1\tuntreated\ns2\t2\ttreated\n"),
    # trimws() and the Python mirror strip the same characters, so these two collapse to one name.
    "padded_names": ("sample_id\tlibrary_name\tcondition\n"
                     "s1\t  Liver \tuntreated\ns2\tLiver\ttreated\n"),
    # The bare token NA is missing to read.delim; the Python side matches it deliberately.
    "na_token": ("sample_id\tlibrary_name\tcondition\n"
                 "s1\tNA\tuntreated\ns2\tLiver\ttreated\n"),
}

EXPECTED: dict[str, list[str]] = {
    "absent": ["s1", "s2"],
    "all_blank": ["s1", "s2"],
    "all_unique": ["Liver control", "Liver treated"],
    "all_duplicate": ["Liver (s1)", "Liver (s2)"],
    "partly_duplicate": ["Liver (s1)", "Liver (s2)", "Brain"],
    "blank_and_duplicate": ["Liver (s1)", "s2", "Liver (s3)"],
    "numeric_names": ["1", "2"],
    "padded_names": ["Liver (s1)", "Liver (s2)"],
    "na_token": ["s1", "Liver"],
}


@pytest.fixture(scope="module")
def labels():
    spec = importlib.util.spec_from_file_location(
        "_sample_labels", SCRIPTS / "_sample_labels.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse(text: str) -> tuple[list[str], list[str] | None]:
    """Read a fixture sheet the way the Python reports read it."""
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = list(reader)
    ids = [row["sample_id"] for row in rows]
    if "library_name" not in (reader.fieldnames or []):
        return ids, None
    return ids, [row["library_name"] for row in rows]


def _python_labels(labels, name: str) -> list[str]:
    ids, names = _parse(FIXTURES[name])
    return labels.sample_display_labels(ids, names)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_python_labels_match_the_stated_display_rule(labels, name) -> None:
    assert _python_labels(labels, name) == EXPECTED[name]


def _bash_or_skip():
    """The shared probe's bash runtime, or a skip.

    Module-level so tests/test_runtime_probe.py can reach it. Checking shutil.which("wsl")
    instead would report a runtime on a Windows host that ships wsl.exe with no distribution
    installed -- the hosted runner -- and the harness would then fail rather than skip.
    """
    runtime = bash_runtime()
    if runtime is None:
        pytest.skip("no bash runtime: install WSL2 with a distribution on Windows")
    return runtime


@pytest.fixture(scope="module")
def r_labels(tmp_path_factory) -> dict[str, list[str]]:
    bash, as_path = _bash_or_skip()
    work = tmp_path_factory.mktemp("sample_labels")
    sheets = work / "sheets"
    sheets.mkdir()
    for name, text in FIXTURES.items():
        (sheets / f"{name}.tsv").write_text(text, encoding="utf-8", newline="")
    script = work / "harness.R"
    script.write_text(_HARNESS, encoding="utf-8", newline="\n")
    runner = work / "run_r.sh"
    runner.write_text(_RUNNER, encoding="utf-8", newline="\n")
    out = work / "labels.tsv"

    cmd = [*bash, as_path(runner), as_path(script), as_path(DE_COMMON),
           as_path(sheets), as_path(out)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == _NO_R:
        pytest.skip("R runtime unavailable: neither Rscript nor the bulkseq micromamba env")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    parsed = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        parsed[fields[0]] = fields[1:]
    return parsed


def test_r_reads_every_fixture_shape(r_labels) -> None:
    assert sorted(r_labels) == sorted(FIXTURES)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_r_and_python_agree_on_every_fixture(labels, r_labels, name) -> None:
    assert r_labels[name] == _python_labels(labels, name) == EXPECTED[name]


def test_an_absent_column_returns_the_sample_ids_themselves(labels) -> None:
    ids = ["s1", "s2", "s3"]
    assert labels.sample_display_labels(ids, None) == ids


def test_a_mismatched_column_length_is_refused(labels) -> None:
    with pytest.raises(ValueError):
        labels.sample_display_labels(["s1", "s2"], ["Liver"])


def test_disambiguated_labels_are_unique_whenever_sample_ids_are(labels) -> None:
    ids = ["s1", "s2", "s3", "s4"]
    names = ["Liver", "Liver", "Liver", ""]
    out = labels.sample_display_labels(ids, names)
    assert len(set(out)) == len(out)


def test_label_rows_are_empty_without_a_recorded_library_name(labels) -> None:
    assert labels.sample_label_rows(FIXTURES["absent"]) == []
    assert labels.sample_label_rows(FIXTURES["all_blank"]) == []
    assert labels.sample_label_rows("") == []


def test_label_rows_keep_the_sample_id_beside_the_label(labels) -> None:
    assert labels.sample_label_rows(FIXTURES["all_duplicate"]) == [
        ("s1", "Liver", "Liver (s1)"),
        ("s2", "Liver", "Liver (s2)"),
    ]


def test_label_rows_tolerate_a_byte_order_mark(labels) -> None:
    assert labels.sample_label_rows("﻿" + FIXTURES["all_unique"]) == [
        ("s1", "Liver control", "Liver control"),
        ("s2", "Liver treated", "Liver treated"),
    ]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mrs():
    return _load("make_run_summary")


@pytest.fixture(scope="module")
def mhr():
    return _load("make_html_report")


def _payload() -> dict:
    return {"project": {"name": "p"}, "run_date": "2026-09-14", "input": {"type": "fastq"},
            "deseq2": {"design_formula": "~ condition", "alpha": 0.05, "lfc_threshold": 1,
                       "contrasts": [{"factor": "condition", "numerator": "treated",
                                      "denominator": "untreated"}]},
            "workflow": {}}


@pytest.mark.parametrize("name", ["absent", "all_blank"])
def test_study_design_is_unchanged_without_a_recorded_library_name(mrs, name) -> None:
    # The acceptance condition for the whole column: a project that does not use it gets the
    # report it gets today, byte for byte.
    text = FIXTURES[name]
    assert mrs.figure_label_lines(text) == []
    rendered = mrs.render_study_design(_payload(), text)
    assert "Figure sample labels" not in rendered
    # Nothing follows the verbatim sheet, so the file is byte-for-byte what it is today.
    assert rendered.endswith(text.rstrip("\n") + "\n")


def test_study_design_maps_each_sample_id_to_its_figure_label(mrs) -> None:
    rendered = mrs.render_study_design(_payload(), FIXTURES["partly_duplicate"])
    assert "Figure sample labels" in rendered
    for expected in ("s1  Liver (s1)", "s2  Liver (s2)", "s3  Brain"):
        assert expected in rendered


def test_report_sample_table_is_absent_without_a_recorded_library_name(mhr, tmp_path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "samples.tsv").write_text(FIXTURES["all_blank"], encoding="utf-8")
    assert mhr._sample_label_table(tmp_path, _payload()) == ""
    assert mhr._study_design_section(_payload(), tmp_path) == mhr._study_design_section(_payload())


def test_report_sample_table_shows_the_id_beside_the_label(mhr, tmp_path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "samples.tsv").write_text(FIXTURES["all_duplicate"], encoding="utf-8")
    table = mhr._sample_label_table(tmp_path, _payload())
    assert "<th>Sample ID</th>" in table and "<th>Figure label</th>" in table
    assert "<td class='mono'>s1</td><td>Liver</td><td>Liver (s1)</td>" in table
    assert table in mhr._study_design_section(_payload(), tmp_path)
