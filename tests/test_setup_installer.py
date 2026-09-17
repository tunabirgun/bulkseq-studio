from __future__ import annotations

import hashlib
import importlib.util
import os
import platform
import re
import subprocess
from pathlib import Path

import pytest

from _runtime import bash_runtime
from app.core.setup_installer import (build_bioenv_kill_command, build_native_bioenv_command,
                                      build_wsl_admin_install_command, build_wsl_bioenv_command,
                                      launch_native_bioenv_install, new_setup_run_tag,
                                      stop_bioenv_install, wsl_bioenv_script)


def _bash_or_skip():
    runtime = bash_runtime()
    if runtime is None:
        pytest.skip("no runnable bash environment")
    return runtime


def _spec_fixture_sections(marker_text: str | None = None) -> tuple[str, str, str]:
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")

    selection_start = text.index('FIXED_LOCK_ENV_FILE=""\ncase "$PROFILE" in\n')
    selection_end = text.index('\nesac\n', selection_start) + len('\nesac\n')
    selection = text[selection_start:selection_end]
    assert selection.startswith('FIXED_LOCK_ENV_FILE=""\ncase "$PROFILE" in\n')
    assert selection.endswith("\nesac\n")

    install_start = text.index('INSTALLED_ENV_FILE="$ENV_FILE"\n')
    install_end = text.index("\n# Stage 2b (full profile)", install_start)
    install = text[install_start:install_end]
    assert install.startswith('INSTALLED_ENV_FILE="$ENV_FILE"\n')
    assert install.rstrip().endswith("fi")

    marker_start = text.index('  spec_basename="$(basename "$INSTALLED_ENV_FILE")"\n')
    marker_end_line = '  echo "Recorded installed spec \'$spec_basename\' (source: $spec_source) in $ENV_PREFIX/.bulkseq_spec"\n'
    marker_end = text.index(marker_end_line, marker_start) + len(marker_end_line)
    marker = marker_text if marker_text is not None else text[marker_start:marker_end]
    for section in (selection, install, marker):
        for forbidden in ("Stage 1/3", "sudo -n true", "bootstrap_with_python3", "acquire_lock"):
            assert forbidden not in section, f"spec fixture reached setup content: {forbidden}"
    return selection, install, marker


