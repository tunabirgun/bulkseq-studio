"""Versioned provenance for the inputs a pre-run validation actually checked.

A successful input-validation result is reusable only while its configuration,
sample sheet, reference locks and local scientific inputs remain unchanged.
Every local file is therefore content-addressed. Validation remains responsive
for large FASTQs and indexes by reusing a recorded digest only when the host
provides a strong per-file change marker. On platforms without one, the gate
rehashes instead of trusting timestamps that can be restored or coalesced.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import stat as stat_module
import threading
from typing import Any, Callable, Mapping

import yaml

from app.core.paths import project_configured_path


PREFLIGHT_FINGERPRINT_KEY = "preflight_fingerprint"
PREFLIGHT_FINGERPRINT_VERSION = 2
INPUT_VALIDATION_CHECK = "01_input_validation"

_ANY_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_REMOTE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_DIRECTORY_DEPTH = 64
_MAX_DIRECTORY_ENTRIES = 100_000
_HASH_CONTROL = threading.local()
_IDENTITY_KEYS = (
    "path",
    "kind",
    "size",
    "mtime_ns",
    "ctime_ns",
    "change_time_ns",
    "usn",
    "device",
    "inode",
)


@dataclass(frozen=True)
class PreflightFingerprintValidation:
    """Whether the saved preflight state still describes the current project."""

    valid: bool
    reason: str
    recorded: str | None
    current: str


class PreflightCancelled(RuntimeError):
    """Raised when a closing window cancels an in-progress content fingerprint."""


def compute_preflight_fingerprint(
    project_root: Path,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Return a deterministic, versioned fingerprint for current pre-run inputs.

    This public computation always reads and hashes the current content.  The
    gate-specific reuse path is private to :func:`validate_current_preflight`,
    where the previously recorded state is available for an identity check.
    """
    previous_cancel = getattr(_HASH_CONTROL, "cancel_requested", None)
    _HASH_CONTROL.cancel_requested = cancel_requested
    try:
        return _compute_preflight_fingerprint(project_root, reusable_state=None)
    finally:
        _HASH_CONTROL.cancel_requested = previous_cancel


