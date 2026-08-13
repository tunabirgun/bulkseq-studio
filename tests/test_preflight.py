from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

import app.core.preflight as preflight_module
from app.core.preflight import (
    PREFLIGHT_FINGERPRINT_KEY,
    PREFLIGHT_FINGERPRINT_VERSION,
    PreflightCancelled,
    compute_preflight_fingerprint,
    read_recorded_preflight_fingerprint,
    validate_current_preflight,
    write_input_validation_with_fingerprint,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "inputs").mkdir()
    (root / "inputs" / "counts.tsv").write_text("gene\ts1\nA\t10\n", encoding="utf-8")
    (root / "references" / "genome.fa").write_text(">chr1\nACGT\n", encoding="utf-8")
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\nS1\tcontrol\n", encoding="utf-8",
    )
    (root / "references" / "project_reference.lock.yaml").write_text(
        "locked: true\nrelease: test-1\n", encoding="utf-8",
    )
    (root / "config" / "config.yaml").write_text(
        "input:\n  type: count_matrix\n  count_matrix: inputs/counts.tsv\n"
        "reference:\n  genome_fasta: references/genome.fa\n",
        encoding="utf-8",
    )
    return root


def _write_current(project: Path) -> None:
    write_input_validation_with_fingerprint(
        project,
        {"check": "01_input_validation", "status": "PASS", "messages": []},
    )


def test_current_preflight_validates_unchanged_project(project: Path) -> None:
    _write_current(project)
    recorded = read_recorded_preflight_fingerprint(project)
    outcome = validate_current_preflight(project)

    assert recorded is not None
    assert recorded["version"] == PREFLIGHT_FINGERPRINT_VERSION
    assert outcome.valid is True
    assert outcome.reason == "current"
    assert outcome.recorded == outcome.current


def test_fingerprint_is_deterministic_and_includes_reference_lock_and_local_files(project: Path) -> None:
    first = compute_preflight_fingerprint(project)
    second = compute_preflight_fingerprint(project)

    assert first == second
    assert first["version"] == PREFLIGHT_FINGERPRINT_VERSION
    assert "references/project_reference.lock.yaml" in first["state"]["reference_locks"]
    local = first["state"]["configured_files"]
    assert local["input.count_matrix"]["exists"] is True
    assert local["reference.genome_fasta"]["exists"] is True


def test_fastq_replacement_invalidates_preflight_without_sample_sheet_edit(tmp_path: Path) -> None:
    root = tmp_path / "fastq-project"
    (root / "config").mkdir(parents=True)
    (root / "reads").mkdir()
    fastq = root / "reads" / "S1_R1.fastq.gz"
    fastq.write_bytes(b"synthetic-fastq-v1")
    (root / "config" / "config.yaml").write_text(
        "input:\n  type: fastq\n", encoding="utf-8")
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\tlayout\tfastq_1\n"
        "S1\tcontrol\tsingle\treads/S1_R1.fastq.gz\n",
        encoding="utf-8",
    )
    _write_current(root)
    recorded = compute_preflight_fingerprint(root)
    sample_files = recorded["state"]["sample_input_files"]
    assert sample_files["row_000001.fastq_1"]["exists"] is True

    fastq.write_bytes(b"synthetic-fastq-v2-with-different-size")

    outcome = validate_current_preflight(root)
    assert outcome.valid is False
    assert outcome.reason == "changed"


def test_custom_samples_path_is_fingerprinted_and_drives_fastq_discovery(tmp_path: Path) -> None:
    root = tmp_path / "custom-samples-project"
    (root / "config").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "reads").mkdir()
    fastq = root / "reads" / "input.fastq.gz"
    fastq.write_bytes(b"synthetic-fastq")
    custom_samples = root / "metadata" / "study.tsv"
    custom_samples.write_text(
        "sample_id\tcondition\tlayout\tfastq_1\n"
        "sample-private-name\tcontrol\tsingle\treads/input.fastq.gz\n",
        encoding="utf-8",
    )
    # This decoy proves that the legacy fixed path is not what gets checked.
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\ndecoy\tcontrol\n", encoding="utf-8",
    )
    (root / "config" / "config.yaml").write_text(
        "input:\n  type: fastq\n  samples: metadata/study.tsv\n",
        encoding="utf-8",
    )

    _write_current(root)
    recorded = read_recorded_preflight_fingerprint(root)
    assert recorded is not None
    sheet = recorded["state"]["project_files"]["input.samples"]
    assert sheet["configured_path"] == "metadata/study.tsv"
    assert sheet["sha256"]
    sample_files = recorded["state"]["sample_input_files"]
    assert list(sample_files) == ["row_000001.fastq_1"]
    assert sample_files["row_000001.fastq_1"]["sha256"] == preflight_module._stream_sha256(fastq)
    assert all("sample-private-name" not in key for key in sample_files)

    custom_samples.write_text(
        "sample_id\tcondition\tlayout\tfastq_1\n"
        "sample-private-name\ttreated\tsingle\treads/input.fastq.gz\n",
        encoding="utf-8",
    )
    assert validate_current_preflight(root).reason == "changed"


