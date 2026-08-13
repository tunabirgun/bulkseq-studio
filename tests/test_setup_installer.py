from __future__ import annotations

from app.core.setup_installer import build_wsl_admin_install_command, build_wsl_bioenv_command, wsl_bioenv_script


def test_wsl_admin_install_command_uses_uac() -> None:
    command = build_wsl_admin_install_command()
    joined = " ".join(command)
    assert command[0].endswith("launch_wsl_setup_admin.bat")
    assert "Ubuntu" in joined


def test_wsl_bioenv_command_runs_repo_script() -> None:
    # Default distro is None so the command targets WSL's default distribution
    # (avoids hardcoding "Ubuntu" when the installed distro is e.g. Ubuntu-24.04).
    command = build_wsl_bioenv_command()
    assert command[:4] == ["wsl", "--", "bash", "-lc"]
    assert "setup_wsl_bioenv.sh" in command[-1]
    assert "bulkseq" in command[-1]
    assert "core" in command[-1]


def test_wsl_bioenv_command_accepts_explicit_distro() -> None:
    command = build_wsl_bioenv_command(distro="Ubuntu-24.04")
    assert command[:4] == ["wsl", "-d", "Ubuntu-24.04", "--"]


def test_bioenv_script_bootstraps_micromamba_without_interactive_sudo() -> None:
    # The GUI runs this script with stdin=DEVNULL and no tty, so an interactive
    # sudo would dead-end. micromamba must install from the python3 standard
    # library, and the only sudo path must be gated by a non-interactive probe.
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    assert "python3" in text
    assert "micro.mamba.pm" in text
    assert "sudo -n true" in text
    guard = text.index("sudo -n true")
    for apt_call in ("apt-get update", "apt-get install"):
        assert apt_call in text
        assert text.index(apt_call) > guard
    # When nothing automatic works, the script prints the exact recovery command.
    assert "ACTION REQUIRED" in text


def test_stage3_is_profile_aware_and_executes_direct_companion_tools() -> None:
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    stage3 = text.split('echo "Stage 3/3: Verifying the $PROFILE environment"', 1)[1]
    assert "CORE_PROBE_TOOLS=(" in stage3
    assert "FULL_ONLY_PROBE_TOOLS=(ribodetector_cpu Rscript)" in stage3
    assert 'if [ "$PROFILE" = "full" ]' in stage3
    for command in ("gtfToGenePred", "geneBody_coverage.py", "hisat2-build", "bowtie2", "perl"):
        assert command in stage3
    assert 'run_limited 20 "$MICROMAMBA" run -n "$ENV_NAME" "$tool"' in stage3
    assert "path presence alone is never accepted" in stage3
    assert "import numpy, pandas, yaml" in stage3


def test_r_load_failure_is_non_destructive_without_explicit_rebuild() -> None:
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    stage2b = text.split("# Stage 2b (full profile)", 1)[1].split('echo "Configuring shell activation helper"', 1)[0]
    assert "ACTION REQUIRED" in stage2b
    assert "The existing environment was retained" in stage2b
    assert "remove_env" not in stage2b
    assert 'if [ "$REBUILD" = "1" ]' in stage2b
