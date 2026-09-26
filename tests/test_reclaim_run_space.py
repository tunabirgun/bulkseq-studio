from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _runtime import bash_runtime

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reclaim_run_space.sh"


def _bash_or_skip():
    runtime = bash_runtime()
    if runtime is None:
        pytest.skip("no bash runtime: install WSL2 with a distribution on Windows")
    return runtime


def _project(root: Path, input_type: str, with_results: bool) -> Path:
    (root / "config").mkdir(parents=True)
    # The Windows application writes the configuration with CRLF line endings.
    (root / "config" / "config.yaml").write_bytes(
        f"project:\r\n  name: demo\r\ninput:\r\n  type: {input_type}\r\n  layout: unknown\r\n".encode())
    if with_results:
        (root / "results" / "deseq2").mkdir(parents=True)
        (root / "results" / "deseq2" / "deseq2_results.csv").write_text("gene_id,padj\nG1,0.01\n")
    return root


@pytest.mark.parametrize(("input_type", "with_results", "refuses"), [
    ("microarray", True, False),
    ("deseq2_results", True, False),
    ("sra", True, True),
    ("fastq", True, True),
    ("microarray", False, True),
])
def test_required_outputs_follow_the_input_route(tmp_path, input_type, with_results, refuses) -> None:
    bash, as_path = _bash_or_skip()
    project = _project(tmp_path / "project", input_type, with_results)
    completed = subprocess.run([*bash, as_path(SCRIPT), as_path(project)],
                               capture_output=True, text=True, timeout=120, check=False)
    output = completed.stdout + completed.stderr
    assert (completed.returncode == 1) is refuses, output
    assert ("Refusing to delete" in output) is refuses, output
    assert ("MISSING  results/counts/counts.txt" in output) is (input_type in {"sra", "fastq"}), output