def test_absolute_samples_path_is_resolved(tmp_path: Path) -> None:
    root = tmp_path / "absolute-samples-project"
    root.mkdir()
    external = tmp_path / "external.tsv"
    external.write_text("sample_id\tcondition\nS1\tcontrol\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "config.yaml").write_text(
        f"input:\n  samples: {json.dumps(str(external))}\n", encoding="utf-8",
    )

    fingerprint = compute_preflight_fingerprint(root)

    sheet = fingerprint["state"]["project_files"]["input.samples"]
    assert sheet["local"] is True
    assert sheet["exists"] is True
    assert sheet["sha256"] == preflight_module._stream_sha256(external)


@pytest.mark.skipif(os.name != "nt", reason="/mnt/<drive> host conversion is Windows-specific")
def test_wsl_form_samples_path_is_resolved_on_windows(tmp_path: Path) -> None:
    root = tmp_path / "wsl-samples-project"
    root.mkdir()
    external = tmp_path / "wsl-external.tsv"
    external.write_text("sample_id\tcondition\nS1\tcontrol\n", encoding="utf-8")
    (root / "config").mkdir()
    drive = external.drive.rstrip(":").lower()
    wsl_path = f"/mnt/{drive}/" + "/".join(external.resolve().parts[1:])
    (root / "config" / "config.yaml").write_text(
        f"input:\n  samples: {wsl_path}\n", encoding="utf-8",
    )

    sheet = compute_preflight_fingerprint(root)["state"]["project_files"]["input.samples"]

    assert sheet["local"] is True
    assert sheet["exists"] is True
    assert sheet["sha256"] == preflight_module._stream_sha256(external)


def test_same_size_fastq_replacement_with_restored_mtime_invalidates_preflight(
    tmp_path: Path,
) -> None:
    root = tmp_path / "same-size-fastq-project"
    (root / "config").mkdir(parents=True)
    (root / "reads").mkdir()
    fastq = root / "reads" / "input.fastq.gz"
    fastq.write_bytes(b"AAAA-BBBB-CCCC")
    (root / "config" / "config.yaml").write_text(
        "input:\n  type: fastq\n  samples: config/samples.tsv\n", encoding="utf-8",
    )
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\tlayout\tfastq_1\n"
        "S1\tcontrol\tsingle\treads/input.fastq.gz\n",
        encoding="utf-8",
    )
    before = fastq.stat()
    _write_current(root)

    fastq.write_bytes(b"ZZZZ-YYYY-XXXX")
    os.utime(fastq, ns=(before.st_atime_ns, before.st_mtime_ns))

    outcome = validate_current_preflight(root)
    assert fastq.stat().st_size == before.st_size
    assert fastq.stat().st_mtime_ns == before.st_mtime_ns
    assert outcome.valid is False
    assert outcome.reason == "changed"


def test_directory_index_manifest_detects_nested_child_content_mutation(project: Path) -> None:
    index = project / "references" / "star_index"
    (index / "nested").mkdir(parents=True)
    child = index / "nested" / "Genome"
    child.write_bytes(b"index-content-v1")
    config = project / "config" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "  star_index: references/star_index\n",
        encoding="utf-8",
    )
    _write_current(project)
    recorded = read_recorded_preflight_fingerprint(project)
    assert recorded is not None
    index_state = recorded["state"]["configured_files"]["reference.star_index"]
    entries = {entry["relative_path"]: entry for entry in index_state["manifest"]}
    assert entries["nested"]["kind"] == "directory"
    assert entries["nested/Genome"]["sha256"] == preflight_module._stream_sha256(child)

    child.write_bytes(b"index-content-v2")

    assert validate_current_preflight(project).reason == "changed"


