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
    "KEGGREST": "bioconductor-keggrest",
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


# Every CLI command readiness probes, with the conda package that provides it and the profile
# that must contain it. The header comment of this file claims CLI coverage; without this table
# a probed command could be missing from every environment spec and only fail on a user's
# machine. 'full' entries must also be exactly what readiness treats as full-only.
PROBED_COMMAND_TO_CONDA = {
    "snakemake": ("snakemake-minimal", "core"),
    "aria2c": ("aria2", "core"),
    "fastqc": ("fastqc", "core"),
    "multiqc": ("multiqc", "core"),
    "fastp": ("fastp", "core"),
    "sortmerna": ("sortmerna", "core"),
    "ribodetector_cpu": ("ribodetector", "full"),
    "STAR": ("star", "core"),
    "hisat2": ("hisat2", "core"),
    "hisat2-build": ("hisat2", "core"),
    "salmon": ("salmon", "core"),
    "gffread": ("gffread", "core"),
    "perl": ("perl", "core"),
    "samtools": ("samtools", "core"),
    "featureCounts": ("subread", "core"),
    "trim_galore": ("trim-galore", "core"),
    "trimmomatic": ("trimmomatic", "core"),
    "fastq_screen": ("fastq-screen", "core"),
    "bowtie2": ("bowtie2", "core"),
    "read_distribution.py": ("rseqc", "core"),
    "geneBody_coverage.py": ("rseqc", "core"),
    "gtfToGenePred": ("ucsc-gtftogenepred", "core"),
    "genePredToBed": ("ucsc-genepredtobed", "core"),
    "Rscript": ("r-base", "full"),
}


def _assert_probed_commands_installed(probed: set[str], mapping: dict[str, tuple[str, str]],
                                      core: set[str], full: set[str], lock: set[str],
                                      full_only: set[str]) -> None:
    unmapped = sorted(probed - set(mapping))
    assert not unmapped, f"probed CLI commands with no conda package (gate fails closed): {unmapped}"
    for command, (package, profile) in mapping.items():
        name = _normalise_distribution(package)
        assert name in full, f"bulkseq_full.yaml does not install {package} for probed {command}"
        assert name in lock, f"bulkseq.lock.yaml does not install {package} for probed {command}"
        if profile == "core":
            assert name in core, f"bulkseq_core.yaml does not install {package} for probed {command}"
            assert command not in full_only, f"{command} is probed as full-only but ships in core"
        else:
            assert name not in core, f"{package} is in the core profile but mapped as full-only"
            assert command in full_only, (
                f"{command} needs the full profile, so readiness must not require it on a core "
                "environment")


def test_every_probed_cli_tool_is_installed_by_the_profile_that_must_provide_it() -> None:
    from app.core.readiness import BIOINFORMATICS_TOOLS, FULL_ONLY_TOOLS, WSL_TOOLS

    envs = REPO_ROOT / "workflow" / "envs"
    _assert_probed_commands_installed(
        set(BIOINFORMATICS_TOOLS) | set(WSL_TOOLS), PROBED_COMMAND_TO_CONDA,
        _conda_names(envs / "bulkseq_core.yaml"), _conda_names(envs / "bulkseq_full.yaml"),
        _conda_names(envs / "bulkseq.lock.yaml"), set(FULL_ONLY_TOOLS))


def test_cli_tool_parity_rejects_an_unmapped_probe_and_a_missing_package() -> None:
    envs = REPO_ROOT / "workflow" / "envs"
    core = _conda_names(envs / "bulkseq_core.yaml")
    full = _conda_names(envs / "bulkseq_full.yaml")
    lock = _conda_names(envs / "bulkseq.lock.yaml")
    full_only = {"ribodetector_cpu", "Rscript"}
    with pytest.raises(AssertionError, match="never_probed_tool"):
        _assert_probed_commands_installed({"never_probed_tool"}, PROBED_COMMAND_TO_CONDA,
                                          core, full, lock, full_only)
    mutated = dict(PROBED_COMMAND_TO_CONDA, imaginary=("no-such-conda-package", "core"))
    with pytest.raises(AssertionError, match="no-such-conda-package"):
        _assert_probed_commands_installed(set(mutated), mutated, core, full, lock, full_only)


# R scripts a run executes only on some configs (input route, DE engine, or an optional
# feature). A namespace that appears in none of the other scripts is not loaded by every run,
# so it belongs in validate_project.required_r_packages()'s per-config additions rather than in
# the unconditional blocking gate.
_CONFIG_GATED_R_SCRIPTS = {
    "ingest_geo.R", "ingest_deseq2_results.R", "salmon_tximport.R",
    "run_edger.R", "run_limma.R", "run_voom.R", "run_gsva.R", "run_custom_enrichment.R",
    "run_meta_analysis.R", "run_meta_enrichment.R", "run_meta_per_study.R",
    "run_meta_per_study_enrichment.R", "make_meta_figures.R",
    "make_meta_enrichment_figures.R", "make_meta_per_study_figures.R", "make_goi.R",
}


