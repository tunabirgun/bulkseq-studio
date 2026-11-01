from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from app.core.config_models import default_config
from app.core.paths import project_configured_path, windows_to_wsl_path
from app.core.preflight_checks import input_validation_messages
from app.core.snakemake_runner import build_snakemake_command
from workflow.scripts.input_paths import wsl_input_path


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop path semantics")
def test_native_linux_path_is_not_rewritten_as_a_windows_drive() -> None:
    assert windows_to_wsl_path("/home/example/reads_1.fastq.gz") == "/home/example/reads_1.fastq.gz"


@pytest.mark.skipif(os.name != "nt", reason="Windows desktop path semantics")
def test_linux_input_uses_the_project_wsl_distribution() -> None:
    root = Path(r"\\wsl.localhost\ExampleUbuntu\home\example\project")
    assert project_configured_path(root, "/home/example/reads_1.fastq.gz") == Path(
        r"\\wsl.localhost\ExampleUbuntu\home\example\reads_1.fastq.gz"
    )


def test_preflight_resolves_fastqs_relative_to_project_without_mutating_sheet(tmp_path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    read = root / "reads.fastq"
    read.write_text("@synthetic\nACGT\n+\nIIII\n", encoding="ascii")
    frame = pd.DataFrame([
        {"sample_id": "sample1", "condition": "control", "layout": "single", "fastq_1": read.name}
    ])
    before = frame.copy(deep=True)
    config = default_config("path_check", root)
    config.input.type = "fastq"
    findings = input_validation_messages(config, root, frame)
    assert not [finding for finding in findings if "FASTQ R1 does not exist" in finding["message"]]
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize(("supplied", "expected"), [
    (r"\\wsl.localhost\ExampleUbuntu\home\reads with spaces\r1.fq", "/home/reads with spaces/r1.fq"),
    (r"\\wsl$\ExampleUbuntu\home\r1.fq", "/home/r1.fq"),
    ("//wsl.localhost/ExampleUbuntu/home/r1.fq", "/home/r1.fq"),
    ("/home/r1.fq", "/home/r1.fq"),
    ("/mnt/d/reads/r1.fq", "/mnt/d/reads/r1.fq"),
    (r"D:\reads\r1.fq", "/mnt/d/reads/r1.fq"),
    ("D:/reads/r1.fq", "/mnt/d/reads/r1.fq"),
    ("data/raw/r1.fq", "data/raw/r1.fq"),
    (r"data\raw\r1.fq", "data/raw/r1.fq"),
    ("/home/reads_Δ/r1.fq", "/home/reads_Δ/r1.fq"),
])
def test_wsl_input_formats(supplied, expected) -> None:
    assert wsl_input_path(supplied, "ExampleUbuntu") == expected


@pytest.mark.parametrize("supplied", [
    r"\\wsl.localhost\OtherUbuntu\home\r1.fq",
    "/wsl.localhost/ExampleUbuntu/home/r1.fq",
    r"\\server\share\r1.fq",
    "C:reads/r1.fq",
])
def test_ambiguous_or_unreachable_wsl_paths_are_rejected(supplied) -> None:
    with pytest.raises(ValueError):
        wsl_input_path(supplied, "ExampleUbuntu")


def test_wsl_runner_uses_project_distribution() -> None:
    root = Path(r"\\wsl.localhost\ExampleUbuntu\home\example\project")
    config = default_config("path_check", root)
    command = build_snakemake_command(root, config, mode="dry-run", use_wsl=True)
    assert command.command[command.command.index("-d") + 1] == "ExampleUbuntu"
    with pytest.raises(RuntimeError, match="does not match"):
        build_snakemake_command(root, config, use_wsl=True, distro="OtherUbuntu")


def test_missing_file_still_fails_and_pending_download_still_passes(tmp_path) -> None:
    config = default_config("path_check", tmp_path)
    frame = pd.DataFrame([{"sample_id": "s1", "condition": "control", "layout": "single",
                           "fastq_1": "data/raw/not_downloaded.fastq"}])
    config.input.type = "fastq"
    assert any("FASTQ R1 does not exist" in m["message"] for m in input_validation_messages(config, tmp_path, frame))
    config.input.type = "sra"
    assert not any("FASTQ R1 does not exist" in m["message"] for m in input_validation_messages(config, tmp_path, frame))