def test_hisat2_prefix_expands_and_detects_shard_mutation_when_prefix_is_missing(
    project: Path,
) -> None:
    index = project / "references" / "hisat2"
    index.mkdir()
    prefix = index / "genome"
    shard_1 = index / "genome.1.ht2"
    shard_2 = index / "genome.2.ht2l"
    shard_1.write_bytes(b"hisat-shard-one")
    shard_2.write_bytes(b"hisat-shard-two")
    assert prefix.exists() is False
    config = project / "config" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "  hisat2_index: references/hisat2/genome\n",
        encoding="utf-8",
    )
    _write_current(project)
    recorded = read_recorded_preflight_fingerprint(project)
    assert recorded is not None
    prefix_state = recorded["state"]["configured_files"]["reference.hisat2_index"]
    assert prefix_state["kind"] == "hisat2_prefix"
    assert [item["relative_path"] for item in prefix_state["manifest"]] == [
        "genome.1.ht2", "genome.2.ht2l",
    ]

    shard_2.write_bytes(b"changed-shard-two")

    assert validate_current_preflight(project).reason == "changed"


@pytest.mark.skipif(os.name != "nt", reason="digest reuse requires an NTFS USN")
def test_unchanged_gate_reuses_recorded_content_digests(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_current(project)
    hash_calls: list[Path] = []

    def fail_if_rehashed(path: Path) -> str:
        hash_calls.append(path)
        raise AssertionError(f"unchanged file was rehashed: {path}")

    monkeypatch.setattr(preflight_module, "_stream_sha256", fail_if_rehashed)

    outcome = validate_current_preflight(project)

    assert outcome.valid is True
    assert hash_calls == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX inputs deliberately rehash")
def test_posix_gate_rehashes_when_no_strong_change_marker(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_current(project)
    original = preflight_module._stream_sha256
    hash_calls: list[Path] = []

    def record_rehash(path: Path) -> str:
        hash_calls.append(path)
        return original(path)

    monkeypatch.setattr(preflight_module, "_stream_sha256", record_rehash)

    outcome = validate_current_preflight(project)

    assert outcome.valid is True
    assert hash_calls


@pytest.mark.parametrize(
    ("relative_path", "replacement"),
    [
        ("config/config.yaml", "input:\n  type: count_matrix\n  count_matrix: inputs/counts.tsv\nworkflow:\n  enrichment: false\n"),
        ("config/samples.tsv", "sample_id\tcondition\nS1\tcontrol\nS2\ttreated\n"),
        ("references/project_reference.lock.yaml", "locked: true\nrelease: test-2\n"),
        ("inputs/counts.tsv", "gene\ts1\nA\t100\nB\t2\n"),
        ("references/genome.fa", ">chr1\nACGTACGTACGT\n"),
    ],
    ids=["config", "samples", "reference-lock", "local-input", "local-reference"],
)
def test_mutated_preflight_dependency_invalidates_saved_check(
    project: Path, relative_path: str, replacement: str,
) -> None:
    _write_current(project)
    (project / relative_path).write_text(replacement, encoding="utf-8")

    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert outcome.reason == "changed"
    assert outcome.recorded != outcome.current


def test_missing_check_is_not_treated_as_valid_preflight(project: Path) -> None:
    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert outcome.reason == "missing_or_legacy"
    assert outcome.recorded is None


def test_legacy_check_is_not_treated_as_valid_preflight(project: Path) -> None:
    path = project / "checks" / "01_input_validation.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"check": "01_input_validation", "status": "PASS", "messages": []}), encoding="utf-8")

    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert outcome.reason == "missing_or_legacy"
    assert read_recorded_preflight_fingerprint(project) is None


def test_added_fingerprint_field_preserves_legacy_payload_fields(project: Path) -> None:
    _write_current(project)
    payload = json.loads((project / "checks" / "01_input_validation.json").read_text(encoding="utf-8"))

    assert payload["check"] == "01_input_validation"
    assert payload["status"] == "PASS"
    assert payload["messages"] == []
    assert PREFLIGHT_FINGERPRINT_KEY in payload


def test_stably_missing_configured_input_never_validates(project: Path) -> None:
    (project / "inputs" / "counts.tsv").unlink()
    _write_current(project)

    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert outcome.reason.startswith("invalid_inputs:configured_files.input.count_matrix:missing")


def test_pending_remote_reference_is_validated_by_its_download_contract(project: Path) -> None:
    config = project / "config" / "config.yaml"
    config.write_text(
        "input:\n  type: count_matrix\n  count_matrix: inputs/counts.tsv\n"
        "reference:\n"
        "  genome_fasta: references/pending-genome.fa\n"
        "  annotation_file: references/pending-annotation.gtf\n"
        "  genome_fasta_url: https://example.org/releases/test/genome.fa.gz\n"
        "  annotation_gtf_url: https://example.org/releases/test/annotation.gtf.gz\n",
        encoding="utf-8",
    )
    _write_current(project)

    outcome = validate_current_preflight(project)
    recorded = read_recorded_preflight_fingerprint(project)

    assert outcome.valid is True
    assert recorded is not None
    reference = recorded["state"]["configured_files"]
    for field in ("reference.genome_fasta", "reference.annotation_file"):
        assert reference[field]["source_kind"] == "download"
        assert reference[field]["download_pending"] is True
        assert reference[field]["checksum_optional"] is True
        assert len(reference[field]["download_url_sha256"]) == 64

    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "test/annotation.gtf.gz", "test-2/annotation.gtf.gz"),
        encoding="utf-8",
    )
    assert validate_current_preflight(project).reason == "changed"


