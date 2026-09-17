from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from app.constants import (APP_VERSION, PROJECT_DIRS, SAFE_ID_PATTERN, SCAFFOLD_METADATA_COLUMNS,
                           WORKFLOW_VERSION)
from app.core.config_models import AppConfig, default_config
from app.core.paths import (data_path, is_unsupported_unc_path, is_wsl_unc_path,
                            usable_disk_free_bytes, workflow_root)


_DECIMAL_COMMA_RE = re.compile(r"^-?\d+,\d+$")


class ProjectExistsError(ValueError):
    """Raised when scaffolding would write to an occupied destination.

    Subclasses ValueError deliberately: both GUI call sites already catch
    (OSError, ValueError), so an unhandled escape is impossible even if a
    caller is not updated to handle this type specifically.
    """

    def __init__(self, root: Path, *, is_project: bool = True,
                 is_file: bool = False) -> None:
        self.root = root
        self.is_project = is_project
        self.is_file = is_file
        if is_file:
            message = f"Project destination is an existing file: {root}"
        elif is_project:
            message = f"A BulkSeq Studio project already exists at {root}"
        else:
            message = f"Project destination already exists and is not empty: {root}"
        super().__init__(message)


class WorkflowSyncError(ValueError):
    """Raised when the copied workflow cannot be safely verified or promoted."""


def is_project_root(path: Path) -> bool:
    """True when *path* is an existing BulkSeq Studio project.

    Single definition of "is a project", shared by the create-time overwrite
    guard and the GUI's open-project validation, so the two cannot drift into
    disagreeing about what counts as a project.
    """
    return (path / "config" / "config.yaml").is_file()


def normalize_decimal_commas(data: Any) -> tuple[Any, list[str]]:
    """Recursively rewrite comma-decimal string values (e.g. "0,05") to dot floats.

    A hand-edited config on a comma-decimal locale can carry "alpha: 0,05", which YAML reads
    as the string "0,05" and pydantic then rejects. This keeps the decimal point a dot
    everywhere and returns the dotted paths that were fixed so the caller can warn.
    """
    fixed: list[str] = []

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, str) and _DECIMAL_COMMA_RE.match(node.strip()):
            fixed.append(path or "value")
            return node.strip().replace(",", ".")
        return node

    return walk(data, ""), fixed


