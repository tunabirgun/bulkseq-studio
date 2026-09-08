from __future__ import annotations

import base64
import sys

import pytest

from app.core.readiness import ReadinessItem, _core_tools_present, check_readiness, check_wsl_bulkseq_environment, has_native_core_environment, has_wsl_core_environment, missing_python_packages, next_readiness_actions, readiness_summary


def test_readiness_check_reports_python() -> None:
    items = check_readiness()
    names = {item.name for item in items}
    assert "Python" in names
    assert "PySide6" in names
    assert "numpy" in names
    assert isinstance(missing_python_packages(), list)
    assert "Python" in readiness_summary(items)


def test_readiness_probes_direct_companion_commands() -> None:
    from app.core.readiness import BIOINFORMATICS_TOOLS, WSL_TOOLS
    for command in ("gtfToGenePred", "geneBody_coverage.py", "hisat2-build", "bowtie2", "perl"):
        assert command in BIOINFORMATICS_TOOLS
        assert command in WSL_TOOLS


def test_run_wsl_base64_transport_round_trips_complex_probe(monkeypatch) -> None:
    # wsl.exe mangles inline loops, quotes, and substitutions. The probe must cross that boundary
    # only as a base64 payload and decode to the exact original script inside WSL.
    import app.core.readiness as readiness

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return _P(0, "OK")

    monkeypatch.setattr(readiness.subprocess, "run", fake_run)
    probe = 'for t in "a b" c; do printf "%s:%s\\n" "$t" "$(command -v "$t")"; done'
    result = readiness._run_wsl("Ubuntu-24.04", probe)
    assert result.returncode == 0
    command = captured["command"]
    assert probe not in command
    inner = command[-1]
    encoded = inner.removeprefix("echo ").split(" | base64 -d | bash", 1)[0]
    assert base64.b64decode(encoded).decode("utf-8") == probe


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


def test_full_only_tool_split_matches_the_setup_script() -> None:
    # The core/full split has to mean the same thing in both places: readiness decides what a
    # core environment may legitimately lack, the setup script decides what it installs.
    import re
    from pathlib import Path
    from app.core.readiness import FULL_ONLY_TOOLS

    script = (Path(__file__).resolve().parents[1] / "scripts" / "setup_wsl_bioenv.sh").read_text(
        encoding="utf-8")
    match = re.search(r"FULL_ONLY_PROBE_TOOLS=\(([^)]*)\)", script)
    assert match, "setup_wsl_bioenv.sh no longer declares FULL_ONLY_PROBE_TOOLS"
    assert set(match.group(1).split()) == set(FULL_ONLY_TOOLS)


def test_core_profile_does_not_report_the_missing_r_stack_as_broken() -> None:
    from app.core.readiness import _r_packages_item, _tool_item, installed_profile

    assert installed_profile("core\n", rscript_present=False) == "core"
    assert installed_profile(None, rscript_present=True) == "full"      # pre-marker full install
    assert installed_profile(None, rscript_present=False) == "core"
    assert installed_profile("full", rscript_present=False) == "full"   # marker beats inference

    core_r = _r_packages_item("WSL Rscript", "", False, "core")
    assert core_r.status == "WARNING", core_r
    core_tool = _tool_item("WSL Rscript", "Rscript", "not found", False, "", "core")
    assert core_tool.status == "WARNING", core_tool
    # Negative control: the same absence on a FULL environment is a real failure.
    assert _tool_item("WSL Rscript", "Rscript", "not found", False, "", "full").status == "REVIEW_REQUIRED"
    assert _r_packages_item("WSL R packages", "cannot load: GO.db", False, "full").status == "REVIEW_REQUIRED"
    # A core-only tool that is not full-only still fails on a core environment.
    assert _tool_item("WSL STAR", "STAR", "not found", False, "", "core").status == "REVIEW_REQUIRED"


@pytest.mark.skipif(sys.platform != "win32", reason="the rebuild advice is the WSL branch")
def test_core_environment_is_never_told_to_rebuild_from_scratch() -> None:
    core = [ReadinessItem(n, "PASS", "", "") for n in
            ("wsl", "WSL distribution", "WSL micromamba", "WSL env:bulkseq", "WSL snakemake",
             "WSL fastqc", "WSL multiqc", "WSL fastp", "WSL STAR", "WSL featureCounts",
             "WSL samtools", "WSL salmon", "WSL gffread", "WSL hisat2")]
    core += [ReadinessItem("WSL Rscript", "WARNING", "not installed in the core environment", ""),
             ReadinessItem("WSL R packages", "WARNING", "not installed in the core environment", "")]
    actions = next_readiness_actions(core)
    assert not any("Rebuild from scratch" in a for a in actions), actions
    assert any("Install Full R/DESeq2 Stack" in a for a in actions), actions
    # Negative control: a full environment whose stack will not load still gets the rebuild.
    broken = [i for i in core if i.name not in ("WSL Rscript", "WSL R packages")]
    broken += [ReadinessItem("WSL Rscript", "PASS", "", ""),
               ReadinessItem("WSL R packages", "REVIEW_REQUIRED", "cannot load: GO.db", "")]
    assert any("Rebuild from scratch" in a for a in next_readiness_actions(broken))


def test_snakemake_is_probed_through_the_environment_bin(monkeypatch, tmp_path) -> None:
    # An unactivated shell must not report a correct environment's snakemake missing.
    from pathlib import Path

    import app.core.readiness as R

    env_bin = tmp_path / "envs" / "bulkseq" / "bin"
    env_bin.mkdir(parents=True)
    exe = env_bin / ("snakemake.exe" if sys.platform.startswith("win") else "snakemake")
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(R, "_env_search_path", lambda: str(env_bin))
    found = R._which_in_env("snakemake")  # Windows resolves PATHEXT and upper-cases it
    assert found is not None and Path(found).parent == env_bin
    monkeypatch.setattr(R, "_env_search_path", lambda: str(tmp_path / "empty"))
    assert R._which_in_env("snakemake") is None


def test_accepted_wsl_env_prefix_is_the_one_a_run_uses() -> None:
    from app.constants import WSL_MAMBA_ROOT
    from app.core.readiness import _wsl_env_prefix_command

    command = _wsl_env_prefix_command("bulkseq")
    assert f"{WSL_MAMBA_ROOT}/envs/bulkseq" in command
    # A prefix no run path can reach must not be accepted as a ready environment.
    assert ".local/share/mamba" not in command


def test_readiness_counts_items_not_cards() -> None:
    from app.core.readiness import readiness_counts

    items = [ReadinessItem("a", "PASS", "", ""), ReadinessItem("b", "PASS", "", ""),
             ReadinessItem("perl", "REVIEW_REQUIRED", "not found", ""),
             ReadinessItem("mamba", "WARNING", "optional", "")]
    assert readiness_counts(items) == (2, 3)
    assert readiness_counts([i for i in items if i.status != "REVIEW_REQUIRED"]) == (2, 2)


def test_validate_reference_empty_field_fails_cleanly() -> None:
    from pathlib import Path
    from app.core.reference_manager import validate_reference
    # Path("") == Path(".") exists as a dir; the guard must FAIL, not slip through to open(".").
    for g, a in [(Path(""), Path("")), (Path("."), Path("."))]:
        msgs = validate_reference(g, a)
        assert msgs and all(m["status"] == "FAIL" for m in msgs)
