from __future__ import annotations

from app.core.readiness import ReadinessItem, _core_tools_present, check_readiness, check_wsl_bulkseq_environment, has_wsl_core_environment, missing_python_packages, next_readiness_actions, readiness_summary


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
