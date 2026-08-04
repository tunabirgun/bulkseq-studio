from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MRS = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_run_summary.py"


@pytest.fixture(scope="module")
def mrs():
    spec = importlib.util.spec_from_file_location("make_run_summary", _MRS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- select_tools: trimmer / rRNA filter / contamination screen gating -----------------


def test_select_tools_defaults_to_fastp(mrs) -> None:
    tools = mrs.select_tools({"workflow": {}})
    assert tools["fastp"] == ["fastp", "--version"]
    assert "trim_galore" not in tools
    assert "trimmomatic" not in tools


def test_select_tools_probes_trim_galore_when_configured(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"trimmer": "trim-galore"}})
    assert tools["trim_galore"] == ["trim_galore", "--version"]
    assert "fastp" not in tools


def test_select_tools_probes_trimmomatic_when_configured(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"trimmer": "trimmomatic"}})
    assert tools["trimmomatic"][0] == "trimmomatic"
    assert "fastp" not in tools


def test_select_tools_omits_every_trimmer_when_trimming_is_off(mrs) -> None:
    # `trimming: false` runs no trimmer at all; probing one would record a version for a
    # tool that never executed.
    tools = mrs.select_tools({"workflow": {"trimming": False, "trimmer": "trim-galore"}})
    assert "fastp" not in tools
    assert "trim_galore" not in tools
    assert "trimmomatic" not in tools


def test_select_tools_omits_rrna_tool_when_filtering_off(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"rrna_filtering": False, "rrna_tool": "ribodetector"}})
    assert "ribodetector" not in tools
    assert "sortmerna" not in tools


def test_select_tools_probes_sortmerna_when_rrna_filtering_on(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"rrna_filtering": True}})
    assert tools["sortmerna"] == ["sortmerna", "--version"]


def test_select_tools_probes_ribodetector_when_configured(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"rrna_filtering": True, "rrna_tool": "ribodetector"}})
    assert tools["ribodetector"] == ["ribodetector_cpu", "--version"]
    assert "sortmerna" not in tools


def test_select_tools_omits_fastq_screen_by_default(mrs) -> None:
    assert "fastq_screen" not in mrs.select_tools({"workflow": {}})


def test_select_tools_probes_fastq_screen_when_contamination_screen_on(mrs) -> None:
    tools = mrs.select_tools({"workflow": {"contamination_screen": True},
                              "contamination": {"conf": "/path/to/fastq_screen.conf"}})
    assert tools["fastq_screen"] == ["fastq_screen", "--version"]


def test_select_tools_omits_fastq_screen_without_a_config_path(mrs) -> None:
    # The Snakefile also requires a FastQ Screen config; without one the rule is skipped
    # with a warning, so recording a version would claim a tool ran that did not.
    tools = mrs.select_tools({"workflow": {"contamination_screen": True}})
    assert "fastq_screen" not in tools


@pytest.mark.parametrize("input_type", ["count_matrix", "microarray", "deseq2_results"])
def test_select_tools_omits_read_processing_tools_for_non_fastq_input(mrs, input_type) -> None:
    # These modes never touch reads, so the Snakefile forces TRIMMING/RRNA_FILTER off no
    # matter what the switches say — and they keep their FASTQ-run defaults in a converted
    # project.
    tools = mrs.select_tools({
        "input": {"type": input_type},
        "workflow": {"trimming": True, "rrna_filtering": True, "contamination_screen": True},
        "contamination": {"conf": "/x.conf"},
    })
    for absent in ("fastp", "trim_galore", "trimmomatic", "sortmerna", "ribodetector", "fastq_screen"):
        assert absent not in tools, f"{absent} probed for a {input_type} run"


def test_de_engine_label_names_the_engine_that_ran(mrs) -> None:
    # Only DESeq2 shrinks; naming DESeq2/apeglm for a voom or edgeR run reports an engine
    # and a method that never executed.
    assert mrs.de_engine_label({"workflow": {"de_engine": "deseq2"}}, False, None, "apeglm") == \
        "DESeq2, shrinkage: apeglm"
    assert mrs.de_engine_label({"workflow": {"de_engine": "deseq2"}}, False, "ashr", "apeglm") == \
        "DESeq2, shrinkage: ashr"
    assert mrs.de_engine_label({"workflow": {"de_engine": "limma-voom"}}, False, None, "apeglm") == \
        "limma-voom (no LFC shrinkage)"
    assert mrs.de_engine_label({"workflow": {"de_engine": "edger"}}, False, None, "apeglm") == \
        "edgeR (no LFC shrinkage)"
    assert mrs.de_engine_label({"workflow": {}}, True, None, "apeglm") == "limma (no LFC shrinkage)"


