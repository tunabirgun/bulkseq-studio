from __future__ import annotations

from app.core.setup_installer import build_wsl_admin_install_command, build_wsl_bioenv_command


def test_wsl_admin_install_command_uses_uac() -> None:
    command = build_wsl_admin_install_command()
    joined = " ".join(command)
    assert command[0].endswith("launch_wsl_setup_admin.bat")
    assert "Ubuntu" in joined


def test_wsl_bioenv_command_runs_repo_script() -> None:
    command = build_wsl_bioenv_command()
    assert command[:5] == ["wsl", "-d", "Ubuntu", "--", "bash"]
    assert "setup_wsl_bioenv.sh" in command[-1]
    assert "bulkseq" in command[-1]
    assert "core" in command[-1]
