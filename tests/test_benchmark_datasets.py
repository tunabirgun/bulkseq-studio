from __future__ import annotations

import copy
import csv
import hashlib
import re
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

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
        paired = samples["layout"].str.lower() == "paired"
        assert samples.loc[paired, "fastq_2_md5"].str.fullmatch(r"[0-9a-f]{32}").all()
        # A single-end run has no second mate, so its md5 must be empty rather than invented.
        assert (samples.loc[~paired, "fastq_2_md5"].fillna("") == "").all()
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


FUSARIUM_BENCHMARKS = ("fg_spores_mycelium_paired", "fg_heat_shock_paired")


def _benchmark(benchmark_id: str) -> dict:
    return next(item for item in load_benchmark_catalog() if item["id"] == benchmark_id)


def _assert_declared_download_metadata(benchmark: dict) -> None:
    """Every declared ENA value a download needs must be present and well-formed.

    Shared by the catalogue assertion and by its negative control, so the control
    exercises the same code that guards the shipped entries.
    """
    for sample in benchmark["samples"]:
        accession = str(sample["original_accession"])
        assert str(sample["layout"]) == "paired"
        for mate in (1, 2):
            digest = str(sample[f"fastq_{mate}_md5"])
            assert re.fullmatch(r"[0-9a-f]{32}", digest), f"{accession} mate {mate}: {digest}"
            url = str(sample[f"fastq_{mate}_url"])
            assert url.endswith(f"/{accession}_{mate}.fastq.gz"), url
        assert int(sample["download_bytes"]) > 0
        read_count = int(sample["read_count"])
        base_count = int(sample["base_count"])
        assert read_count > 0 and base_count > 0
        # ENA counts spots, not mates: base_count must divide into two equal-length mates.
        assert base_count % (read_count * 2) == 0, accession


def test_fusarium_benchmarks_declare_complete_ena_download_metadata() -> None:
    catalog_ids = [item["id"] for item in load_benchmark_catalog()]
    assert catalog_ids[0] == "pasilla_paired_subset"
    for benchmark_id in FUSARIUM_BENCHMARKS:
        assert benchmark_id in catalog_ids
        benchmark = _benchmark(benchmark_id)
        assert len(benchmark["samples"]) == 6
        _assert_declared_download_metadata(benchmark)
        reference = benchmark["reference"]
        assert re.fullmatch(r"[0-9a-f]{32}", str(reference["genome_md5"]))
        assert re.fullmatch(r"[0-9a-f]{32}", str(reference["annotation_md5"]))
        # Mate length is uniform within a study, so it is derived here rather than declared.
        lengths = {
            int(sample["base_count"]) // (int(sample["read_count"]) * 2)
            for sample in benchmark["samples"]
        }
        assert len(lengths) == 1
        assert str(lengths.pop()) in benchmark["description"]


def test_corrupt_declared_md5_is_rejected_by_the_catalogue_check() -> None:
    benchmark = copy.deepcopy(_benchmark("fg_spores_mycelium_paired"))
    benchmark["samples"][0]["fastq_1_md5"] = "not-a-checksum"

    with pytest.raises(AssertionError):
        _assert_declared_download_metadata(benchmark)


def test_fusarium_benchmarks_scaffold_onto_the_ph1_enrichment_route(tmp_path: Path) -> None:
    expected_conditions = {
        "fg_spores_mycelium_paired": ("spores", "mycelium"),
        "fg_heat_shock_paired": ("temp_37", "temp_25"),
    }
    for benchmark_id, (numerator, denominator) in expected_conditions.items():
        root = create_benchmark_project(benchmark_id, tmp_path, f"scaffold-{benchmark_id}")
        cfg = ProjectManager().load_config(root)
        samples = load_metadata(root / "config" / "samples.tsv")

        assert cfg.input.type == "sra"
        assert cfg.input.layout == "paired"
        assert cfg.reference.organism_name == "Fusarium graminearum PH-1"
        # No Bioconductor OrgDb exists for this organism: enrichment must resolve to the
        # KEGG/STRING route, and a stray OrgDb would send GO down a dead end.
        assert cfg.enrichment.orgdb is None
        assert cfg.enrichment.kegg_organism == "fgr"
        assert cfg.enrichment.kegg_key_form == "locus_tag"
        assert cfg.enrichment.gprofiler_organism == "fgraminearum"
        assert cfg.ppi.taxon == 229533
        assert cfg.reference.genome_size_category == "fungal"
        assert cfg.reference.annotation_format == "gtf"

        c0 = cfg.deseq2.contrasts[0]
        assert (c0.numerator, c0.denominator) == (numerator, denominator)
        assert cfg.deseq2.reference_level == {"condition": denominator}
        assert cfg.deseq2.design_formula == "~ condition"

        assert samples.shape[0] == 6
        assert dict(samples["condition"].value_counts()) == {numerator: 3, denominator: 3}
        assert {c0.numerator, c0.denominator} == set(samples["condition"])
        assert (root / "config" / "sra_accessions.txt").read_text(
            encoding="utf-8").split() == list(samples["original_accession"])
        messages = validate_metadata(samples, allow_pending_sra=True)
        assert not any(m["status"] == "FAIL" for m in messages)