def enrich_input_validation_payload(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Add the current fingerprint to an input-validation JSON payload.

    The fingerprint is an additive top-level field, so existing readers that
    consume only ``check``, ``status`` and ``messages`` remain compatible.
    """
    enriched = dict(payload)
    enriched[PREFLIGHT_FINGERPRINT_KEY] = compute_preflight_fingerprint(
        project_root,
        cancel_requested=cancel_requested,
    )
    return enriched


def write_input_validation_with_fingerprint(
    project_root: Path,
    payload: Mapping[str, Any],
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    """Write ``checks/01_input_validation.json`` with a current fingerprint."""
    root = project_root.expanduser().resolve()
    path = root / "checks" / f"{INPUT_VALIDATION_CHECK}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    enriched = enrich_input_validation_payload(
        root,
        payload,
        cancel_requested=cancel_requested,
    )
    path.write_text(json.dumps(enriched, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def read_recorded_preflight_fingerprint(project_root: Path) -> dict[str, Any] | None:
    """Return a saved fingerprint, or ``None`` for missing/legacy/unreadable checks."""
    path = project_root.expanduser().resolve() / "checks" / f"{INPUT_VALIDATION_CHECK}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    fingerprint = payload.get(PREFLIGHT_FINGERPRINT_KEY) if isinstance(payload, dict) else None
    return dict(fingerprint) if isinstance(fingerprint, dict) else None


def validate_current_preflight(
    project_root: Path,
    *,
    cancel_requested: Callable[[], bool] | None = None,
) -> PreflightFingerprintValidation:
    """Compare the saved validation state with current project inputs.

    Legacy checks intentionally do not validate: a successful status without a
    fingerprint cannot establish which project state was checked.  A supported
    fingerprint may supply cached content digests, but each digest is reused only
    after the current file's full strong identity matches the recorded identity.
    POSIX timestamps are not a strong change marker: two writes can share one
    timestamp tick, and callers can restore ``mtime``. Those hosts rehash.
    """
    root = project_root.expanduser().resolve()
    recorded = read_recorded_preflight_fingerprint(root)
    reusable_state: Mapping[str, Any] | None = None
    if (
        isinstance(recorded, Mapping)
        and recorded.get("version") == PREFLIGHT_FINGERPRINT_VERSION
        and recorded.get("algorithm") == "sha256"
        and isinstance(recorded.get("state"), Mapping)
    ):
        reusable_state = recorded["state"]
    previous_cancel = getattr(_HASH_CONTROL, "cancel_requested", None)
    _HASH_CONTROL.cancel_requested = cancel_requested
    try:
        current = _compute_preflight_fingerprint(root, reusable_state=reusable_state)
    finally:
        _HASH_CONTROL.cancel_requested = previous_cancel
    state_problem = _fingerprint_state_problem(current.get("state"))
    if state_problem is not None:
        return PreflightFingerprintValidation(
            False, f"invalid_inputs:{state_problem}", _fingerprint_value(recorded or {}), current["value"],
        )
    if recorded is None:
        return PreflightFingerprintValidation(False, "missing_or_legacy", None, current["value"])
    if recorded.get("version") != PREFLIGHT_FINGERPRINT_VERSION:
        return PreflightFingerprintValidation(
            False, "unsupported_version", _fingerprint_value(recorded), current["value"],
        )
    if recorded.get("algorithm") != "sha256" or not isinstance(recorded.get("value"), str):
        return PreflightFingerprintValidation(
            False, "malformed", _fingerprint_value(recorded), current["value"],
        )
    if recorded["value"] != current["value"]:
        return PreflightFingerprintValidation(False, "changed", recorded["value"], current["value"])
    return PreflightFingerprintValidation(True, "current", recorded["value"], current["value"])


def _compute_preflight_fingerprint(
    project_root: Path,
    reusable_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    config_path = root / "config" / "config.yaml"
    config_data = _load_yaml_mapping(config_path)
    previous = reusable_state if isinstance(reusable_state, Mapping) else {}
    previous_project_files = _mapping(previous.get("project_files"))

    samples_configured = _configured_samples_path(config_data)
    samples_path = _resolve_local_path(root, samples_configured)
    samples_signature = _configured_path_signature(
        root,
        samples_configured,
        previous_project_files.get("input.samples"),
    )
    state = {
        "project_files": {
            "config/config.yaml": _file_signature(
                config_path, previous_project_files.get("config/config.yaml"),
            ),
            "input.samples": samples_signature,
        },
        "reference_locks": _reference_lock_signatures(
            root, _mapping(previous.get("reference_locks")),
        ),
        "configured_files": _configured_file_signatures(
            root, config_data, _mapping(previous.get("configured_files")),
        ),
        "sample_input_files": _sample_input_file_signatures(
            root,
            samples_path,
            _mapping(previous.get("sample_input_files")),
        ),
    }
    canonical_state = _canonical_json(state)
    return {
        "version": PREFLIGHT_FINGERPRINT_VERSION,
        "algorithm": "sha256",
        "value": hashlib.sha256(canonical_state.encode("utf-8")).hexdigest(),
        "state": state,
    }


def _reference_lock_signatures(
    project_root: Path,
    previous: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    references = project_root / "references"
    if references.is_symlink() or _is_junction(references):
        return {
            "references": _symlink_signature(
                references, _mapping(previous.get("references")),
            ),
        }
    if not references.exists():
        return {}
    signatures: dict[str, dict[str, Any]] = {}
    visited: set[tuple[int, int]] = set()
    root_key = _filesystem_identity_key(_stat_identity(references))
    if root_key is not None:
        visited.add(root_key)
    try:
        _append_reference_lock_signatures(
            project_root,
            references,
            previous,
            signatures,
            visited=visited,
            depth=0,
            entry_count=[0],
        )
    except (OSError, ValueError) as exc:
        signatures["references"] = {
            "exists": True,
            "kind": "directory",
            "path": _normalized_path(references),
            "readable": False,
            "traversal_error": str(exc),
        }
    return signatures


def _append_reference_lock_signatures(
    project_root: Path,
    directory: Path,
    previous: Mapping[str, Any],
    signatures: dict[str, dict[str, Any]],
    *,
    visited: set[tuple[int, int]],
    depth: int,
    entry_count: list[int],
) -> None:
    """Find reference lock files without following links or unbounded trees."""
    _raise_if_cancelled()
    if depth >= _MAX_DIRECTORY_DEPTH:
        raise ValueError(f"reference traversal exceeds {_MAX_DIRECTORY_DEPTH} levels")
    for child in _bounded_sorted_children(directory, entry_count):
        _raise_if_cancelled()
        relative = child.relative_to(project_root).as_posix()
        if child.is_symlink() or _is_junction(child):
            # Even a non-lock directory link is unsafe here: following it while
            # searching for locks can escape the project or form a cycle.
            signatures[relative] = _symlink_signature(
                child, _mapping(previous.get(relative)),
            )
            continue
        if child.is_dir():
            identity_key = _filesystem_identity_key(_stat_identity(child))
            if identity_key is not None and identity_key in visited:
                signatures[relative] = {
                    "exists": True,
                    "kind": "directory",
                    "path": _normalized_path(child),
                    "readable": False,
                    "cycle": True,
                }
                continue
            if identity_key is not None:
                visited.add(identity_key)
            _append_reference_lock_signatures(
                project_root,
                child,
                previous,
                signatures,
                visited=visited,
                depth=depth + 1,
                entry_count=entry_count,
            )
            continue
        if (
            child.is_file()
            and "lock" in child.name.lower()
            and child.suffix.lower() in {".yaml", ".yml", ".json"}
        ):
            signatures[relative] = _file_signature(child, previous.get(relative))


def _configured_file_signatures(
    project_root: Path,
    config: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    values = _configured_local_paths(config)
    signatures: dict[str, dict[str, Any]] = {}
    reference = config.get("reference")
    reference_mapping = reference if isinstance(reference, Mapping) else {}
    reference_download_fields = {
        "reference.genome_fasta": ("genome_fasta_url", "genome_md5"),
        "reference.annotation_file": ("annotation_gtf_url", "annotation_md5"),
    }
    for field, configured_path in sorted(values.items()):
        signature = _configured_path_signature(
            project_root,
            configured_path,
            previous.get(field),
            hisat2_prefix=field == "reference.hisat2_index",
        )
        contract = reference_download_fields.get(field)
        if signature.get("exists") is False and contract is not None:
            url_field, checksum_field = contract
            remote_url = str(reference_mapping.get(url_field) or "").strip()
            expected_md5 = str(reference_mapping.get(checksum_field) or "").strip().lower()
            if _REMOTE_URL_RE.match(remote_url):
                signature.update({
                    "source_kind": "download",
                    "download_pending": True,
                    "download_url_sha256": hashlib.sha256(
                        remote_url.encode("utf-8"),
                    ).hexdigest(),
                    "expected_md5": expected_md5,
                    # Existing reference presets identify immutable release URLs
                    # but do not all publish MD5 values. This exception is limited
                    # to the two reference artifacts; sample downloads still need
                    # the explicit URL + MD5 contract below.
                    "checksum_optional": not expected_md5,
                })
        signatures[field] = signature
    return signatures


def _configured_path_signature(
    project_root: Path,
    configured_path: str,
    previous: Any,
    *,
    hisat2_prefix: bool = False,
) -> dict[str, Any]:
    resolved = _resolve_local_path(project_root, configured_path)
    signature: dict[str, Any] = {
        "configured_path": configured_path,
        "local": resolved is not None,
    }
    if resolved is None:
        if _REMOTE_URL_RE.match(configured_path):
            signature["source_kind"] = "url"
        else:
            signature["source_kind"] = "unsupported_url"
            signature["readable"] = False
        return signature
    previous_mapping = _mapping(previous)
    if hisat2_prefix:
        prefix_signature = _hisat2_prefix_signature(resolved, previous_mapping)
        if prefix_signature is not None:
            signature.update(prefix_signature)
            return signature
    signature.update(_path_content_signature(resolved, previous_mapping))
    return signature


def _sample_input_file_signatures(
    project_root: Path,
    samples_path: Path | None,
    previous: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Content-address FASTQs referenced by the configured sample sheet.

    Row ordinals deliberately keep sample identifiers out of fingerprint keys.
    Paths remain in values because they are required to identify the validated
    input and are already part of the user's project configuration.
    """
    if samples_path is None:
        return {}
    try:
        with samples_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle, delimiter="\t")
            signatures: dict[str, dict[str, Any]] = {}
            for row_index, row in enumerate(rows, start=1):
                for column in ("fastq_1", "fastq_2"):
                    raw = str(row.get(column, "") or "").strip()
                    if not raw:
                        continue
                    key = f"row_{row_index:06d}.{column}"
                    signature = _configured_path_signature(
                        project_root, raw, previous.get(key),
                    )
                    remote_url = str(row.get(f"{column}_url", "") or "").strip()
                    expected_md5 = str(row.get(f"{column}_md5", "") or "").strip().lower()
                    if _ANY_URL_RE.match(raw):
                        # FASTQ columns describe the local file consumed by the
                        # workflow. A pending download is accepted only through
                        # the explicit local-path + URL + checksum contract below.
                        signature["source_kind"] = "direct_sample_url"
                        signature["readable"] = False
                    elif remote_url:
                        signature["source_kind"] = "download"
                        signature["download_url_sha256"] = hashlib.sha256(
                            remote_url.encode("utf-8"),
                        ).hexdigest()
                        signature["expected_md5"] = expected_md5
                        if signature.get("exists") is False:
                            signature["download_pending"] = True
                    signatures[key] = signature
            return signatures
    except (OSError, UnicodeDecodeError, csv.Error):
        return {}


