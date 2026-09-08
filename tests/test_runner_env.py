from __future__ import annotations

import re
from pathlib import Path

from app.core.snakemake_runner import (
    SnakemakeCommand,
    SnakemakeRunner,
    native_mamba_root,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _runner(use_wsl: bool) -> SnakemakeRunner:
    command = SnakemakeCommand(["snakemake"], "snakemake", use_wsl=use_wsl)
    return SnakemakeRunner(REPO_ROOT, command)


def test_native_child_env_exports_the_mamba_root(monkeypatch):
    # Rule shells dereference $MAMBA_ROOT_PREFIX under `set -u`; a native run inherits it from
    # nothing, so the runner has to supply it.
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    env = _runner(use_wsl=False)._child_env()
    assert env["MAMBA_ROOT_PREFIX"] == str(Path.home() / "micromamba")

    # An explicitly configured root passes through byte-identical (no re-normalisation).
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", "/opt/mamba")
    assert _runner(use_wsl=False)._child_env()["MAMBA_ROOT_PREFIX"] == "/opt/mamba"


def test_wsl_child_env_is_left_to_the_login_shell(monkeypatch):
    # The WSL branch builds its own environment inside _wrap_wsl; injecting a Windows-side
    # value here would put a Windows path in front of the Linux one.
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    assert "MAMBA_ROOT_PREFIX" not in _runner(use_wsl=True)._child_env()


def test_runner_default_matches_the_default_written_into_the_rules(monkeypatch):
    """The Python default and the shell default must be the same string.

    Derived from the rules rather than restated: any drift in either place fails here
    instead of surfacing as an unbound variable mid-run.
    """
    monkeypatch.delenv("MAMBA_ROOT_PREFIX", raising=False)
    sources = [
        (REPO_ROOT / "workflow" / "rules" / name).read_text(encoding="utf-8")
        for name in ("trimming.smk", "reference.smk", "qc.smk", "rrna.smk")
    ]
    defaults = {
        match
        for text in sources
        for match in re.findall(r"\$\{\{MAMBA_ROOT_PREFIX:-([^}]+)\}\}", text)
    }
    assert defaults, "no MAMBA_ROOT_PREFIX expansion found in the rules; the scan is broken"
    assert defaults == {"$HOME/micromamba"}
    assert native_mamba_root() == Path.home() / "micromamba"


def test_rules_never_dereference_the_mamba_root_bare():
    # `$MAMBA_ROOT_PREFIX` without a default aborts a native run under `set -u`.
    for path in sorted((REPO_ROOT / "workflow" / "rules").glob("*.smk")):
        text = path.read_text(encoding="utf-8")
        bare = re.findall(r"\$MAMBA_ROOT_PREFIX", text)
        assert not bare, f"{path.name} dereferences MAMBA_ROOT_PREFIX without a default"