# What the 2026-09-13 reproduction measured: each study re-run from FASTQ under the 0.31.0
# workflow and again under a v0.29.1 copy, counts and DESeq2 table identical on both sides.
# Recorded measurements, so the values here are the specification, not something derivable.
FUSARIUM_REPRODUCTION = {
    "fg_spores_mycelium_paired": {"de_genes": 5734, "strandedness": 0},
    "fg_heat_shock_paired": {"de_genes": 5836, "strandedness": 2},
}
REPRODUCTION_WORKFLOW_VERSION = "0.31.0"
REPRODUCTION_DATE = "2026-09-13"


def _assert_reports_the_reproduction(expected: dict, measured: dict, where: str) -> None:
    assert expected["de_genes"] == measured["de_genes"], where
    assert expected["strandedness"] == measured["strandedness"], where
    # The reproduction settled the threshold for both entries; neither may go back to omitting it.
    assert "padj < 0.05" in str(expected["de_criterion"]), where
    status = str(expected["status"])
    assert REPRODUCTION_WORKFLOW_VERSION in status, f"{where}: {status}"
    assert REPRODUCTION_DATE in status, f"{where}: {status}"
    assert "0.2x-era" in status, f"{where}: {status}"  # the earlier provenance must survive
    for field in ("status", "de_criterion", "strandedness_basis"):
        text = str(expected[field])
        for disclaimer in ("not reproduced", "not recorded"):
            assert disclaimer not in text, f"{where}.{field}: {text}"


def test_fusarium_benchmarks_report_the_0_31_0_reproduction() -> None:
    """Catalogue and committed manifest must both state the reproduction and agree on it."""
    assert set(FUSARIUM_REPRODUCTION) == set(FUSARIUM_BENCHMARKS)
    examples = Path(__file__).resolve().parents[1] / "examples" / "benchmarks"
    for benchmark_id, measured in FUSARIUM_REPRODUCTION.items():
        catalogue = _benchmark(benchmark_id)["expected"]
        _assert_reports_the_reproduction(catalogue, measured, f"catalogue/{benchmark_id}")
        manifest = yaml.safe_load(
            (examples / benchmark_id / "benchmark_manifest.yaml").read_text(encoding="utf-8"))
        _assert_reports_the_reproduction(manifest["expected"], measured, f"manifest/{benchmark_id}")
        assert manifest["expected"] == catalogue, benchmark_id