def _configured_samples_path(config: Mapping[str, Any]) -> str:
    input_config = config.get("input")
    if isinstance(input_config, Mapping):
        raw = input_config.get("samples")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "config/samples.tsv"


def _configured_local_paths(config: Mapping[str, Any]) -> dict[str, str]:
    """Extract paths that are scientific inputs/references rather than outputs."""
    input_section = config.get("input")
    input_type = str(input_section.get("type", "fastq")) if isinstance(input_section, Mapping) else "fastq"
    input_fields: tuple[str, ...] = ()
    if input_type == "count_matrix":
        input_fields = ("count_matrix",)
    elif input_type == "deseq2_results":
        input_fields = ("deseq2_results",)
    elif input_type == "sra":
        input_fields = ("sra_accessions",)
    fields = {
        "input": input_fields,
        "microarray": ("expression_matrix",),
        "reference": (
            "genome_fasta", "annotation_file", "transcriptome_fasta", "protein_fasta",
            "star_index", "hisat2_index", "salmon_index",
        ),
        "gene_sets": (
            "custom_gene_list", "custom_gene_sets", "functional_annotation_table",
            "background_gene_list",
        ),
        "contamination": ("conf",),
        "sortmerna": ("database",),
    }
    found: dict[str, str] = {}
    for section, names in fields.items():
        value = config.get(section)
        if not isinstance(value, Mapping):
            continue
        for name in names:
            raw = value.get(name)
            if isinstance(raw, str) and raw.strip():
                found[f"{section}.{name}"] = raw.strip()
    return found


