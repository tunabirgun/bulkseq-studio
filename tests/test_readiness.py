from __future__ import annotations

import sys

import pytest

from app.core.readiness import ReadinessItem, _core_tools_present, check_readiness, check_wsl_bulkseq_environment, has_native_core_environment, has_wsl_core_environment, missing_python_packages, next_readiness_actions, readiness_summary


def test_readiness_check_reports_python() -> None:
    items = check_readiness()
    names = {item.name for item in items}
    assert "Python" in names
    assert "PySide6" in names
    assert isinstance(missing_python_packages(), list)
    assert "Python" in readiness_summary(items)


def test_wsl_readiness_probe_is_nonfatal() -> None:
    items = check_wsl_bulkseq_environment()
    assert items
    assert items[0].name.startswith("WSL")


@pytest.mark.skipif(sys.platform != "win32", reason="WSL install guidance is Windows-only; Linux returns the native-activation hint")
def test_next_action_points_to_core_env_when_bulkseq_missing() -> None:
    items = [
        ReadinessItem("wsl", "PASS", "", ""),
        ReadinessItem("WSL distribution", "PASS", "", ""),
        ReadinessItem("WSL micromamba", "PASS", "", ""),
        ReadinessItem("WSL env:bulkseq", "REVIEW_REQUIRED", "", ""),
    ]
    assert "Install/Repair Core WSL Env" in next_readiness_actions(items)[0]
    assert not has_wsl_core_environment(items)


@pytest.mark.skipif(sys.platform != "win32", reason="WSL distribution guidance is Windows-only")
def test_next_action_points_to_distro_when_wsl_present_but_no_distro() -> None:
    # wsl.exe is installed but no distribution starts (a missing/broken ext4.vhdx): the guidance
    # must route to installing a distribution, not to the in-WSL micromamba step that would fail.
    items = [
        ReadinessItem("wsl", "PASS", "", ""),
        ReadinessItem("WSL distribution", "REVIEW_REQUIRED", "will not start", ""),
    ]
    assert "Install Ubuntu distribution" in next_readiness_actions(items)[0]


def test_core_tools_present_requires_all_core_tools() -> None:
    paths = {tool: f"/env/bin/{tool}" for tool in ("snakemake", "fastqc", "multiqc", "fastp", "STAR", "featureCounts", "samtools")}
    assert _core_tools_present(paths)
    paths.pop("samtools")
    assert not _core_tools_present(paths)


def test_check_readiness_native_skips_wsl_probe(monkeypatch) -> None:
    # On a non-Windows host the WSL probe must not run and must not emit "WSL ..." noise;
    # the wsl tool is reported as not-applicable rather than missing.
    monkeypatch.setattr(sys, "platform", "linux")
    items = check_readiness()
    assert not any(item.name.startswith("WSL") for item in items)
    wsl = next(item for item in items if item.name == "wsl")
    assert wsl.status == "PASS"
    assert "not applicable" in wsl.detail


def test_has_native_core_environment() -> None:
    core = ("snakemake", "STAR", "featureCounts", "samtools", "fastp", "fastqc", "multiqc")
    items = [ReadinessItem(name, "PASS", f"/usr/bin/{name}", "") for name in core]
    assert has_native_core_environment(items)
    assert not has_native_core_environment([it for it in items if it.name != "samtools"])


def test_native_readiness_actions_point_to_local_env(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    action = next_readiness_actions([ReadinessItem("Rscript", "PASS", "/usr/bin/Rscript", "")])[0]
    assert "bulkseq" in action


_FULL_LOG_PATHS = {t: f"/home/u/micromamba/envs/bulkseq/bin/{t}"
                   for t in ("snakemake", "fastqc", "multiqc", "fastp", "STAR",
                             "featureCounts", "samtools", "hisat2", "salmon", "aria2c")}


class _P:
    def __init__(self, rc, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _patch_wsl(monkeypatch, *, recheck_rc):
    # micromamba present; env-prefix probe FAILS; on-disk re-check of a recorded tool -> recheck_rc.
    import app.core.readiness as R
    monkeypatch.setattr(R.shutil, "which", lambda x: "/usr/bin/wsl" if x == "wsl" else None)
    monkeypatch.setattr(R, "_tool_paths_from_install_log", lambda *a, **k: dict(_FULL_LOG_PATHS))

    def fake_run_wsl(distro, cmd, timeout=None):
        if ".local/bin/micromamba" in cmd:
            return _P(0, "/home/u/.local/bin/micromamba")
        if cmd.startswith("test -x") and "envs/bulkseq" in cmd:
            return _P(recheck_rc)              # the new on-disk re-check
        return _P(1)                            # env-prefix probe fails (env dir absent to probe)
    monkeypatch.setattr(R, "_run_wsl", fake_run_wsl)


def test_stale_install_log_does_not_report_deleted_env_as_pass(monkeypatch) -> None:
    # Rebuild-from-scratch deleted the env then the install failed, leaving a stale success block in
    # the log. The env-prefix probe fails AND the recorded tool is gone from disk -> must NOT PASS.
    _patch_wsl(monkeypatch, recheck_rc=1)
    items = check_wsl_bulkseq_environment()
    env = next(i for i in items if i.name.startswith("WSL env:"))
    assert env.status == "REVIEW_REQUIRED", env


def test_probe_error_on_real_env_still_trusts_install_log(monkeypatch) -> None:
    # The fallback's original purpose: the env-prefix probe errored (e.g. transient) but the env is
    # really there (recorded tool IS on disk) -> keep reporting PASS from the setup log.
    _patch_wsl(monkeypatch, recheck_rc=0)
    items = check_wsl_bulkseq_environment()
    env = next(i for i in items if i.name.startswith("WSL env:"))
    assert env.status == "PASS", env


def test_validate_reference_empty_field_fails_cleanly() -> None:
    from pathlib import Path
    from app.core.reference_manager import validate_reference
    # Path("") == Path(".") exists as a dir; the guard must FAIL, not slip through to open(".").
    for g, a in [(Path(""), Path("")), (Path("."), Path("."))]:
        msgs = validate_reference(g, a)
        assert msgs and all(m["status"] == "FAIL" for m in msgs)
