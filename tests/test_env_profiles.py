from __future__ import annotations

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