def _resolve_local_path(project_root: Path, configured_path: str) -> Path | None:
    if _ANY_URL_RE.match(configured_path):
        return None
    return project_configured_path(project_root, configured_path)


def _path_content_signature(path: Path, previous: Mapping[str, Any]) -> dict[str, Any]:
    try:
        # Test the link itself before predicates that follow it. Otherwise a
        # configured symlink to a directory is traversed as an ordinary folder,
        # can escape the intended tree, and loses the link-target identity.
        if path.is_symlink() or _is_junction(path):
            return _symlink_signature(path, previous)
        if path.is_dir():
            return _directory_signature(path, previous)
        if path.is_file():
            return _file_signature(path, previous)
    except OSError:
        pass
    return _missing_or_other_signature(path)


def _file_signature(path: Path, previous: Any = None) -> dict[str, Any]:
    previous_mapping = _mapping(previous)
    for _attempt in range(2):
        before = _stat_identity(path)
        if not before.get("exists") or before.get("kind") != "file":
            return before
        if _digest_is_reusable(before, previous_mapping):
            before["sha256"] = previous_mapping["sha256"]
            return before
        try:
            digest = _stream_sha256(path)
        except OSError:
            before["readable"] = False
            return before
        after = _stat_identity(path)
        if _same_identity(before, after):
            after["sha256"] = digest
            return after
    after["readable"] = False
    after["unstable"] = True
    return after


