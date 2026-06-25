from __future__ import annotations

import sys

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


def test_next_action_points_to_core_env_when_bulkseq_missing() -> None:
    items = [
        ReadinessItem("wsl", "PASS", "", ""),
        ReadinessItem("WSL micromamba", "PASS", "", ""),
        ReadinessItem("WSL env:bulkseq", "REVIEW_REQUIRED", "", ""),
    ]
    assert "Install/Repair Core WSL Env" in next_readiness_actions(items)[0]
    assert not has_wsl_core_environment(items)


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
