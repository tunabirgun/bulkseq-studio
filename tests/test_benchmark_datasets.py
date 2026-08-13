from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.core.benchmark_datasets import create_benchmark_project, load_benchmark_catalog
from app.core.metadata import load_metadata, validate_metadata
from app.core.project import ProjectManager


def test_pasilla_benchmark_project_creation() -> None:
    catalog = load_benchmark_catalog()
    assert catalog[0]["id"] == "pasilla_paired_subset"
    root = create_benchmark_project("pasilla_paired_subset", Path("manual_test_benchmark") / uuid4().hex, "pasilla_test")
    cfg = ProjectManager().load_config(root)
    samples = load_metadata(root / "config" / "samples.tsv")
    assert cfg.input.type == "sra"
    assert cfg.input.layout == "paired"
    assert cfg.reference.organism_name == "Drosophila melanogaster"
    assert cfg.deseq2.contrasts[0].name == "cg8144_rnai_vs_untreated"
    assert samples.shape[0] == 4
    messages = validate_metadata(samples, allow_pending_sra=True)
    assert not any(m["status"] == "FAIL" for m in messages)


def test_yeast_benchmark_project_creation() -> None:
    catalog = load_benchmark_catalog()
    ids = [b["id"] for b in catalog]
    assert ids[0] == "pasilla_paired_subset"  # pasilla stays first (picker + test order)
    assert "sc_ume6_paired" in ids
    root = create_benchmark_project("sc_ume6_paired", Path("manual_test_benchmark") / uuid4().hex, "yeast_test")
    cfg = ProjectManager().load_config(root)
    samples = load_metadata(root / "config" / "samples.tsv")
    assert cfg.input.type == "sra"
    assert cfg.input.layout == "paired"
    assert cfg.reference.organism_name == "Saccharomyces cerevisiae"
    # Enrichment ids must resolve from the catalog by exact organism_name match;
    # if they don't, enrichment silently no-ops (the v0.8.0 trap). This is the
    # discriminating assertion.
    assert cfg.enrichment.kegg_organism == "sce"
    assert cfg.enrichment.orgdb == "org.Sc.sgd.db"
    assert cfg.enrichment.taxon_id == 4932
    assert cfg.ppi.taxon == 4932
    # The contrast levels must be real condition values or DESeq2 fails at runtime.
    c0 = cfg.deseq2.contrasts[0]
    assert c0.name == "rpd3_ume6_delta_2_508_vs_rpd3_delta"
    assert c0.numerator == "rpd3_ume6_delta_2_508"
    assert c0.denominator == "rpd3_delta"
    assert {c0.numerator, c0.denominator} <= set(samples["condition"])
    assert cfg.deseq2.reference_level == {"condition": "rpd3_delta"}
    assert cfg.deseq2.design_formula == "~ condition"
    assert samples.shape[0] == 4
    messages = validate_metadata(samples, allow_pending_sra=True)
    assert not any(m["status"] == "FAIL" for m in messages)


def test_yeast_ume6_uses_paper_supported_rpd3_background_contrast() -> None:
    benchmark = next(
        item for item in load_benchmark_catalog() if item["id"] == "sc_ume6_paired")
    samples = {sample["sample_id"]: sample for sample in benchmark["samples"]}
    assert set(samples) == {
        "rpd3_delta_1", "rpd3_delta_2",
        "rpd3_ume6_delta_2_508_1", "rpd3_ume6_delta_2_508_2",
    }
    assert {sample["original_accession"] for sample in samples.values()} == {
        "SRR11684209", "SRR11684210", "SRR11684213", "SRR11684214",
    }
    assert {sample["condition"] for sample in samples.values()} == {
        "rpd3_delta", "rpd3_ume6_delta_2_508",
    }
    expected_runs = {
        "rpd3_delta_1": (
            "SRR11684209", "GSM4512958", "SRX8244998",
            "54f6ad43cdf8b17f3a8072d6346458b4",
            "6fe691238041a7cc65c89ff5727cfd7b", 623751693),
        "rpd3_delta_2": (
            "SRR11684210", "GSM4512959", "SRX8244999",
            "2f688755626ac8ade47adb1d6669c2aa",
            "4f8ca852b127de3748f48aae67ee5a15", 555483256),
        "rpd3_ume6_delta_2_508_1": (
            "SRR11684213", "GSM4512962", "SRX8245002",
            "2ee329c5f2141843df296e82b7c2da70",
            "6114a89f45109402035ba616b77d35fe", 515588089),
        "rpd3_ume6_delta_2_508_2": (
            "SRR11684214", "GSM4512963", "SRX8245003",
            "2032ced00dce6c760c1ce1b46cf7be1d",
            "5c520f18098fb29850b4b71547fd44ba", 534375089),
    }
    for sample_id, expected in expected_runs.items():
        sample = samples[sample_id]
        observed = (
            sample["original_accession"], sample["geo_accession"],
            sample["experiment_accession"], sample["fastq_1_md5"],
            sample["fastq_2_md5"], sample["download_bytes"],
        )
        assert observed == expected
    provenance = " ".join([benchmark["description"], *benchmark["notes"]])
    assert "10.7554/eLife.64061" in provenance
    assert "not a complete UME6 gene deletion" in provenance
    assert "two biological replicates for each genotype" in provenance

    for sample in samples.values():
        assert sample["fastq_1_url"].endswith("_1.fastq.gz")
        assert sample["fastq_2_url"].endswith("_2.fastq.gz")