def _directory_signature(path: Path, previous: Mapping[str, Any]) -> dict[str, Any]:
    root_identity = _stat_identity(path)
    previous_entries = _manifest_entries_by_name(previous.get("manifest"))
    entries: list[dict[str, Any]] = []
    try:
        root_key = _filesystem_identity_key(root_identity)
        visited = {root_key} if root_key is not None else set()
        _append_directory_entries(
            path,
            path,
            previous_entries,
            entries,
            visited=visited,
            depth=0,
            scan_count=[0],
        )
    except (OSError, ValueError) as exc:
        root_identity["readable"] = False
        root_identity["traversal_error"] = str(exc)
    entries.sort(key=lambda item: str(item.get("relative_path", "")))
    manifest_sha256 = hashlib.sha256(_canonical_json(entries).encode("utf-8")).hexdigest()
    root_identity.update({
        "manifest": entries,
        "manifest_sha256": manifest_sha256,
        "sha256": manifest_sha256,
    })
    return root_identity


def _append_directory_entries(
    root: Path,
    directory: Path,
    previous_entries: Mapping[str, Mapping[str, Any]],
    entries: list[dict[str, Any]],
    *,
    visited: set[tuple[int, int]],
    depth: int,
    scan_count: list[int],
) -> None:
    _raise_if_cancelled()
    if depth >= _MAX_DIRECTORY_DEPTH:
        raise ValueError(f"directory traversal exceeds {_MAX_DIRECTORY_DEPTH} levels")
    children = _bounded_sorted_children(directory, scan_count)
    for child in children:
        _raise_if_cancelled()
        if len(entries) >= _MAX_DIRECTORY_ENTRIES:
            raise ValueError(f"directory traversal exceeds {_MAX_DIRECTORY_ENTRIES} entries")
        relative = child.relative_to(root).as_posix()
        previous = previous_entries.get(relative, {})
        if child.is_symlink() or _is_junction(child):
            entry = _symlink_signature(child, previous)
        elif child.is_dir():
            entry = _stat_identity(child)
        elif child.is_file():
            entry = _file_signature(child, previous)
        else:
            entry = _missing_or_other_signature(child)
        entry["relative_path"] = relative
        entries.append(entry)
        if entry.get("kind") == "directory":
            identity_key = _filesystem_identity_key(entry)
            if identity_key is not None and identity_key in visited:
                entry["readable"] = False
                entry["cycle"] = True
                continue
            if identity_key is not None:
                visited.add(identity_key)
            _append_directory_entries(
                root,
                child,
                previous_entries,
                entries,
                visited=visited,
                depth=depth + 1,
                scan_count=scan_count,
            )


