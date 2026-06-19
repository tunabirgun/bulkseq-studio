from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_cli_create_validate(tmp_path) -> None:
    workdir = tmp_path / "benchmark_cli" / uuid4().hex
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.benchmark_cli",
            "create",
            "--benchmark",
            "pasilla_paired_subset",
            "--workdir",
            str(workdir),
            "--name",
            "pasilla_cli",
            "--validate",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,  # so `-m app.benchmark_cli` resolves regardless of test CWD
    )
    assert result.returncode == 0, result.stderr
    assert "Runtime estimate:" in result.stdout
    assert (workdir / "pasilla_cli" / "checks" / "01_input_validation.json").exists()