def test_yeast_microarray_benchmark_uses_species_taxon_and_symbol_route(
        tmp_path: Path) -> None:
    root = create_benchmark_project(
        "yeast_cbc2_microarray", tmp_path, "yeast_microarray_test")
    cfg = ProjectManager().load_config(root)
    assert cfg.input.type == "microarray"
    assert cfg.reference.organism_name == "Saccharomyces cerevisiae"
    assert cfg.enrichment.orgdb == "org.Sc.sgd.db"
    assert cfg.enrichment.keytype == "SYMBOL"
    assert cfg.enrichment.kegg_organism == "sce"
    assert cfg.enrichment.taxon_id == 4932


def test_model_organism_microarray_provenance_is_scientifically_accurate() -> None:
    catalog = {item["id"]: item for item in load_benchmark_catalog()}
    for benchmark_id in ("arabidopsis_hub2_microarray", "yeast_cbc2_microarray"):
        benchmark = catalog[benchmark_id]
        provenance = " ".join([benchmark["description"], *benchmark["notes"]]).lower()
        assert "non-model" not in provenance
        assert "model" in provenance


def test_all_sequence_benchmarks_ship_verifiable_download_metadata(tmp_path: Path) -> None:
    sequence_benchmarks = [
        benchmark
        for benchmark in load_benchmark_catalog()
        if str(benchmark.get("type", "sra")).lower() != "microarray"
    ]
    assert sequence_benchmarks

    for benchmark in sequence_benchmarks:
        for sample in benchmark["samples"]:
            assert len(str(sample.get("fastq_1_md5", ""))) == 32
            assert all(char in "0123456789abcdef" for char in sample["fastq_1_md5"])
            if str(sample.get("layout", "paired")).lower() == "paired":
                assert len(str(sample.get("fastq_2_md5", ""))) == 32
                assert all(char in "0123456789abcdef" for char in sample["fastq_2_md5"])
            assert int(sample.get("download_bytes", 0)) > 0

        root = create_benchmark_project(
            str(benchmark["id"]), tmp_path, f"checksum-{benchmark['id']}")
        config = ProjectManager().load_config(root)
        reference = benchmark["reference"]
        assert config.reference.genome_md5 == reference["genome_md5"]
        assert config.reference.annotation_md5 == reference["annotation_md5"]
        samples = load_metadata(root / "config" / "samples.tsv")
        assert {"fastq_1_md5", "fastq_2_md5", "download_bytes"} <= set(samples.columns)
        assert samples["fastq_1_md5"].str.fullmatch(r"[0-9a-f]{32}").all()
        assert samples["fastq_2_md5"].str.fullmatch(r"[0-9a-f]{32}").all()
        assert (samples["download_bytes"].astype(int) > 0).all()


def test_rice_drr805007_download_bytes_matches_verified_mate_sizes() -> None:
    benchmark = next(
        item for item in load_benchmark_catalog()
        if item["id"] == "rice_cy1000_salt_paired"
    )
    sample = next(
        item for item in benchmark["samples"]
        if item["original_accession"] == "DRR805007"
    )
    verified_mate_sizes = 2_418_281_877 + 2_493_220_995
    assert sample["download_bytes"] == verified_mate_sizes
