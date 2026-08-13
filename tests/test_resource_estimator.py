from __future__ import annotations

import subprocess

from app.core import resources
from app.core.resources import (
    SystemResources,
    _parse_lscpu_physical_cores,
    _parse_wsl_probe,
    recommend_profile,
    recommend_rule_threads,
)


def test_profiles_fall_back_to_host_logical_cpus() -> None:
    system = SystemResources("Windows", "CPU", 8, 16, 32, 20, 100, "C:/tmp", True, False, False, False)
    assert system.wsl_physical_cores == 0
    expected_threads = {"low": 7, "balanced": 12, "high": 14}
    for profile, threads in expected_threads.items():
        rec = recommend_profile(system, profile)
        assert rec["total_threads"] == threads
        assert rec["total_memory_gb"] <= 30


def test_parse_wsl_topology_counts_unique_socket_core_pairs() -> None:
    topology = [f"{core},0" for core in range(12) for _thread in range(2)]
    stdout = "\n".join(
        ["login banner", "RAM=33554432", "CPU=24", "LSCPU_BEGIN", "# CORE,SOCKET"]
        + topology
        + ["LSCPU_END"]
    )
    ram, logical, physical = _parse_wsl_probe(stdout)
    assert (ram, logical, physical) == (32.0, 24, 12)
    assert _parse_lscpu_physical_cores(topology) == 12


def test_parse_wsl_topology_rejects_malformed_or_impossible_values() -> None:
    malformed = "\n".join(
        [
            "RAM=not-a-number",
            "CPU=4",
            "LSCPU_BEGIN",
            "broken",
            "1,not-a-socket",
            "-1,0",
            "LSCPU_END",
        ]
    )
    assert _parse_wsl_probe(malformed) == (0.0, 4, 0)
    impossible = "\n".join(
        ["RAM=1048576", "CPU=2", "LSCPU_BEGIN", "0,0", "1,0", "2,0", "LSCPU_END"]
    )
    assert _parse_wsl_probe(impossible) == (1.0, 2, 0)


def test_wsl_probe_failure_returns_zero_caps(monkeypatch) -> None:
    monkeypatch.setattr(resources.sys, "platform", "win32")

    def fail(*_args, **_kwargs):
        raise subprocess.SubprocessError("probe failed")

    monkeypatch.setattr(resources.subprocess, "run", fail)
    assert resources._wsl_caps() == (0.0, 0, 0)


def test_profiles_use_wsl_logical_cpus_and_memory_on_hybrid_cpu() -> None:
    expected = {
        "low": (10, 17),
        "balanced": (18, 23),
        "high": (21, 29),
    }
    for guest_physical in (12, 0):
        system = SystemResources(
            "Windows", "Hybrid CPU", 20, 28, 64, 48, 100, "C:/tmp",
            True, False, False, False,
            wsl_ram_gb=31.2, wsl_cpus=24, wsl_physical_cores=guest_physical,
        )
        for profile, (threads, memory_gb) in expected.items():
            recommendation = recommend_profile(system, profile)
            assert recommendation["total_threads"] == threads
            assert recommendation["total_memory_gb"] == memory_gb
            assert recommendation["star_align_threads"] == recommend_rule_threads(threads)["star_align"]
            assert recommendation["star_align_memory_gb"] == min(memory_gb, 24)


def test_rule_threads_derive_two_star_workers_from_global_pool() -> None:
    expected_star = {1: 1, 4: 4, 8: 4, 10: 5, 18: 9, 20: 10, 21: 10, 24: 12}
    for total, star_threads in expected_star.items():
        rules = recommend_rule_threads(total)
        assert rules["star_align"] == star_threads
        assert rules["star_align"] <= total
        if total >= 8:
            assert 2 * star_threads <= total
        assert all(1 <= value <= total for value in rules.values())