def _run_spec_fixture(tmp_path: Path, *, platform_name: str, profile: str,
                      fail_fixed_lock: bool, expected_source: str,
                      marker_text: str | None = None) -> tuple[subprocess.CompletedProcess[str], Path]:
    runtime, convert = _bash_or_skip()
    repo = tmp_path / "synthetic repo"
    envs = repo / "workflow" / "envs"
    envs.mkdir(parents=True)
    payloads = {
        "bulkseq.lock.yaml": b"fixed linux-64 lock\n",
        "bulkseq_full.yaml": b"floating full spec\n",
        "bulkseq_core.yaml": b"core spec\n",
    }
    for name, payload in payloads.items():
        (envs / name).write_bytes(payload)
    expected_name = {
        ("core", False): "bulkseq_core.yaml",
        ("full", False): "bulkseq_full.yaml" if platform_name == "linux-aarch64" else "bulkseq.lock.yaml",
        ("full", True): "bulkseq_full.yaml",
    }[(profile, fail_fixed_lock)]
    expected_sha = hashlib.sha256(payloads[expected_name]).hexdigest()
    selection, install, marker = _spec_fixture_sections(marker_text)
    fixture = tmp_path / "spec-marker-fixture.sh"
    fixture.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'REPO_DIR="$1"\nMAMBA_ROOT="$2"\nPROFILE="$3"\nMM_PLATFORM="$4"\n'
        'FAIL_FIXED_LOCK="$8"\n'
        'ENV_NAME="synthetic"\nHOME="$2/home"\nexport HOME\n'
        'mkdir -p "$MAMBA_ROOT/envs/$ENV_NAME"\n'
        + selection
        + 'attempt_install() {\n  if [ "$FAIL_FIXED_LOCK" = "1" ] && [ "$1" = "$FIXED_LOCK_ENV_FILE" ]; then return 1; fi\n  return 0\n}\n'
        + install
        + 'ENV_PREFIX="$MAMBA_ROOT/envs/$ENV_NAME"\n'
        + marker
        + 'IFS= read -r recorded_file < "$ENV_PREFIX/.bulkseq_spec"\n'
        + 'IFS= read -r recorded_sha < <(sed -n "2p" "$ENV_PREFIX/.bulkseq_spec")\n'
        + 'IFS= read -r recorded_source < <(sed -n "3p" "$ENV_PREFIX/.bulkseq_spec")\n'
        + '[ "$recorded_file" = "$5" ] || { echo "file: expected $5, got $recorded_file" >&2; exit 41; }\n'
        + '[ "$recorded_sha" = "$6" ] || { echo "sha256: expected $6, got $recorded_sha" >&2; exit 42; }\n'
        + '[ "$recorded_source" = "$7" ] || { echo "source: expected $7, got $recorded_source" >&2; exit 43; }\n'
        + "exit 0\n",
        encoding="utf-8", newline="\n",
    )
    completed = subprocess.run(
        [*runtime, convert(fixture), convert(repo), convert(tmp_path / "mamba"), profile,
         platform_name, expected_name, expected_sha, expected_source,
         "1" if fail_fixed_lock else "0"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return completed, tmp_path / "mamba" / "envs" / "synthetic" / ".bulkseq_spec"


def test_installed_spec_marker_classifies_the_actual_spec_on_every_branch(tmp_path: Path) -> None:
    cases = (
        ("linux-64", "core", False, "core"),
        ("linux-64", "full", False, "lock"),
        ("linux-64", "full", True, "fallback"),
        ("linux-aarch64", "full", False, "fallback"),
    )
    for index, (platform_name, profile, fail_lock, expected_source) in enumerate(cases):
        completed, _ = _run_spec_fixture(
            tmp_path / str(index), platform_name=platform_name, profile=profile,
            fail_fixed_lock=fail_lock, expected_source=expected_source,
        )
        assert completed.returncode == 0, completed.stderr + completed.stdout


def test_arm_marker_would_fail_with_the_original_mutable_env_file_classification(tmp_path: Path) -> None:
    _, _, corrected = _spec_fixture_sections()
    fixed = '''  spec_source="fallback"
  if [ "$PROFILE" = "core" ]; then
    spec_source="core"
  elif [ "$INSTALLED_ENV_FILE" = "$FIXED_LOCK_ENV_FILE" ]; then
    spec_source="lock"
  fi'''
    original = '''  spec_source="fallback"
  if [ "$INSTALLED_ENV_FILE" = "$ENV_FILE" ]; then
    spec_source="lock"
    [ "$PROFILE" = "core" ] && spec_source="core"
  fi'''
    legacy = corrected.replace(fixed, original)
    assert legacy != corrected, "original classification block was not reconstructed"
    completed, _ = _run_spec_fixture(
        tmp_path, platform_name="linux-aarch64", profile="full",
        fail_fixed_lock=False, expected_source="fallback", marker_text=legacy,
    )
    assert completed.returncode == 43
    assert "source: expected fallback, got lock" in completed.stderr


def test_spec_marker_oracle_rejects_a_wrong_lock_classification(tmp_path: Path) -> None:
    completed, _ = _run_spec_fixture(
        tmp_path, platform_name="linux-aarch64", profile="full",
        fail_fixed_lock=False, expected_source="lock",
    )
    assert completed.returncode == 43
    assert "source: expected lock, got fallback" in completed.stderr


def test_generated_arm_fallback_marker_round_trips_through_production_reader(
        tmp_path: Path, monkeypatch) -> None:
    completed, marker = _run_spec_fixture(
        tmp_path, platform_name="linux-aarch64", profile="full",
        fail_fixed_lock=False, expected_source="fallback",
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    module_path = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_run_summary.py"
    spec = importlib.util.spec_from_file_location("phase15_make_run_summary", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("CONDA_PREFIX", str(marker.parent))
    expected_sha = hashlib.sha256(
        (tmp_path / "synthetic repo" / "workflow" / "envs" / "bulkseq_full.yaml").read_bytes()
    ).hexdigest()
    assert module.environment_spec() == {
        "file": "bulkseq_full.yaml", "sha256": expected_sha, "source": "fallback",
    }


def test_wsl_admin_install_command_uses_uac() -> None:
    command = build_wsl_admin_install_command()
    joined = " ".join(command)
    assert command[0].endswith("launch_wsl_setup_admin.bat")
    assert "Ubuntu" in joined


def test_wsl_bioenv_command_runs_repo_script() -> None:
    # Default distro is None so the command targets WSL's default distribution
    # (avoids hardcoding "Ubuntu" when the installed distro is e.g. Ubuntu-24.04).
    command = build_wsl_bioenv_command()
    assert command[:3] == ["wsl", "--exec", "env"]
    assert command[3].startswith("BULKSEQ_SETUP_LOG_DIR=")
    assert command[-4] == "bash"
    assert command[-3].endswith("setup_wsl_bioenv.sh")
    assert command[-2:] == ["bulkseq", "core"]


def test_wsl_bioenv_command_accepts_explicit_distro() -> None:
    command = build_wsl_bioenv_command(distro="Ubuntu-24.04")
    assert command[:5] == ["wsl", "-d", "Ubuntu-24.04", "--exec", "env"]


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
    # `wsl --exec env` passes each value as an argv item, so Windows paths and values
    # containing spaces/apostrophes never require fragile shell interpolation.
    import app.core.setup_installer as si

    awkward = "/mnt/c/Users/O'Brien/My Apps/BulkSeq Studio"
    script = awkward + "/scripts/setup_wsl_bioenv.sh"
    monkeypatch.setattr(si, "windows_to_wsl_path",
                        lambda p: script if str(p).endswith(".sh") else awkward + "/logs")
    command = si.build_wsl_bioenv_command(env_name="bulk'seq", profile="core profile")
    assert command == [
        "wsl", "--exec", "env", f"BULKSEQ_SETUP_LOG_DIR={awkward}/logs",
        "bash", script, "bulk'seq", "core profile",
    ]


def test_wsl_exec_transport_preserves_literal_log_and_profile_values(monkeypatch, tmp_path: Path) -> None:
    """A harmless real-WSL command proves `--exec env` preserves literal values."""
    import app.core.setup_installer as si
    from app.core.paths import windows_to_wsl_path

    _bash_or_skip()
    script = tmp_path / "setup script's name.sh"
    script.write_text(
        '#!/usr/bin/env bash\nmkdir -p "$BULKSEQ_SETUP_LOG_DIR"\nprintf "%s\\n" "$BULKSEQ_SETUP_LOG_DIR" "$1" "$2" > "$BULKSEQ_SETUP_LOG_DIR/received.txt"\n',
        encoding="utf-8", newline="\n",
    )
    log_dir = tmp_path / "user data" / "Tuna O'Brien" / "logs"
    monkeypatch.setattr(si, "wsl_bioenv_script", lambda: script)
    monkeypatch.setattr(si, "bioenv_setup_log_dir", lambda: log_dir)
    command = si.build_wsl_bioenv_command(env_name="bulk'seq", profile="core profile")
    if os.name == "nt":
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    else:
        completed = subprocess.run(command[2:], capture_output=True, text=True,
                                   timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    received = log_dir / "received.txt"
    assert received.read_text(encoding="utf-8").splitlines() == [
        windows_to_wsl_path(log_dir), "bulk'seq", "core profile",
    ]


def test_install_carries_a_run_tag_that_the_kill_command_targets() -> None:
    tag = new_setup_run_tag()
    command = build_wsl_bioenv_command(run_tag=tag, rebuild=True)
    assert f"{tag}=1" in command
    assert "BULKSEQ_REBUILD=1" in command
    kill = build_bioenv_kill_command(tag, distro="Ubuntu-24.04")
    assert kill[:4] == ["wsl", "-d", "Ubuntu-24.04", "--exec"]
    assert f"{tag}=1" in kill[-1] and "/proc/" in kill[-1]
    # No tag, no export: the run tag stays opt-in for callers that do not need Stop.
    assert not any(part.startswith("BULKSEQ_RUN_TAG") for part in build_wsl_bioenv_command())


@pytest.mark.skipif(platform.system() == "Darwin", reason="native Linux installer launcher")
def test_native_launcher_passes_the_same_user_log_override(monkeypatch, tmp_path: Path) -> None:
    import app.core.setup_installer as si

    captured: dict[str, object] = {}

    class FakePopen:
        def __init__(self, command, **kwargs) -> None:
            captured["command"] = command
            captured.update(kwargs)

    log_dir = tmp_path / "Tuna O'Brien" / "BulkSeq Studio" / "logs"
    monkeypatch.setattr(si, "bioenv_setup_log_dir", lambda: log_dir)
    monkeypatch.setattr(si.subprocess, "Popen", FakePopen)
    si.launch_native_bioenv_install(rebuild=True)
    assert captured["command"][-2:] == ["bulkseq", "core"]
    assert captured["env"]["BULKSEQ_SETUP_LOG_DIR"] == str(log_dir)
    assert captured["env"]["BULKSEQ_REBUILD"] == "1"


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


def _repair_function() -> str:
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    start = text.index("repair_attempted=0\n")
    end = text.index('\necho ""\necho "Configuring shell activation helper"', start)
    return text[start:end]


def _verification_repair_flow() -> str:
    text = wsl_bioenv_script().read_text(encoding="utf-8")
    start = text.index("if ! verify_environment; then\n")
    end = text.index("\n# Record which profile", start)
    return text[start:end]


def test_verification_repair_is_bounded_exact_spec_and_non_destructive() -> None:
    repair = _repair_function()
    stage3 = wsl_bioenv_script().read_text(encoding="utf-8").split(
        'echo "Stage 3/3: Verifying the $PROFILE environment"', 1
    )[1]
    assert '--force-reinstall -n "$ENV_NAME" -f "$INSTALLED_ENV_FILE"' in repair
    assert 'if [ "$repair_attempted" -ne 0 ]' in repair
    assert 'repair_attempted=1' in repair
    assert "post-link steps may download data again" in repair
    assert "remove_env" not in repair
    assert 'if [ "$PROFILE" = "full" ]' in stage3
    assert "r_stack_loads" in stage3


def test_force_reinstall_repairs_what_update_only_leaves_missing(tmp_path: Path) -> None:
    runtime, convert = _bash_or_skip()
    fake = tmp_path / "micromamba"
    target = tmp_path / "env" / "bin" / "salmon"
    spec = tmp_path / "bulkseq.lock.yaml"
    calls = tmp_path / "calls.txt"
    fake.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        'if [ "$1" = "env" ] && [ "$2" = "update" ]; then exit 0; fi\n'
        'if [ "$1" = "install" ] && [[ " $* " == *" --force-reinstall "* ]]; then\n'
        '  [ "${FAIL_FORCE:-0}" = "0" ] || exit 51\n'
        '  mkdir -p "$(dirname "$REPAIR_TARGET")"\n  : > "$REPAIR_TARGET"\n  exit 0\nfi\n'
        'exit 99\n',
        encoding="utf-8", newline="\n",
    )
    fake.chmod(0o755)
    fixture = tmp_path / "repair-fixture.sh"
    fixture.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'MICROMAMBA="$1"\nENV_NAME=bulkseq\nINSTALLED_ENV_FILE="$2"\n'
        'REPAIR_TARGET="$3"\nCALLS="$4"\nexport REPAIR_TARGET CALLS\n'
        + _repair_function()
        + '\n"$MICROMAMBA" env update --yes -n "$ENV_NAME" -f "$INSTALLED_ENV_FILE"\n'
        + '[ ! -e "$REPAIR_TARGET" ] || exit 41\n'
        + 'repair_installed_spec_once\n[ -e "$REPAIR_TARGET" ] || exit 42\n'
        + 'if repair_installed_spec_once; then exit 43; fi\n',
        encoding="utf-8", newline="\n",
    )
    completed = subprocess.run(
        [*runtime, convert(fixture), convert(fake), convert(spec), convert(target), convert(calls)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    invoked = calls.read_text(encoding="utf-8")
    assert "env update" in invoked
    assert invoked.count("--force-reinstall") == 1


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_failed_repair_or_failed_second_verification_exits_nonzero(
        tmp_path: Path, repair_succeeds: bool) -> None:
    runtime, convert = _bash_or_skip()
    events = tmp_path / "events.txt"
    fixture = tmp_path / "verification-flow.sh"
    fixture.write_text(
        '#!/usr/bin/env bash\nset -u\nPROFILE=full\nEVENTS="$1"\n'
        'verify_environment() { echo verify >> "$EVENTS"; return 1; }\n'
        f'repair_installed_spec_once() {{ echo repair >> "$EVENTS"; return {0 if repair_succeeds else 51}; }}\n'
        + _verification_repair_flow()
        + '\nexit 0\n',
        encoding="utf-8", newline="\n",
    )
    completed = subprocess.run(
        [*runtime, convert(fixture), convert(events)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 1, completed.stderr + completed.stdout
    observed = events.read_text(encoding="utf-8").splitlines()
    assert observed.count("repair") == 1
    assert observed.count("verify") == (2 if repair_succeeds else 1)
