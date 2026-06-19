from __future__ import annotations

from app.core.resources import SystemResources, recommend_profile


def test_recommend_profile_balanced() -> None:
    system = SystemResources("Windows", "CPU", 8, 16, 32, 20, 100, "C:/tmp", True, False, False, False)
    rec = recommend_profile(system, "balanced")
    assert rec["total_threads"] == 12
    assert rec["total_memory_gb"] <= 24