def decimal_comma_warnings(project_root: Path) -> list[str]:
    """Human-readable warnings for any comma-decimal numbers in the project's config."""
    cfg = project_root / "config" / "config.yaml"
    try:
        _, fixed = normalize_decimal_commas(yaml.safe_load(cfg.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError):
        return []
    if not fixed:
        return []
    return [
        "Some numeric settings used a comma as the decimal separator (e.g. 0,05): "
        + ", ".join(fixed[:8])
        + (", …" if len(fixed) > 8 else "")
        + ". BulkSeq Studio uses a dot everywhere (0.05), so they were read as dots — "
        "save the project to normalize the file."
    ]


def validate_working_directory(path: Path, min_free_gb: float = 5.0, use_wsl: bool = False) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    path = path.expanduser()
    # A non-WSL network share is writable from Windows but has no path the pipeline can use:
    # windows_to_wsl_path raises on it, and a native Linux run cannot see it either. Report it
    # here, where the user chose it, instead of at launch.
    if is_unsupported_unc_path(path):
        return [{"status": "FAIL", "message": (
            f"'{path}' is a network share (UNC). The pipeline cannot reach it. Choose a folder "
            "on a local drive, or a WSL folder (\\\\wsl.localhost\\<distro>\\...).")}]
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".bulkseq_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        messages.append({"status": "FAIL", "message": f"Directory is not writable: {exc}"})
        return messages

    # usable_disk_free_bytes corrects the WSL vhdx over-report (a near-empty vhdx claims
    # its ~1 TB virtual size as free, hiding a nearly full physical drive).
    free_gb = usable_disk_free_bytes(path) / (1024**3)
    if free_gb < min_free_gb:
        messages.append({"status": "WARNING", "message": f"Low free disk space: {free_gb:.1f} GB"})

    lowered = str(path).lower()
    for marker in ("onedrive", "dropbox", "icloud"):
        if marker in lowered:
            messages.append({"status": "REVIEW_REQUIRED", "message": f"Path appears to be inside {marker}; sync tools can slow or lock workflow files."})

    # A WSL-native UNC path is already on the fast Linux filesystem; only a
    # Windows-drive path under WSL pays the /mnt 9P penalty.
    if use_wsl and path.drive and not is_wsl_unc_path(path):
        messages.append({"status": "WARNING", "message": "Under WSL this Windows-drive path is reached via the slower /mnt/<drive> 9P mount; staging the project on the WSL filesystem (\\\\wsl.localhost\\...) is faster for genomics I/O."})

    if not messages:
        messages.append({"status": "PASS", "message": "Working directory is writable and has sufficient free space."})
    return messages


class ProjectManager:
    def create_project(self, project_name: str, working_directory: Path,
                       overwrite: bool = False) -> Path:
        """Scaffold a new project directory.

        Refuses to scaffold over an existing project unless *overwrite* is set:
        the writes below reset samples.tsv, contrasts.yaml, gene_sets.yaml and
        config.yaml to empty defaults, which would destroy a user's sample sheet
        and experiment configuration without warning.
        """
        safe_name = project_name.strip().replace(" ", "_")
        if not safe_name:
            raise ValueError("Project name cannot be empty.")
        if safe_name in {".", ".."}:
            raise ValueError("Project name cannot be '.' or '..'.")
        # Reject names with characters that break Snakemake wildcards or the
        # filesystem path (slash, colon, #, parentheses, …) rather than silently
        # creating an unusable directory.
        if not re.fullmatch(SAFE_ID_PATTERN, safe_name):
            raise ValueError(
                "Project name may only contain letters, numbers, '_', '-' and '.' "
                f"(spaces become underscores). Got: {project_name!r}")
        working_root = working_directory.expanduser().resolve()
        requested_root = working_root / safe_name
        root = requested_root.resolve()
        if root.parent != working_root or root != requested_root:
            raise ValueError("Project destination must be an immediate child inside the selected working directory.")
        if root.exists() and not root.is_dir():
            raise ProjectExistsError(root, is_file=True)
        if root.is_dir() and any(root.iterdir()) and not overwrite:
            raise ProjectExistsError(root, is_project=is_project_root(root))
        for relative in PROJECT_DIRS:
            (root / relative).mkdir(parents=True, exist_ok=True)

        manifest = {
            "project_name": safe_name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "app_version": APP_VERSION,
            "workflow_version": WORKFLOW_VERSION,
            "workflow_source": "BulkSeq Studio scaffold",
        }
        self._write_yaml(root / "config" / "project_manifest.yaml", manifest)
        self._write_yaml(root / "config" / "contrasts.yaml", {"contrasts": []})
        self._write_yaml(root / "config" / "gene_sets.yaml", {"gene_sets": {}})
        (root / "config" / "sra_accessions.txt").write_text("", encoding="utf-8")
        # Written as bytes, not text: write_text translates "\n" to the host's newline, so the
        # same scaffold produced a CRLF sheet on Windows and an LF one on Linux. save_metadata
        # preserves whatever terminator it finds, so a new sheet is the one place to settle on
        # line feeds and keep a project's sample sheet identical wherever it was created.
        scaffold_header = ("\t".join(SCAFFOLD_METADATA_COLUMNS) + "\n").encode("utf-8")
        (root / "config" / "samples.auto_generated.tsv").write_bytes(scaffold_header)
        (root / "config" / "samples.tsv").write_bytes(scaffold_header)
        self._write_yaml(root / "references" / "project_reference.lock.yaml", {"reference": None, "locked": False})

        cfg = default_config(safe_name, root)
        self.save_config(root, cfg)
        # Copy the bundled defaults so the workflow can diff config vs defaults
        # (the Customized / Non-standard Parameters section of run_summary).
        default_src = data_path("default_config.yaml")
        if default_src.exists():
            shutil.copyfile(default_src, root / "config" / "default_config.yaml")
        self.copy_workflow_metadata(root)
        return root

    def save_config(self, project_root: Path, config: AppConfig) -> None:
        self._write_yaml(project_root / "config" / "config.yaml", config.model_dump(mode="json"))

    def load_config(self, project_root: Path) -> AppConfig:
        with (project_root / "config" / "config.yaml").open("r", encoding="utf-8") as handle:
            data, _ = normalize_decimal_commas(yaml.safe_load(handle))
        return AppConfig.model_validate(data)

    @staticmethod
    def workflow_tree_digest(root: Path) -> str | None:
        """Hash workflow content while excluding generated caches and its metadata file."""
        if not root.is_dir():
            return None
        h = hashlib.sha256()
        paths = [
            p for p in root.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.relative_to(root).parts
            and p.suffix.casefold() not in {".pyc", ".pyo"}
            and p.name != "workflow_metadata.yaml"
        ]
        for path in sorted(paths, key=lambda path: path.relative_to(root).as_posix()):
            h.update(path.relative_to(root).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
        return h.hexdigest()

    @staticmethod
    def _legacy_workflow_tree_digests(root: Path) -> set[str]:
        """Read pre-0.32.0 Windows and POSIX orderings independent of this host."""
        if not root.is_dir():
            return set()
        paths = [
            p for p in root.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.relative_to(root).parts
            and p.suffix.casefold() not in {".pyc", ".pyo"}
            and p.name != "workflow_metadata.yaml"
        ]

        def digest_for(key) -> str:
            h = hashlib.sha256()
            for path in sorted(paths, key=key):
                h.update(path.relative_to(root).as_posix().encode("utf-8"))
                h.update(b"\0")
                h.update(path.read_bytes())
                h.update(b"\0")
            return h.hexdigest()

        return {
            digest_for(lambda path: PurePosixPath(*path.relative_to(root).parts)),
            digest_for(lambda path: PureWindowsPath(*path.relative_to(root).parts)),
        }

    @staticmethod
    def _is_digest(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))

    @staticmethod
    def _workflow_metadata(project_root: Path) -> dict[str, Any] | None:
        try:
            data = yaml.safe_load((project_root / "workflow" / "workflow_metadata.yaml").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return None
        return data if isinstance(data, dict) else None

    def _promote_workflow_stage(self, stage: Path, target: Path) -> None:
        backup = target.parent / f".workflow-backup-{uuid.uuid4().hex}"
        had_target = target.exists()
        try:
            if had_target and any(target.iterdir()):
                os.replace(target, backup)
            elif had_target:
                target.rmdir()
            os.replace(stage, target)
        except OSError as exc:
            if backup.exists():
                try:
                    os.replace(backup, target)
                except OSError as restore_exc:
                    raise WorkflowSyncError(
                        f"Could not promote the verified workflow stage ({exc}) and could not restore the original "
                        f"workflow from {backup}: {restore_exc}. Restore that directory manually before running.") from restore_exc
                raise WorkflowSyncError(
                    f"Could not promote the verified workflow stage: {exc}. The original project workflow was restored; "
                    "resolve the filesystem error and run again.") from exc
            raise WorkflowSyncError(
                f"Could not promote the verified workflow stage: {exc}. No project workflow was replaced.") from exc

    def copy_workflow_metadata(self, project_root: Path) -> None:
        source = workflow_root()
        target = project_root / "workflow"
        try:
            source_ready = source.is_dir() and (source / "Snakefile").is_file() and (source / "Snakefile").stat().st_size > 0
        except OSError as exc:
            raise WorkflowSyncError(
                f"Bundled workflow files could not be inspected, so the project workflow was not changed: {exc}") from exc
        if not source_ready:
            raise WorkflowSyncError(
                "Bundled workflow files are incomplete (missing a non-empty Snakefile), so the project workflow was "
                "not changed. Repair or reinstall the application before running.")
        bundled_digest = self.workflow_tree_digest(source)
        if bundled_digest is None:
            raise WorkflowSyncError("Bundled workflow files could not be read, so the project workflow was not changed.")
        stage: Path | None = None
        try:
            # A project can already be close to Windows MAX_PATH. Reserve an
            # empty same-filesystem sibling without reducing that path budget.
            stage = Path(tempfile.mkdtemp(prefix="", dir=project_root))
            if len(stage.name) > len(target.name):
                raise WorkflowSyncError(
                    "Could not reserve a short workflow stage. The project workflow was not changed.")
            shutil.copytree(
                source,
                stage,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                dirs_exist_ok=True,
            )
            staged_digest = self.workflow_tree_digest(stage)
            if staged_digest != bundled_digest:
                raise WorkflowSyncError(
                    "The staged project workflow does not match the bundled workflow. The project workflow was not changed.")
            self._write_workflow_metadata(stage, bundled_digest, staged_digest)
            self._promote_workflow_stage(stage, target)
        except WorkflowSyncError:
            raise
        except OSError as exc:
            raise WorkflowSyncError(
                f"Could not stage the bundled workflow: {exc}. The project workflow was not changed.") from exc
        finally:
            if stage is not None and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def _bundled_workflow_digest(self) -> str | None:
        source = workflow_root()
        try:
            ready = source.is_dir() and (source / "Snakefile").is_file() and (source / "Snakefile").stat().st_size > 0
        except OSError:
            return None
        return self.workflow_tree_digest(source) if ready else None

    def workflow_digest_of(self, project_root: Path) -> str | None:
        data = self._workflow_metadata(project_root)
        recorded = data.get("workflow_digest") if data else None
        return str(recorded) if recorded else None

    def workflow_execution_digest_of(self, project_root: Path) -> str | None:
        data = self._workflow_metadata(project_root)
        recorded = data.get("workflow_execution_digest") if data else None
        return str(recorded) if recorded else None

    def _write_workflow_metadata(self, workflow: Path, bundle_digest: str, execution_digest: str) -> None:
        self._write_yaml(
            workflow / "workflow_metadata.yaml",
            {"app_version": APP_VERSION, "workflow_version": WORKFLOW_VERSION,
             "workflow_digest": bundle_digest, "workflow_execution_digest": execution_digest,
             "copied_at": datetime.now().isoformat(timespec="seconds")},
        )

    def verify_workflow_integrity(self, project_root: Path) -> str:
        """Confirm that the project tree still matches its recorded execution digest."""
        metadata = self._workflow_metadata(project_root)
        recorded = ((metadata or {}).get("workflow_execution_digest")
                    if "workflow_execution_digest" in (metadata or {})
                    else (metadata or {}).get("workflow_digest"))
        actual = self.workflow_tree_digest(project_root / "workflow")
        if not self._is_digest(recorded):
            raise WorkflowSyncError(
                "The project workflow has no valid execution digest, so it cannot be verified. Synchronize the "
                "workflow before running.")
        if actual != recorded:
            if "workflow_execution_digest" not in (metadata or {}) and recorded in self._legacy_workflow_tree_digests(
                    project_root / "workflow"):
                return actual
            raise WorkflowSyncError(
                "The project workflow changed since it was copied. Its recorded digest does not match the files that "
                "would run, so no files were overwritten. Restore the recorded workflow from a backup or review the "
                "local edits before running again.")
        return actual

    @staticmethod
    def _version_tuple(version: str) -> tuple[int, ...]:
        # Parse "0.8.4" -> (0, 8, 4); non-numeric chunks degrade to 0 so a malformed
        # version compares as older rather than raising.
        parts = []
        for chunk in str(version).split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    def workflow_version_of(self, project_root: Path) -> str | None:
        # The workflow version recorded when the project's workflow/ was last copied.
        data = self._workflow_metadata(project_root)
        if data is None:
            return None
        recorded = data.get("workflow_version")
        return str(recorded) if recorded else None

    def sync_workflow_if_outdated(self, project_root: Path) -> str | None:
        # An existing project keeps its own copy of workflow/, so a workflow fix
        # shipped in an app update does not reach it on its own. Re-copy the bundled
        # workflow when the project's recorded version is missing or older than this
        # build's; return the version synced to, or None when already current.
        metadata = self._workflow_metadata(project_root)
        recorded = self.workflow_version_of(project_root)
        version_current = recorded is not None and self._version_tuple(recorded) >= self._version_tuple(WORKFLOW_VERSION)
        workflow = project_root / "workflow"
        actual_digest = self.workflow_tree_digest(workflow)
        # Also re-sync when the bundled workflow content changed under the SAME version — this
        # project ships frequent in-place same-version revisions, and a version-only check would
        # leave those projects on a stale (buggy) workflow copy.
        bundled_digest = self._bundled_workflow_digest()
        if bundled_digest is None:
            raise WorkflowSyncError(
                "Bundled workflow files are unavailable or incomplete (missing a non-empty Snakefile), so the project "
                "workflow was not changed. Repair or reinstall the application before running.")
        recorded_execution_digest = ((metadata or {}).get("workflow_execution_digest")
                                     if "workflow_execution_digest" in (metadata or {})
                                     else (metadata or {}).get("workflow_digest"))
        if not self._is_digest(recorded_execution_digest):
            if actual_digest == bundled_digest:
                self._write_workflow_metadata(workflow, bundled_digest, actual_digest)
                return WORKFLOW_VERSION
            raise WorkflowSyncError(
                "The project workflow has no valid recorded digest and differs from the bundled workflow. No files "
                "were overwritten. Review the local workflow or restore a known copy before running.")
        self.verify_workflow_integrity(project_root)
        digest_current = self.workflow_digest_of(project_root) == bundled_digest
        if version_current and digest_current:
            return None
        self.copy_workflow_metadata(project_root)
        return WORKFLOW_VERSION

    @staticmethod
    def _write_yaml(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write only when the content changed, so re-saving config on Resume does not touch the file's
        # mtime (keeps Snakemake from re-running steps that depend on it). Compare on decoded text so a
        # platform newline difference does not count as a change.
        text = yaml.safe_dump(data, sort_keys=False)
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == text:
                    return
            except (OSError, UnicodeDecodeError):
                pass
        path.write_text(text, encoding="utf-8")