def _symlink_signature(path: Path, previous: Mapping[str, Any]) -> dict[str, Any]:
    identity = _stat_identity(path, follow_symlinks=False)
    if _is_junction(path):
        identity["kind"] = "junction"
    try:
        target = os.readlink(path)
    except OSError:
        identity["readable"] = False
        return identity
    identity["link_target_sha256"] = hashlib.sha256(os.fsencode(target)).hexdigest()
    identity["sha256"] = identity["link_target_sha256"]
    # A symlink target's content is scientifically relevant too. Retain the
    # link type/target while content-addressing the resolved file or directory.
    try:
        if path.is_file():
            identity["target_content"] = _file_signature(
                path, _mapping(previous.get("target_content")),
            )
        elif path.is_dir():
            # Directory symlinks can escape the selected tree or form branching
            # cycles. Reject them explicitly instead of recursively crawling an
            # unbounded target; users can configure the resolved directory itself.
            identity["target_content"] = _stat_identity(path)
            identity["target_content"]["readable"] = False
            identity["target_content"]["unsupported"] = "directory_symlink"
            identity["target_readable"] = False
        else:
            identity["target_readable"] = False
    except OSError:
        identity["target_readable"] = False
    return identity


def _filesystem_identity_key(signature: Mapping[str, Any]) -> tuple[int, int] | None:
    device = signature.get("device")
    inode = signature.get("inode")
    if isinstance(device, int) and isinstance(inode, int):
        return device, inode
    return None


def _is_junction(path: Path) -> bool:
    """Return whether ``path`` is a Windows directory junction, without following it."""
    checker = getattr(path, "is_junction", None)
    if checker is not None:
        try:
            return bool(checker())
        except OSError:
            return False
    if os.name != "nt":
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    # pathlib.Path.is_junction arrived in Python 3.12, while this project still
    # supports 3.11. The Windows mount-point reparse tag is the OS-defined marker
    # used by directory junctions (IO_REPARSE_TAG_MOUNT_POINT).
    return getattr(info, "st_reparse_tag", None) == 0xA0000003


def _bounded_sorted_children(directory: Path, scan_count: list[int]) -> list[Path]:
    """Charge every yielded child to one traversal-wide materialization budget."""
    children: list[Path] = []
    for child in directory.iterdir():
        _raise_if_cancelled()
        scan_count[0] += 1
        if scan_count[0] > _MAX_DIRECTORY_ENTRIES:
            raise ValueError(f"directory traversal exceeds {_MAX_DIRECTORY_ENTRIES} entries")
        children.append(child)
    children.sort(key=lambda item: item.name)
    return children


