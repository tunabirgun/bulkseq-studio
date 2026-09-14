"""The sample sheet keeps the newline it already has, on every platform.

pandas serializes with ``os.linesep``, so a sheet written by the interface on Windows and saved
again on native Linux differed in every byte. ``save_metadata`` only skips writing when the bytes
match, so that difference moved ``samples.tsv``'s mtime -- and that file is a declared input to
most of the workflow, which makes a resumed run rebuild from alignment. These tests pin the
behaviour from the file on disk rather than from the host, so they fail on exactly one platform if
the terminator is ever taken from ``os.linesep`` again.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.metadata import existing_line_terminator, load_metadata, save_metadata

FRAME = pd.DataFrame(
    {"sample_id": ["s1", "s2"], "condition": ["ctrl", "treat"], "layout": ["paired", "paired"]}
)


def _write(path: Path, terminator: str, frame: pd.DataFrame = FRAME) -> bytes:
    body = terminator.join(
        ["\t".join(frame.columns), *("\t".join(row) for row in frame.astype(str).to_numpy())]
    ) + terminator
    data = body.encode("utf-8")
    path.write_bytes(data)
    return data


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_resaving_an_unchanged_sheet_never_rewrites_it(terminator: str, tmp_path: Path) -> None:
    """The guard that protects a resumed run has to survive the host, not just the content."""
    path = tmp_path / "samples.tsv"
    before = _write(path, terminator)
    mtime = path.stat().st_mtime_ns

    save_metadata(load_metadata(path), path)

    assert path.read_bytes() == before, "re-saving an unchanged sheet rewrote it"
    assert path.stat().st_mtime_ns == mtime, "re-saving an unchanged sheet moved the mtime"


@pytest.mark.parametrize("terminator", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_real_edit_keeps_the_sheet_s_own_terminator(terminator: str, tmp_path: Path) -> None:
    path = tmp_path / "samples.tsv"
    _write(path, terminator)
    edited = load_metadata(path)
    edited.loc[0, "condition"] = "renamed"

    save_metadata(edited, path)
    data = path.read_bytes()

    assert b"renamed" in data, "the edit was not written"
    assert (b"\r\n" in data) is (terminator == "\r\n")
    assert load_metadata(path).loc[0, "condition"] == "renamed"


def test_a_new_sheet_is_written_with_line_feeds(tmp_path: Path) -> None:
    path = tmp_path / "samples.tsv"
    save_metadata(FRAME, path)
    assert b"\r\n" not in path.read_bytes()


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (b"", "\n"),
        (b"sample_id\n", "\n"),
        (b"sample_id\r\n", "\r\n"),
        (b"sample_id", "\n"),
    ),
    ids=["empty", "lf", "crlf", "no-terminator-at-all"],
)
def test_the_terminator_is_read_from_the_file_not_the_host(
    content: bytes, expected: str, tmp_path: Path
) -> None:
    path = tmp_path / "samples.tsv"
    path.write_bytes(content)
    assert existing_line_terminator(path) == expected


def test_an_absent_sheet_reports_line_feeds(tmp_path: Path) -> None:
    assert existing_line_terminator(tmp_path / "missing.tsv") == "\n"


def test_a_scaffolded_project_writes_both_sheets_with_line_feeds(tmp_path: Path) -> None:
    """A new project's sheet must not differ by the platform that created it."""
    from app.core.project import ProjectManager

    root = ProjectManager().create_project("proj", tmp_path)
    for name in ("samples.tsv", "samples.auto_generated.tsv"):
        data = (Path(root) / "config" / name).read_bytes()
        assert b"\r\n" not in data, f"{name} was scaffolded with the host's newline"
        assert data.endswith(b"\n")