def test_missing_custom_reference_without_download_url_still_fails_closed(project: Path) -> None:
    config = project / "config" / "config.yaml"
    config.write_text(
        "input:\n  type: count_matrix\n  count_matrix: inputs/counts.tsv\n"
        "reference:\n  genome_fasta: references/missing-custom.fa\n",
        encoding="utf-8",
    )
    _write_current(project)

    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert outcome.reason.startswith(
        "invalid_inputs:configured_files.reference.genome_fasta:missing")


def test_stably_unreadable_configured_input_never_validates(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = preflight_module._stream_sha256

    def deny_counts(path: Path) -> str:
        if path.name == "counts.tsv":
            raise PermissionError("synthetic access denial")
        return original(path)

    monkeypatch.setattr(preflight_module, "_stream_sha256", deny_counts)
    _write_current(project)

    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert "input.count_matrix:unreadable" in outcome.reason


def test_directory_symlink_is_rejected_without_recursive_traversal(project: Path) -> None:
    index = project / "references" / "linked_index"
    target = project / "references" / "index_target"
    target.mkdir()
    (target / "Genome").write_bytes(b"synthetic-index")
    try:
        index.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    config = project / "config" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "  star_index: references/linked_index\n",
        encoding="utf-8",
    )
    _write_current(project)

    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert "unsafe_symlink_target" in outcome.reason


def test_file_url_is_not_misclassified_as_verified_remote_input(project: Path) -> None:
    config = project / "config" / "config.yaml"
    config.write_text(
        "input:\n  type: count_matrix\n  count_matrix: file:///tmp/counts.tsv\n",
        encoding="utf-8",
    )
    _write_current(project)

    outcome = validate_current_preflight(project)

    assert outcome.valid is False
    assert "unsupported_source" in outcome.reason


def test_pending_ena_download_is_bound_to_url_and_md5(tmp_path: Path) -> None:
    root = tmp_path / "sra-project"
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.yaml").write_text(
        "input:\n  type: sra\n  samples: config/samples.tsv\n", encoding="utf-8",
    )
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\tlayout\tfastq_1\tfastq_1_url\tfastq_1_md5\n"
        "S1\tcontrol\tsingle\tdata/raw/S1.fastq.gz\tftp.example/S1.fastq.gz\t"
        "0123456789abcdef0123456789abcdef\n",
        encoding="utf-8",
    )
    _write_current(root)

    outcome = validate_current_preflight(root)

    assert outcome.valid is True
    pending = read_recorded_preflight_fingerprint(root)["state"]["sample_input_files"]
    assert pending["row_000001.fastq_1"]["download_pending"] is True


def test_initial_content_hash_can_be_cancelled(project: Path) -> None:
    with pytest.raises(PreflightCancelled, match="cancelled"):
        compute_preflight_fingerprint(project, cancel_requested=lambda: True)


def test_gate_rehash_can_be_cancelled(project: Path) -> None:
    _write_current(project)
    (project / "inputs" / "counts.tsv").write_text(
        "gene\ts1\nA\t11\n", encoding="utf-8",
    )

    with pytest.raises(PreflightCancelled, match="cancelled"):
        validate_current_preflight(project, cancel_requested=lambda: True)


