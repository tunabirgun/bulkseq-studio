from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path

import pytest

from app.core.setup_installer import (build_bioenv_kill_command, build_native_bioenv_command,
                                      build_wsl_admin_install_command, build_wsl_bioenv_command,
                                      launch_native_bioenv_install, new_setup_run_tag,
                                      stop_bioenv_install, wsl_bioenv_script)


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


def test_install_command_survives_an_apostrophe_and_a_space_in_the_path(monkeypatch) -> None:
    # C:\Users\O'Brien\My Apps\... closed the hand-written single quotes and split the
    # command; every interpolation must be shell-quoted instead.
    import app.core.setup_installer as si

    import shlex

    awkward = "/mnt/c/Users/O'Brien/My Apps/BulkSeq Studio"
    script = awkward + "/scripts/setup_wsl_bioenv.sh"
    monkeypatch.setattr(si, "windows_to_wsl_path",
                        lambda p: script if str(p).endswith(".sh") else awkward)
    inner = si.build_wsl_bioenv_command(env_name="bulk'seq", profile="core")[-1]
    # A POSIX lexer must recover exactly the intended words from the quoted command.
    assert shlex.split(inner.replace("&&", " ")) == [
        "cd", awkward, "bash", script, "bulk'seq", "core"], inner


def test_install_carries_a_run_tag_that_the_kill_command_targets() -> None:
    tag = new_setup_run_tag()
    inner = build_wsl_bioenv_command(run_tag=tag)[-1]
    assert inner.startswith(f"export {tag}=1 && "), inner
    kill = build_bioenv_kill_command(tag, distro="Ubuntu-24.04")
    assert kill[:4] == ["wsl", "-d", "Ubuntu-24.04", "--exec"]
    assert f"{tag}=1" in kill[-1] and "/proc/" in kill[-1]
    # No tag, no export: the run tag stays opt-in for callers that do not need Stop.
    assert not build_wsl_bioenv_command()[-1].startswith("export BULKSEQ_RUN_TAG")


def test_kill_command_rejects_a_forged_tag() -> None:
    with pytest.raises(ValueError):
        build_bioenv_kill_command("; rm -rf /")


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX process groups")
def test_native_install_stop_reaches_the_whole_process_group(tmp_path) -> None:
    import os
    import signal
    import time

    marker = tmp_path / "alive"
    script = tmp_path / "setup_wsl_bioenv.sh"
    script.write_text(f"(while true; do touch {marker}; sleep 0.2; done) & wait\n", encoding="utf-8")
    proc = subprocess.Popen(["bash", str(script)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        stop_bioenv_install(proc, native=True)
        proc.wait(timeout=10)
        marker.unlink(missing_ok=True)
        time.sleep(0.6)
        assert not marker.exists(), "the install's children survived Stop"
    finally:
        if proc.poll() is None:  # pragma: no cover - cleanup
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def test_native_install_is_refused_on_macos(monkeypatch) -> None:
    # The setup script exits on Darwin; the app must say so instead of surfacing exit 1.
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    import app.core.setup_installer as si

    monkeypatch.setattr(si.platform, "system", lambda: "Darwin")
    with pytest.raises(NotImplementedError, match="macOS"):
        build_native_bioenv_command()
    with pytest.raises(NotImplementedError):
        launch_native_bioenv_install()


def test_setup_script_pins_and_verifies_micromamba() -> None:
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    url = re.search(r'MM_URL="([^"]+)"', text)
    assert url and not url.group(1).endswith("/latest"), "micromamba is fetched from a moving URL"
    version = re.search(r'MM_VERSION="([0-9][^"]*)"', text)
    assert version, "MM_VERSION pin is missing"
    assert f'micromamba/${{MM_PLATFORM}}/${{MM_VERSION}}' in text
    digests = re.findall(r'MM_SHA256="([0-9a-f]{64})"', text)
    assert len(digests) == 2 and len(set(digests)) == 2, "one SHA-256 per platform is required"
    # Verified before the binary is used, and moved into place atomically.
    assert "hashlib.sha256(data).hexdigest()" in text
    assert "os.replace(tmp, dest)" in text
    assert "verify_sha256" in text


def test_shell_scripts_are_shebanged_bom_free_utf8() -> None:
    # A UTF-8 BOM makes the kernel miss the shebang, and the resulting error is the first
    # line of the install log. Double-encoded text (mojibake) is checked in the same pass.
    for script in sorted((Path(__file__).resolve().parents[1] / "scripts").glob("*.sh")):
        raw = script.read_bytes()
        assert raw.startswith(b"#!"), f"{script.name} does not start with a shebang"
        text = raw.decode("utf-8")  # raises on any non-UTF-8 byte
        for marker in ("\u00e2\u20ac", "\u00c3\u0083", "\u00c2\u00a0"):
            assert marker not in text, f"{script.name} contains double-encoded text"


def test_r_load_failure_is_non_destructive_without_explicit_rebuild() -> None:
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    stage2b = text.split("# Stage 2b (full profile)", 1)[1].split('echo "Configuring shell activation helper"', 1)[0]
    assert "ACTION REQUIRED" in stage2b
    assert "The existing environment was retained" in stage2b
    assert "remove_env" not in stage2b
    assert 'if [ "$REBUILD" = "1" ]' in stage2b