def _r_scripts_loading(namespace: str) -> set[str]:
    # An actual load — library/require/requireNamespace/loadNamespace or a ns:: call — not a
    # mention of the name in a comment.
    escaped = re.escape(namespace)
    loads = re.compile(rf"(?:library|require|requireNamespace|loadNamespace)\s*\(\s*"
                       rf"[\"']?{escaped}[\"']?\s*[),]|{escaped}::")
    return {path.name for path in (REPO_ROOT / "workflow" / "scripts").glob("*.R")
            if loads.search(path.read_text(encoding="utf-8"))}


# A config that selects every optional route, engine and feature, so required_r_packages()
# reports the complete set of conditionally loaded packages.
_MAXIMAL_CONFIG = {
    "input": {"type": "microarray"}, "microarray": {"source": "affy_cel"},
    "workflow": {"meta_analysis": True, "de_engine": "limma-voom", "gsva": True,
                 "aligner": "Salmon"},
    "enrichment": {"backend": "gprofiler"},
}


def _assert_blocking_r_gate(blocking: list[str], conditional: set[str]) -> None:
    assert len(blocking) == len(set(blocking)), "duplicate entry in _CORE_R_PACKAGES"
    unprobed = sorted(set(blocking) - set(R_ANALYSIS_PACKAGES))
    assert not unprobed, f"validate_project blocks on packages readiness never probes: {unprobed}"
    for namespace in sorted(set(HARD_R_NAMESPACE_TO_CONDA) - set(blocking) - conditional):
        mandatory_scripts = _r_scripts_loading(namespace) - _CONFIG_GATED_R_SCRIPTS
        assert not mandatory_scripts, (
            f"{namespace} is loaded by {sorted(mandatory_scripts)}, which every run executes, "
            "but validate_project._CORE_R_PACKAGES does not block on it and "
            "required_r_packages() does not add it for any config")


def _load_validate_project():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "validate_project", REPO_ROOT / "workflow" / "scripts" / "validate_project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _blocking_and_conditional() -> tuple[list[str], set[str]]:
    module = _load_validate_project()
    blocking = list(module._CORE_R_PACKAGES)
    return blocking, set(module.required_r_packages(_MAXIMAL_CONFIG)) - set(blocking)


def test_blocking_r_gate_is_one_list_with_the_readiness_probe() -> None:
    # validate_project.py runs from the project's workflow copy and cannot import app, so the
    # two lists are separate text. This is what keeps them one list: the blocking gate must be
    # a subset of what readiness probes, and every namespace a mandatory script loads must be
    # either in it or in required_r_packages()'s per-config additions.
    blocking, conditional = _blocking_and_conditional()
    assert conditional, "required_r_packages added nothing for the maximal config"
    _assert_blocking_r_gate(blocking, conditional)


def test_blocking_r_gate_rejects_a_dropped_mandatory_package_negative_control() -> None:
    blocking, conditional = _blocking_and_conditional()
    for dropped in ("ggplot2", "SummarizedExperiment", "systemfonts"):
        with pytest.raises(AssertionError, match=dropped):
            _assert_blocking_r_gate([p for p in blocking if p != dropped], conditional)
    with pytest.raises(AssertionError, match="never probes"):
        _assert_blocking_r_gate(blocking + ["notAProbedPackage"], conditional)


def test_catalog_orgdbs_are_installed_and_probed_everywhere() -> None:
    catalog = (REPO_ROOT / "app" / "data" / "reference_catalog.yaml").read_text(encoding="utf-8")
    orgdbs = sorted(set(re.findall(r"org\.[A-Za-z]+\.[a-z]+\.db", catalog)))
    assert orgdbs, "no OrgDb declared in the reference catalog; the parser is out of date"
    full = _conda_names(REPO_ROOT / "workflow" / "envs" / "bulkseq_full.yaml")
    lock = _conda_names(REPO_ROOT / "workflow" / "envs" / "bulkseq.lock.yaml")
    setup = (REPO_ROOT / "scripts" / "setup_wsl_bioenv.sh").read_text(encoding="utf-8")
    for namespace in orgdbs:
        conda_package = _normalise_distribution("bioconductor-" + namespace.lower())
        assert conda_package in full, f"bulkseq_full.yaml is missing {namespace}"
        assert conda_package in lock, f"bulkseq.lock.yaml is missing {namespace}"
        assert namespace in R_ANALYSIS_PACKAGES, f"readiness R probe is missing {namespace}"
        assert f'"{namespace}"' in setup, f"setup R load probe is missing {namespace}"


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