def _stage_verified_fastq_cache(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Scaffold the guardrail benchmark and back its sample sheet with a synthetic cache.

    The declared ENA checksums address multi-gigabyte files, so the sheet's MD5 columns
    are re-declared against the synthetic payloads. Everything else — column names, row
    order, path layout — is what the scaffolder wrote.
    """
    root = create_benchmark_project("fg_spores_mycelium_paired", tmp_path / name, name)
    cache = tmp_path / f"{name}-cache"
    cache.mkdir()
    sheet = root / "config" / "samples.tsv"
    with sheet.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for row in rows:
        for mate in (1, 2):
            relative = str(row[f"fastq_{mate}"])
            payload = f"@{row['sample_id']}/{mate}\nACGT\n+\nIIII\n".encode("utf-8")
            (cache / Path(relative).name).write_bytes(payload)
            row[f"fastq_{mate}_md5"] = hashlib.md5(payload).hexdigest()
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return root, cache


def test_scaffolded_fusarium_sheet_seeds_from_a_checksum_verified_cache(tmp_path: Path) -> None:
    from tests.gui_benchmark import run_gui_full as harness

    root, cache = _stage_verified_fastq_cache(tmp_path, "verified")

    evidence = harness.seed_fastq_inputs(root, cache)

    assert len(evidence["files"]) == 12
    for entry in evidence["files"]:
        assert (root / str(entry["target"])).is_file()


def test_corrupt_fastq_md5_stops_the_download_integrity_check(tmp_path: Path) -> None:
    from tests.gui_benchmark import run_gui_full as harness

    root, cache = _stage_verified_fastq_cache(tmp_path, "corrupt")
    sheet = root / "config" / "samples.tsv"
    lines = sheet.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    first = lines[1].split("\t")
    first[header.index("fastq_1_md5")] = "0" * 32
    lines[1] = "\t".join(first)
    sheet.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(harness.HarnessFailure, match="MD5 mismatch"):
        harness.seed_fastq_inputs(root, cache)

    assert not (root / first[header.index("fastq_1")]).exists()


def test_fusarium_example_scaffolds_match_what_the_scaffolder_writes(tmp_path: Path) -> None:
    """The committed examples/ copies must not drift from the catalogue entries."""
    examples = Path(__file__).resolve().parents[1] / "examples" / "benchmarks"
    for benchmark_id in FUSARIUM_BENCHMARKS:
        root = create_benchmark_project(benchmark_id, tmp_path / benchmark_id, benchmark_id)
        for name in ("samples.tsv", "sra_accessions.txt"):
            written = (root / "config" / name).read_text(encoding="utf-8").splitlines()
            committed = (examples / benchmark_id / name).read_text(encoding="utf-8").splitlines()
            assert written == committed, f"{benchmark_id}/{name}"
        manifest = yaml.safe_load(
            (examples / benchmark_id / "benchmark_manifest.yaml").read_text(encoding="utf-8"))
        benchmark = _benchmark(benchmark_id)
        assert manifest["expected"] == benchmark["expected"]
        assert [run["run_accession"] for run in manifest["selected_runs"]] == [
            sample["original_accession"] for sample in benchmark["samples"]]
        assert manifest["source_publication"] == benchmark["source_publication"]
        readme = (examples / benchmark_id / "README.md").read_text(encoding="utf-8")
        assert benchmark["geo_series"] in readme
        assert str(benchmark["expected"]["de_genes"]) in readme.replace(",", "")


# The two non-model benchmarks added in 0.33.0, with the values their primary records give.
NON_MODEL_BENCHMARKS = {
    "mo_mocrea_mycelium_paired": {
        "organism": "Magnaporthe oryzae", "layout": "paired", "mate_length": 150,
        "conditions": ("mocrea_deletion", "wild_type"), "kegg": "mgr", "string_taxon": 242507,
        "gprofiler": "moryzae", "category": "fungal", "geo": "GSE153084",
        "doi": "10.1016/j.fgb.2020.103496"},
    "sorghum_sulfur_single": {
        "organism": "Sorghum bicolor", "layout": "single", "mate_length": 86,
        "conditions": ("sulfur_deficient", "control"), "kegg": "sbi", "string_taxon": 4558,
        "gprofiler": "sbicolor", "category": "plant", "geo": "GSE184725",
        "doi": "10.1093/pcp/pcac023"},
}


@pytest.mark.parametrize("benchmark_id", sorted(NON_MODEL_BENCHMARKS))
def test_non_model_benchmarks_declare_ena_metadata_and_scaffold_onto_the_transfer_route(
        tmp_path: Path, benchmark_id: str) -> None:
    spec = NON_MODEL_BENCHMARKS[benchmark_id]
    benchmark = _benchmark(benchmark_id)
    assert spec["geo"] == benchmark["geo_series"] and spec["doi"] in benchmark["source_publication"]
    mates = 2 if spec["layout"] == "paired" else 1
    for sample in benchmark["samples"]:
        accession = str(sample["original_accession"])
        assert sample["layout"] == spec["layout"]
        for mate in range(1, mates + 1):
            assert re.fullmatch(r"[0-9a-f]{32}", str(sample[f"fastq_{mate}_md5"])), accession
            suffix = f"_{mate}.fastq.gz" if mates == 2 else ".fastq.gz"
            assert str(sample[f"fastq_{mate}_url"]).endswith(f"/{accession}{suffix}")
        assert "fastq_2_url" in sample if mates == 2 else "fastq_2_url" not in sample
        # ENA base_count spans every mate; the read length is derived, not declared.
        assert int(sample["base_count"]) == int(sample["read_count"]) * mates * spec["mate_length"], accession
    assert f"{spec['mate_length']} bp" in benchmark["description"]
    for key in ("genome_md5", "annotation_md5"):
        assert re.fullmatch(r"[0-9a-f]{32}", str(benchmark["reference"][key]))

    root = create_benchmark_project(benchmark_id, tmp_path, f"scaffold-{benchmark_id}")
    cfg = ProjectManager().load_config(root)
    samples = load_metadata(root / "config" / "samples.tsv")
    numerator, denominator = spec["conditions"]
    assert cfg.input.layout == spec["layout"]
    assert cfg.reference.organism_name == spec["organism"]
    assert cfg.reference.genome_size_category == spec["category"]
    # No OrgDb, a STRING taxon: enrichment.transfer "auto" runs the annotation-transfer route.
    assert cfg.enrichment.orgdb is None and cfg.enrichment.transfer == "auto"
    assert cfg.ppi.taxon == spec["string_taxon"]
    assert cfg.enrichment.kegg_organism == spec["kegg"]
    assert cfg.enrichment.gprofiler_organism == spec["gprofiler"]
    c0 = cfg.deseq2.contrasts[0]
    assert (c0.numerator, c0.denominator) == (numerator, denominator)
    assert dict(samples["condition"].value_counts()) == {numerator: 3, denominator: 3}
    messages = validate_metadata(samples, allow_pending_sra=True)
    assert not any(m["status"] in ("FAIL", "WARNING") for m in messages), messages
