from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from app.core.readiness import PYTHON_PACKAGES, R_ANALYSIS_PACKAGES


REPO_ROOT = Path(__file__).resolve().parents[1]

# Every non-stdlib import root found by the AST scan must have an explicit mapping. Unknown
# imports fail closed instead of being silently classified as transitive or optional.
IMPORT_TO_DISTRIBUTION = {
    "PySide6": "PySide6",
    "numpy": "numpy",
    "pandas": "pandas",
    "psutil": "psutil",
    "pydantic": "pydantic",
    "yaml": "PyYAML",
}

# make_timing_summary records host capacity when psutil is available and deliberately falls back
# to null metadata when it is not. This is the only explicitly non-mandatory workflow import; the
# GUI runtime still declares psutil normally.
OPTIONAL_WORKFLOW_DISTRIBUTIONS = {"psutil"}


def _normalise_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", spec.strip())
    assert match is not None, f"cannot parse dependency declaration: {spec!r}"
    return _normalise_distribution(match.group(0))


def _import_roots(paths: list[Path], *, injected_source: str | None = None) -> set[str]:
    roots: set[str] = set()
    sources = [(str(path), path.read_text(encoding="utf-8")) for path in paths]
    if injected_source is not None:
        sources.append(("<negative-mutation>", injected_source))
    for filename, source in sources:
        tree = ast.parse(source, filename=filename)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def _mapped_external_distributions(import_roots: set[str], *, local_roots: set[str]) -> set[str]:
    external = import_roots - set(sys.stdlib_module_names) - local_roots - {"__future__"}
    unknown = sorted(external - IMPORT_TO_DISTRIBUTION.keys())
    assert not unknown, f"unmapped external import roots (dependency gate fails closed): {unknown}"
    return {_normalise_distribution(IMPORT_TO_DISTRIBUTION[root]) for root in external}


def _project_metadata() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _runtime_requirement_names() -> set[str]:
    names: set[str] = set()
    for line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        declaration = line.split("#", 1)[0].strip()
        if declaration:
            names.add(_requirement_name(declaration))
    return names


def _conda_names(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for dep in data.get("dependencies", []):
        if isinstance(dep, str):
            names.add(_requirement_name(dep))
    return names


def _assert_declared(discovered: set[str], declared: set[str], site: str) -> None:
    missing = sorted(discovered - declared)
    assert not missing, f"{site} is missing direct runtime dependencies: {missing}"


def test_python_runtime_imports_are_declared_at_every_install_and_readiness_site() -> None:
    app_paths = sorted((REPO_ROOT / "app").rglob("*.py"))
    discovered = _mapped_external_distributions(_import_roots(app_paths), local_roots={"app"})

    metadata = _project_metadata()["project"]
    pyproject_runtime = {_requirement_name(dep) for dep in metadata["dependencies"]}
    readiness = {_normalise_distribution(package) for package in PYTHON_PACKAGES.values()}
    _assert_declared(discovered, pyproject_runtime, "pyproject.toml [project].dependencies")
    _assert_declared(discovered, _runtime_requirement_names(), "requirements.txt")
    _assert_declared(discovered, readiness, "app.core.readiness.PYTHON_PACKAGES")


def test_workflow_python_imports_are_declared_in_core_and_full_profiles() -> None:
    script_paths = sorted((REPO_ROOT / "workflow" / "scripts").glob("*.py"))
    local_roots = {path.stem for path in script_paths} | {"app"}
    discovered = _mapped_external_distributions(_import_roots(script_paths), local_roots=local_roots)
    assert OPTIONAL_WORKFLOW_DISTRIBUTIONS <= discovered
    required = discovered - OPTIONAL_WORKFLOW_DISTRIBUTIONS
    for profile in ("bulkseq_core.yaml", "bulkseq_full.yaml", "bulkseq.lock.yaml"):
        _assert_declared(required, _conda_names(REPO_ROOT / "workflow" / "envs" / profile), profile)


def test_pytest_is_test_only_not_a_shipped_runtime_requirement() -> None:
    metadata = _project_metadata()["project"]
    runtime = {_requirement_name(dep) for dep in metadata["dependencies"]}
    test_extra = {_requirement_name(dep) for dep in metadata["optional-dependencies"]["test"]}
    assert "pytest" not in runtime
    assert "pytest" not in _runtime_requirement_names()
    assert "pytest" in test_extra


def test_ast_dependency_gate_rejects_an_unknown_import_negative_control() -> None:
    # Inject the defect in memory: the exact same scanner used by the real gate must reject it.
    mutated = _import_roots([], injected_source="import undeclared_runtime_dependency\n")
    with pytest.raises(AssertionError, match="undeclared_runtime_dependency"):
        _mapped_external_distributions(mutated, local_roots=set())


# Hard namespaces loaded directly by mandatory or selectable R workflow scripts. Base/recommended
# namespaces are intentionally absent. The float spec uses package names without made-up pins; the
# linux-64 lock remains the authority for exact builds.
HARD_R_NAMESPACE_TO_CONDA = {
    "affy": "bioconductor-affy",
    "AnnotationDbi": "bioconductor-annotationdbi",
    "Biobase": "bioconductor-biobase",
    "clusterProfiler": "bioconductor-clusterprofiler",
    "DESeq2": "bioconductor-deseq2",
    "DOSE": "bioconductor-dose",
    "edgeR": "bioconductor-edger",
    "enrichplot": "bioconductor-enrichplot",
    "GEOquery": "bioconductor-geoquery",
    "ggnewscale": "r-ggnewscale",
    "ggplot2": "r-ggplot2",
    "ggrepel": "r-ggrepel",
    "ggridges": "r-ggridges",
    "gprofiler2": "r-gprofiler2",
    "GSVA": "bioconductor-gsva",
    "gtable": "r-gtable",
    "HTSFilter": "bioconductor-htsfilter",
    "igraph": "r-igraph",
    "jsonlite": "r-jsonlite",
    "limma": "bioconductor-limma",
    "matrixStats": "r-matrixstats",
    "metafor": "r-metafor",
    "metaRNASeq": "r-metarnaseq",
    "msigdbr": "r-msigdbr",
    "pheatmap": "r-pheatmap",
    "RColorBrewer": "r-rcolorbrewer",
    "S4Vectors": "bioconductor-s4vectors",
    "scales": "r-scales",
    "STRINGdb": "bioconductor-stringdb",
    "SummarizedExperiment": "bioconductor-summarizedexperiment",
    "svglite": "r-svglite",
    "systemfonts": "r-systemfonts",
    "tximport": "bioconductor-tximport",
}


def test_hard_direct_r_namespaces_are_in_fallback_lock_readiness_and_setup_probe() -> None:
    full = _conda_names(REPO_ROOT / "workflow" / "envs" / "bulkseq_full.yaml")
    lock = _conda_names(REPO_ROOT / "workflow" / "envs" / "bulkseq.lock.yaml")
    readiness = set(R_ANALYSIS_PACKAGES)
    setup = (REPO_ROOT / "scripts" / "setup_wsl_bioenv.sh").read_text(encoding="utf-8")
    for namespace, conda_package in HARD_R_NAMESPACE_TO_CONDA.items():
        normalised = _normalise_distribution(conda_package)
        assert normalised in full, f"bulkseq_full.yaml is missing direct R package {conda_package}"
        assert normalised in lock, f"bulkseq.lock.yaml is missing direct R package {conda_package}"
        assert namespace in readiness, f"readiness R probe is missing direct namespace {namespace}"
        assert f'"{namespace}"' in setup, f"setup R load probe is missing direct namespace {namespace}"