def test_sample_fastq_url_requires_explicit_download_contract(tmp_path: Path) -> None:
    root = tmp_path / "remote-fastq-project"
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.yaml").write_text(
        "input:\n  type: sra\n  samples: config/samples.tsv\n", encoding="utf-8",
    )
    (root / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\tlayout\tfastq_1\n"
        "S1\tcontrol\tsingle\thttps://example.invalid/S1.fastq.gz\n",
        encoding="utf-8",
    )
    _write_current(root)

    outcome = validate_current_preflight(root)

    assert outcome.valid is False
    assert "sample_input_files.row_000001.fastq_1:unsupported_source" in outcome.reason


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
def test_configured_directory_junction_is_rejected_without_crawling(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = project.parent / "external-index"
    target.mkdir()
    (target / "Genome").write_bytes(b"external-synthetic-index")
    junction = project / "references" / "junction-index"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"could not create a directory junction: {created.stderr or created.stdout}")
    try:
        assert junction.is_junction()
        config = project / "config" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + "  star_index: references/junction-index\n",
            encoding="utf-8",
        )
        _write_current(project)

        outcome = validate_current_preflight(project)

        assert outcome.valid is False
        assert "unsafe_symlink_target" in outcome.reason
        signature = read_recorded_preflight_fingerprint(project)["state"]["configured_files"]
        assert signature["reference.star_index"]["kind"] == "junction"
        assert "manifest" not in signature["reference.star_index"]
        # Python 3.11 has no Path.is_junction(). Its Windows reparse-tag fallback
        # must enforce the same boundary because pyproject still supports 3.11.
        monkeypatch.delattr(Path, "is_junction", raising=False)
        assert preflight_module._is_junction(junction) is True
    finally:
        if junction.exists():
            junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-specific")
def test_reference_lock_search_rejects_junction_escape(project: Path) -> None:
    target = project.parent / "external-locks"
    target.mkdir()
    (target / "external.lock.yaml").write_text("external: true\n", encoding="utf-8")
    junction = project / "references" / "escape"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"could not create a directory junction: {created.stderr or created.stdout}")
    try:
        _write_current(project)

        outcome = validate_current_preflight(project)
        recorded = read_recorded_preflight_fingerprint(project)

        assert outcome.valid is False
        assert "reference_locks.references/escape" in outcome.reason
        assert recorded is not None
        signature = recorded["state"]["reference_locks"]
        assert "references/escape/external.lock.yaml" not in signature
        assert signature["references/escape"]["kind"] == "junction"
    finally:
        if junction.exists() or junction.is_junction():
            junction.rmdir()


def test_hisat2_prefix_enforces_manifest_bound(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preflight_module, "_MAX_DIRECTORY_ENTRIES", 2)
    prefix = project / "references" / "genome"
    for index in range(1, 5):
        (project / "references" / f"genome.{index}.ht2").write_bytes(
            f"synthetic-shard-{index}".encode("ascii"),
        )
    config = project / "config" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "  hisat2_index: references/genome\n",
        encoding="utf-8",
    )

    fingerprint = compute_preflight_fingerprint(project)

    signature = fingerprint["state"]["configured_files"]["reference.hisat2_index"]
    assert signature["readable"] is False
    assert "exceeds 2" in signature["traversal_error"]
    assert prefix.name == "genome"


def test_hisat2_scan_bound_counts_matching_nonfiles(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preflight_module, "_MAX_DIRECTORY_ENTRIES", 2)
    prefix = project / "references" / "genome"
    for index in range(4):
        (project / "references" / f"genome.{index}.ht2").mkdir()

    signature = preflight_module._hisat2_prefix_signature(prefix, {})

    assert signature is not None
    assert signature["readable"] is False
    assert "scan exceeds 2 entries" in signature["traversal_error"]


def test_nested_directory_scan_uses_one_global_materialization_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "nested-index"
    current = root
    for depth in range(8):
        current.mkdir()
        (current / f"file-{depth}.bin").write_bytes(b"x")
        current = current / f"level-{depth}"
    current.mkdir()
    monkeypatch.setattr(preflight_module, "_MAX_DIRECTORY_ENTRIES", 4)
    real_iterdir = Path.iterdir
    yielded = {"count": 0}

    def counted_iterdir(path: Path):
        for child in real_iterdir(path):
            yielded["count"] += 1
            yield child

    monkeypatch.setattr(Path, "iterdir", counted_iterdir)

    signature = preflight_module._directory_signature(root, {})

    assert signature["readable"] is False
    assert "exceeds 4 entries" in signature["traversal_error"]
    assert yielded["count"] == 5
