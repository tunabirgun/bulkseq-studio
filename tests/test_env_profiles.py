from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

from app.core.readiness import BIOINFORMATICS_TOOLS, WSL_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
ENVS = REPO_ROOT / "workflow" / "envs"

# The alternative-aligner-route command-line tools. These were added to bulkseq_full.yaml
# in v0.11.0 but not to bulkseq_core.yaml, so a core-only env failed the Salmon/HISAT2
# routes with exit 127 (gffread/salmon not found). Keep every list below in sync.
ALIGNER_TOOLS = ("gffread", "salmon", "hisat2")


def _conda_deps(env_file: Path) -> list[str]:
    data = yaml.safe_load(env_file.read_text(encoding="utf-8"))
    return [dep for dep in data.get("dependencies", []) if isinstance(dep, str)]


def _has_tool(deps: list[str], tool: str) -> bool:
    # Deps look like "salmon=1.10.3"; match the package name before any version pin.
    return any(dep.split("=", 1)[0].strip() == tool for dep in deps)


def test_core_env_has_all_aligner_route_tools() -> None:
    deps = _conda_deps(ENVS / "bulkseq_core.yaml")
    for tool in ALIGNER_TOOLS:
        assert _has_tool(deps, tool), f"bulkseq_core.yaml is missing {tool}; the route that needs it would fail with exit 127"


def test_full_env_has_all_aligner_route_tools() -> None:
    deps = _conda_deps(ENVS / "bulkseq_full.yaml")
    for tool in ALIGNER_TOOLS:
        assert _has_tool(deps, tool), f"bulkseq_full.yaml is missing {tool}"


def test_lock_has_all_aligner_route_tools() -> None:
    deps = _conda_deps(ENVS / "bulkseq.lock.yaml")
    for tool in ALIGNER_TOOLS:
        assert _has_tool(deps, tool), f"bulkseq.lock.yaml is missing {tool}"


def test_readiness_probes_aligner_route_tools() -> None:
    # Probed and displayed so a stale env is visible; the run-time guards in the
    # Snakemake rules are the hard catch.
    for tool in ALIGNER_TOOLS:
        assert tool in WSL_TOOLS, f"readiness WSL_TOOLS does not probe {tool}"
        assert tool in BIOINFORMATICS_TOOLS, f"readiness BIOINFORMATICS_TOOLS does not probe {tool}"


# Full-env (R/Bioconductor) packages that the DESeq2 and enrichment routes need but that
# were missing from the env: r-ashr (lfcShrink ashr fallback in run_deseq2.R) and the four
# OrgDbs that enrichment.smk maps yeast/Arabidopsis/worm/zebrafish to. Keep in sync with
# bulkseq_full.yaml + bulkseq.lock.yaml.
FULL_ROUTE_PACKAGES = (
    "r-ashr",
    "bioconductor-org.sc.sgd.db",
    "bioconductor-org.at.tair.db",
    "bioconductor-org.ce.eg.db",
    "bioconductor-org.dr.eg.db",
)


def test_full_env_has_deseq2_and_enrichment_packages() -> None:
    deps = _conda_deps(ENVS / "bulkseq_full.yaml")
    for pkg in FULL_ROUTE_PACKAGES:
        assert _has_tool(deps, pkg), f"bulkseq_full.yaml is missing {pkg}"


def test_lock_has_deseq2_and_enrichment_packages() -> None:
    deps = _conda_deps(ENVS / "bulkseq.lock.yaml")
    for pkg in FULL_ROUTE_PACKAGES:
        assert _has_tool(deps, pkg), f"bulkseq.lock.yaml is missing {pkg}"


def test_sortmerna_in_core_full_and_lock() -> None:
    # rRNA filtering (workflow.rrna_filtering) needs sortmerna in the env it runs under;
    # it is a read-processing CLI tool so it belongs in the core profile, not full only.
    for env in ("bulkseq_core.yaml", "bulkseq_full.yaml", "bulkseq.lock.yaml"):
        assert _has_tool(_conda_deps(ENVS / env), "sortmerna"), f"{env} is missing sortmerna"


# --- 0.19.3: structural guards so the lock and the three R-guard lists cannot silently drift ---

def _pkg_name(dep: str) -> str:
    # "bioconductor-go.db=3.22.0=r45..." / "bioconductor-clusterprofiler>=4.14" -> name only.
    return re.split(r"[=<>!]", dep, 1)[0].strip()


def _conda_names(env_file: Path) -> set[str]:
    return {_pkg_name(d) for d in _conda_deps(env_file)}


def _pip_names(env_file: Path) -> set[str]:
    data = yaml.safe_load(env_file.read_text(encoding="utf-8"))
    for dep in data.get("dependencies", []):
        if isinstance(dep, dict) and "pip" in dep:
            return {_pkg_name(str(p)) for p in (dep.get("pip") or [])}
    return set()


def test_lock_is_superset_of_full_conda_deps() -> None:
    # A fresh install (0.19.2+) builds from the lock. If a package is added to bulkseq_full.yaml
    # but the lock is not regenerated, that package is silently absent on every fresh install —
    # the exact class of gap that dropped GO.db. The lock must cover every conda dep the float
    # spec declares.
    missing = sorted(_conda_names(ENVS / "bulkseq_full.yaml") - _conda_names(ENVS / "bulkseq.lock.yaml"))
    assert not missing, f"bulkseq.lock.yaml is missing conda packages present in bulkseq_full.yaml: {missing}"


def test_lock_has_no_conda_pip_double_pin() -> None:
    # A package pinned in BOTH the conda solve and the pip: subsection lets pip silently
    # overwrite the conda build at a different version (numpy/pandas did this), leaving the env
    # in a state the lock does not describe.
    overlap = sorted(_conda_names(ENVS / "bulkseq.lock.yaml") & _pip_names(ENVS / "bulkseq.lock.yaml"))
    assert not overlap, f"bulkseq.lock.yaml pins these in both conda and pip: {overlap}"


# The GO.db / clusterProfiler enrichment cluster whose disappearance from any one guard let a
# broken enrichment stack through. It must be checked by ALL THREE R guards.
_ENRICHMENT_CLUSTER = ("clusterProfiler", "GO.db", "DOSE", "enrichplot")


def _validate_project_core_packages() -> list[str]:
    spec = importlib.util.spec_from_file_location(
        "validate_project", REPO_ROOT / "workflow" / "scripts" / "validate_project.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod._CORE_R_PACKAGES)


def test_enrichment_cluster_in_every_r_guard() -> None:
    # The three R-stack guards legitimately differ in their non-enrichment members (the setup
    # Stage 2b probe is a small core, readiness probes the widest set, validate_project the
    # run-time subset), so this is a SUBSET check on the cluster that matters, not equality. A
    # cluster package present in one guard but not another recreates the pass-one/fail-another
    # gap that let a broken enrichment stack reach a run.
    from app.core.readiness import R_ANALYSIS_PACKAGES
    validate_pkgs = set(_validate_project_core_packages())
    readiness_pkgs = set(R_ANALYSIS_PACKAGES)
    setup_text = (REPO_ROOT / "scripts" / "setup_wsl_bioenv.sh").read_text(encoding="utf-8")
    for pkg in _ENRICHMENT_CLUSTER:
        assert pkg in validate_pkgs, f"validate_project._CORE_R_PACKAGES is missing {pkg}"
        assert pkg in readiness_pkgs, f"readiness.R_ANALYSIS_PACKAGES is missing {pkg}"
        assert f'"{pkg}"' in setup_text, f"setup_wsl_bioenv.sh Stage 2b probe is missing {pkg}"