def _hisat2_prefix_signature(
    prefix: Path,
    previous: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a virtual manifest for a HISAT2 prefix, even if it is not a file."""
    parent = prefix.parent
    candidates: list[Path] = []
    seen: set[Path] = set()
    scanned = 0
    try:
        for pattern in (f"{prefix.name}.*.ht2", f"{prefix.name}.*.ht2l"):
            for path in parent.glob(pattern):
                _raise_if_cancelled()
                scanned += 1
                if scanned > _MAX_DIRECTORY_ENTRIES:
                    raise ValueError(
                        f"HISAT2 index scan exceeds {_MAX_DIRECTORY_ENTRIES} entries",
                    )
                if not path.is_file() or path in seen:
                    continue
                seen.add(path)
                candidates.append(path)
                if len(candidates) > _MAX_DIRECTORY_ENTRIES:
                    raise ValueError(
                        f"HISAT2 index exceeds {_MAX_DIRECTORY_ENTRIES} shards",
                    )
    except (OSError, ValueError) as exc:
        return {
            "exists": True,
            "kind": "hisat2_prefix",
            "path": _normalized_path(prefix),
            "readable": False,
            "traversal_error": str(exc),
        }
    if not candidates:
        return None
    previous_entries = _manifest_entries_by_name(previous.get("manifest"))
    entries: list[dict[str, Any]] = []
    for shard in sorted(candidates, key=lambda item: item.name):
        entry = _path_content_signature(shard, previous_entries.get(shard.name, {}))
        entry["relative_path"] = shard.name
        entries.append(entry)
    manifest_sha256 = hashlib.sha256(_canonical_json(entries).encode("utf-8")).hexdigest()
    return {
        "exists": True,
        "kind": "hisat2_prefix",
        "path": _normalized_path(prefix),
        "manifest": entries,
        "manifest_sha256": manifest_sha256,
        "sha256": manifest_sha256,
    }


def _missing_or_other_signature(path: Path) -> dict[str, Any]:
    signature = _stat_identity(path)
    if not signature.get("exists"):
        return signature
    if signature.get("kind") not in {"file", "directory", "symlink"}:
        signature["readable"] = False
    return signature


def _stat_identity(path: Path, *, follow_symlinks: bool = True) -> dict[str, Any]:
    try:
        file_stat = path.stat() if follow_symlinks else path.lstat()
    except OSError:
        return {"exists": False, "path": _normalized_path(path), "kind": "missing"}
    mode = file_stat.st_mode
    kind = (
        "file" if stat_module.S_ISREG(mode)
        else "directory" if stat_module.S_ISDIR(mode)
        else "symlink" if stat_module.S_ISLNK(mode)
        else "other"
    )
    identity: dict[str, Any] = {
        "exists": True,
        "path": _normalized_path(path),
        "kind": kind,
        "size": int(file_stat.st_size),
        "mtime_ns": int(file_stat.st_mtime_ns),
        "ctime_ns": int(file_stat.st_ctime_ns),
    }
    if int(file_stat.st_dev):
        identity["device"] = int(file_stat.st_dev)
    if int(file_stat.st_ino):
        identity["inode"] = int(file_stat.st_ino)
    identity.update(_windows_identity_extras(path))
    return identity


def _windows_identity_extras(path: Path) -> dict[str, int]:
    """Return Windows ChangeTime and the per-file update sequence number.

    Python 3.12 exposes file creation time as ``st_ctime`` on Windows.  The
    native ``FILE_BASIC_INFO.ChangeTime`` is required to detect an in-place,
    same-size rewrite whose last-write time was restored.  NTFS can coalesce
    very closely spaced timestamps, so its monotonically changing file USN is
    also included when the volume exposes one.  On a Windows filesystem that
    exposes neither strong change marker, digests are not reused.
    """
    if os.name != "nt":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_longlong),
                ("LastAccessTime", ctypes.c_longlong),
                ("LastWriteTime", ctypes.c_longlong),
                ("ChangeTime", ctypes.c_longlong),
                ("FileAttributes", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandleEx
        get_info.argtypes = (
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        )
        get_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        device_io_control = kernel32.DeviceIoControl
        device_io_control.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        device_io_control.restype = wintypes.BOOL

        handle = create_file(
            str(path),
            0x0080,  # FILE_READ_ATTRIBUTES
            0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS (also permits directories)
            None,
        )
        invalid_handle = wintypes.HANDLE(-1).value
        if handle == invalid_handle:
            return {}
        try:
            extras: dict[str, int] = {}
            info = FileBasicInfo()
            if get_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
                extras["change_time_ns"] = int(info.ChangeTime) * 100

            # FSCTL_READ_FILE_USN_DATA returns a USN_RECORD whose fixed header
            # begins with length/version, two file IDs and then the signed USN.
            output = ctypes.create_string_buffer(512)
            returned = wintypes.DWORD()
            if device_io_control(
                handle,
                0x000900EB,
                None,
                0,
                output,
                ctypes.sizeof(output),
                ctypes.byref(returned),
                None,
            ) and returned.value >= 32:
                import struct

                extras["usn"] = int(struct.unpack_from("<q", output.raw, 24)[0])
            return extras
        finally:
            close_handle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return {}


def _digest_is_reusable(current: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    digest = previous.get("sha256")
    # NTFS USNs are the only change markers used here that cannot be restored by
    # ordinary file writes. POSIX ctime/mtime can collide within one clock tick,
    # so treating them as cache keys can approve changed scientific inputs.
    if os.name != "nt" or "usn" not in current:
        return False
    return isinstance(digest, str) and len(digest) == 64 and _same_identity(current, previous)


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if not left.get("exists") or not right.get("exists"):
        return False
    for key in _IDENTITY_KEYS:
        if key in left or key in right:
            if key not in left or key not in right or left[key] != right[key]:
                return False
    required = {"path", "kind", "size", "mtime_ns", "ctime_ns"}
    return required.issubset(left) and required.issubset(right)


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            _raise_if_cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def _raise_if_cancelled() -> None:
    callback = getattr(_HASH_CONTROL, "cancel_requested", None)
    if callback is not None and callback():
        raise PreflightCancelled("Input fingerprinting was cancelled")


def _manifest_entries_by_name(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        return {}
    entries: dict[str, Mapping[str, Any]] = {}
    for item in value:
        if isinstance(item, Mapping) and isinstance(item.get("relative_path"), str):
            entries[item["relative_path"]] = item
    return entries


def _fingerprint_state_problem(value: Any) -> str | None:
    """Return a stable reason when a fingerprint describes unusable inputs.

    Equality alone is insufficient: a missing or unreadable file can remain
    identically missing between validation and launch. Every configured local
    dependency must therefore be both present and content-addressed.
    """
    if not isinstance(value, Mapping):
        return "malformed_state"
    for section in (
        "project_files", "reference_locks", "configured_files", "sample_input_files",
    ):
        signatures = value.get(section)
        if not isinstance(signatures, Mapping):
            return f"malformed_{section}"
        for name, signature in signatures.items():
            problem = _signature_problem(signature)
            if problem is not None:
                return f"{section}.{name}:{problem}"
    return None


def _signature_problem(value: Any, *, manifest_entry: bool = False) -> str | None:
    if not isinstance(value, Mapping):
        return "malformed_signature"
    source_kind = value.get("source_kind")
    if value.get("local") is False:
        return None if source_kind == "url" else "unsupported_source"
    if value.get("readable") is False:
        return str(value.get("unsupported") or value.get("traversal_error") or "unreadable")
    if value.get("unstable"):
        return "changed_while_reading"
    if value.get("exists") is False:
        if (
            source_kind == "download"
            and value.get("download_pending") is True
            and re.fullmatch(
                r"[0-9a-f]{64}", str(value.get("download_url_sha256", "")))
            and (
                re.fullmatch(r"[0-9a-f]{32}", str(value.get("expected_md5", "")))
                or value.get("checksum_optional") is True
            )
        ):
            return None
        return "missing"
    kind = value.get("kind")
    if kind == "file":
        digest = value.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            return "unhashed_file"
    elif kind == "directory":
        if not manifest_entry:
            digest = value.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                return "unhashed_directory"
    elif kind == "hisat2_prefix":
        digest = value.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            return "unhashed_index"
    elif kind in {"symlink", "junction"}:
        target = value.get("target_content")
        if value.get("target_readable") is False:
            return "unsafe_symlink_target"
        problem = _signature_problem(target)
        if problem is not None:
            return f"symlink_target_{problem}"
    elif kind not in {None}:
        return f"unsupported_{kind}"
    elif value.get("local") is not False:
        return "missing_kind"

    manifest = value.get("manifest")
    if manifest is not None:
        if not isinstance(manifest, list):
            return "malformed_manifest"
        for entry in manifest:
            problem = _signature_problem(entry, manifest_entry=True)
            if problem is not None:
                relative = entry.get("relative_path", "?") if isinstance(entry, Mapping) else "?"
                return f"manifest_{relative}_{problem}"
    return None


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, Mapping) else {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint_value(fingerprint: Mapping[str, Any]) -> str | None:
    value = fingerprint.get("value")
    return value if isinstance(value, str) else None