def test_select_tools_always_includes_base_tools(mrs) -> None:
    tools = mrs.select_tools({"workflow": {}})
    for name in ("snakemake", "python", "fastqc", "multiqc", "STAR", "Rscript"):
        assert name in tools


# ---- sessionInfo.txt parsing: realised shrinkage method + BLAS/LAPACK platform line -----


def test_parse_session_info_extracts_shrinkage_line(mrs) -> None:
    text = (
        "Shrinkage method used: ashr (requested 'apeglm' failed and fell back: some reason)\n"
        "\n"
        "R version 4.5.2 (2025-10-31)\n"
        "Platform: x86_64-w64-mingw32/x64 (64-bit)\n"
    )
    info = mrs.parse_session_info(text)
    assert info["shrinkage_used"] == "ashr (requested 'apeglm' failed and fell back: some reason)"
    assert info["r_platform"] == "x86_64-w64-mingw32/x64 (64-bit)"


def test_parse_session_info_extracts_blas_lapack(mrs) -> None:
    text = (
        "Shrinkage method used: apeglm (as requested)\n\n"
        "Matrix products: default\n"
        "BLAS:   C:/conda/envs/bulkseq/Lib/R/lib/libRblas.dll\n"
        "LAPACK: C:/conda/envs/bulkseq/Lib/R/lib/libRlapack.dll\n"
    )
    info = mrs.parse_session_info(text)
    assert "libRblas.dll" in info["blas"]
    assert "libRlapack.dll" in info["lapack"]


def test_parse_session_info_empty_text_returns_empty_dict(mrs) -> None:
    assert mrs.parse_session_info("") == {}


def test_platform_provenance_reports_os_arch_and_r_line(mrs) -> None:
    session_info = {"r_platform": "x86_64-w64-mingw32/x64 (64-bit)",
                     "blas": "libRblas.dll", "lapack": "libRlapack.dll"}
    plat = mrs.platform_provenance(session_info)
    assert plat["os"]
    assert plat["arch"]
    assert plat["r_platform"] == "x86_64-w64-mingw32/x64 (64-bit)"
    assert plat["r_blas"] == "libRblas.dll"
    assert plat["r_lapack"] == "libRlapack.dll"


def test_platform_provenance_r_fields_none_when_no_session_info(mrs) -> None:
    plat = mrs.platform_provenance({})
    assert plat["r_platform"] is None
    assert plat["r_blas"] is None


# ---- render_text: realised shrinkage method overrides the configured one ----------------


def _base_payload(**overrides) -> dict:
    payload = {
        "project": {"name": "proj", "working_directory": "."},
        "run_date": "2026-01-01T00:00:00",
        "app_version": "1.0", "workflow_version": "1.0",
        "workflow_git_commit": None, "environment_lock_md5": None, "snakemake_version": "8.0",
        "input": {"type": "fastq"},
        "reference": {"organism_name": "human", "strain": None, "source": "ensembl", "release": "110",
                      "genome_md5": "x", "annotation_md5": "y"},
        "deseq2": {"shrinkage_method": "apeglm", "design_formula": "~condition",
                   "reference_level": "ctrl", "contrasts": [], "alpha": 0.05, "lfc_threshold": 1},
        "workflow": {},
        "customized_parameters": {},
        "warnings": [],
        "download_integrity": {},
        "output_paths": [],
        "platform": {"os": "Windows", "os_release": "11", "arch": "AMD64",
                     "r_platform": None, "r_blas": None, "r_lapack": None},
        "software_versions": {},
        "session_info": {},
    }
    payload.update(overrides)
    return payload


def test_render_text_reports_configured_method_when_no_fallback_recorded(mrs) -> None:
    text = mrs.render_text(_base_payload())
    assert "Method: DESeq2, shrinkage: apeglm" in text


def test_render_text_reports_realised_fallback_method(mrs) -> None:
    payload = _base_payload(session_info={
        "shrinkage_used": "ashr (requested 'apeglm' failed and fell back: coefficient not found)"
    })
    text = mrs.render_text(payload)
    assert "shrinkage: ashr (requested 'apeglm' failed and fell back" in text
    assert "shrinkage: apeglm" not in text


def test_render_text_includes_platform_section(mrs) -> None:
    text = mrs.render_text(_base_payload())
    assert "Platform" in text
    assert "OS: Windows 11" in text
    assert "Arch: AMD64" in text
