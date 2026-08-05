"""Execution profiles must not quietly contradict what the rules declare.

Two failure modes these guard against, both silent on a real cluster:

  * `cores` below the largest declared rule thread count. With a cluster executor and no
    `cores`, Snakemake reuses `jobs` as the per-rule thread ceiling, so alignment runs at a
    fraction of its declared parallelism and the run is far slower than estimated — with no
    error anywhere.
  * A profile naming `mem_mb` or `threads` under `set-resources`, or a global
    `resources: mem_mb`. Both override what a rule declares for itself, so the scheduler is
    told one thing while config.yaml and run_summary.json record another. On Slurm that
    means star_align (24 GB) can be submitted with a laptop-sized allocation and OOM-killed
    hours into a run.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / "workflow"
SITE_PROFILES = sorted((WORKFLOW / "profiles" / "site").glob("*/config.yaml"))


def _declared_rule_threads() -> dict[str, int]:
    """Largest thread count each rule asks for, read from the rule sources."""
    found: dict[str, int] = {}
    sources = list(WORKFLOW.rglob("*.smk")) + [WORKFLOW / "Snakefile"]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for name, default in re.findall(r"rule_threads\(\s*['\"](\w+)['\"]\s*,\s*(\d+)\s*\)", text):
            found[name] = max(found.get(name, 0), int(default))
    return found


def test_profiles_exist() -> None:
    names = {p.parent.name for p in SITE_PROFILES}
    assert {"slurm", "kubernetes"} <= names, f"missing execution profiles: {names}"


def test_rule_threads_are_discoverable() -> None:
    # If this breaks, the bound below is silently derived from nothing.
    threads = _declared_rule_threads()
    assert threads, "no rule_threads(...) declarations found; the parser is out of date"
    assert max(threads.values()) >= 8


@pytest.mark.parametrize("profile_path", SITE_PROFILES, ids=lambda p: p.parent.name)
def test_profile_cores_cover_the_widest_rule(profile_path: Path) -> None:
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    widest = max(_declared_rule_threads().values())
    assert "cores" in profile, (
        f"{profile_path.parent.name} sets no `cores`; Snakemake would then reuse `jobs` as "
        f"the per-rule thread ceiling and silently under-thread every alignment"
    )
    assert int(profile["cores"]) >= widest, (
        f"{profile_path.parent.name} caps cores at {profile['cores']} but a rule declares "
        f"{widest} threads"
    )


@pytest.mark.parametrize("profile_path", SITE_PROFILES, ids=lambda p: p.parent.name)
def test_profile_declares_an_executor(profile_path: Path) -> None:
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    assert profile.get("executor"), f"{profile_path.parent.name} declares no executor"


@pytest.mark.parametrize("profile_path", SITE_PROFILES, ids=lambda p: p.parent.name)
def test_profile_does_not_override_per_rule_resources(profile_path: Path) -> None:
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}

    overrides = profile.get("set-resources") or {}
    offending = [f"{rule}.{key}" for rule, spec in overrides.items()
                 if isinstance(spec, dict) for key in spec if key in ("mem_mb", "threads")]
    assert not offending, (
        f"{profile_path.parent.name} overrides per-rule resources ({offending}); the run "
        f"would be submitted with values the run summary does not record"
    )

    # A global mem_mb pool clamps every rule down under a cluster executor.
    globals_ = profile.get("resources") or []
    if isinstance(globals_, dict):
        globals_ = [f"{k}={v}" for k, v in globals_.items()]
    assert not [r for r in globals_ if str(r).startswith("mem_mb")], (
        f"{profile_path.parent.name} sets a global mem_mb; under a cluster executor "
        f"Snakemake clamps each rule's request down to it instead of refusing"
    )


def test_kubernetes_profile_states_the_container_requirement() -> None:
    # No rule declares `conda:` or `container:`, so Kubernetes cannot work without an image.
    # That has to be said in the file a user will copy, not only in the docs.
    text = (WORKFLOW / "profiles" / "site" / "kubernetes" / "config.yaml").read_text(encoding="utf-8")
    assert "container" in text.lower()
    assert "conda:" in text or "container-image" in text


def test_no_rule_declares_conda_or_container() -> None:
    # The premise behind the Kubernetes warning. If this ever becomes false, that profile's
    # guidance is stale and must be rewritten.
    for path in list(WORKFLOW.rglob("*.smk")) + [WORKFLOW / "Snakefile"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s{4,}conda:\s*$", text, re.M), f"{path.name} now declares conda:"
        assert not re.search(r"^\s{4,}container:\s*$", text, re.M), f"{path.name} now declares container:"
