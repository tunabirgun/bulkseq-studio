from __future__ import annotations

from pathlib import Path

from app.core.config_models import default_config
from app.core.snakemake_runner import build_snakemake_command


def test_snakemake_command_uses_project_workflow_snakefile() -> None:
    cfg = default_config("demo", Path("demo"))
    command = build_snakemake_command(Path("demo"), cfg, mode="dry-run")
    assert command.command[:4] == ["snakemake", "-n", "--snakefile", "workflow/Snakefile"]
    assert "config/config.yaml" in command.command


def test_unlock_command_uses_project_workflow_snakefile() -> None:
    cfg = default_config("demo", Path("demo"))
    command = build_snakemake_command(Path("demo"), cfg, mode="unlock")
    assert command.command[:3] == ["snakemake", "--snakefile", "workflow/Snakefile"]
    assert "--unlock" in command.command


def test_wsl_command_quotes_project_paths_with_spaces() -> None:
    cfg = default_config("demo", Path("C:/Users/Tuna/Desktop/BulkSeq Studio/demo"))
    command = build_snakemake_command(Path("C:/Users/Tuna/Desktop/BulkSeq Studio/demo"), cfg, mode="dry-run", use_wsl=True)
    inner = command.command[-1]
    assert "cd '/mnt/c/Users/Tuna/Desktop/BulkSeq Studio/demo'" in inner
    assert "snakemake -n --snakefile workflow/Snakefile" in inner
    assert '"cd \'/mnt/c/Users/Tuna/Desktop/BulkSeq Studio/demo\'' in command.display


def test_wsl_command_activates_micromamba_env() -> None:
    # The login shell does not put the bulkseq env on PATH, so the runner must
    # invoke snakemake through `micromamba run -n bulkseq` (the same mechanism the
    # readiness check probes). This is the bug a string-only test would miss.
    cfg = default_config("demo", Path("C:/work/demo"))
    command = build_snakemake_command(Path("C:/work/demo"), cfg, mode="run", use_wsl=True)
    inner = command.command[-1]
    # The micromamba path is double-quoted (preserving $HOME expansion) to be safe
    # against spaces, so the env activation reads ".../micromamba" run -n bulkseq.
    assert 'micromamba" run -n bulkseq snakemake' in inner
    assert "MAMBA_ROOT_PREFIX" in inner
    assert command.command[:3] == ["wsl", "--", "bash"]


def test_native_command_has_no_use_conda() -> None:
    # No rule declares a conda: directive; --use-conda would be a no-op and is
    # intentionally omitted.
    cfg = default_config("demo", Path("demo"))
    command = build_snakemake_command(Path("demo"), cfg, mode="run")
    assert "--use-conda" not in command.command
