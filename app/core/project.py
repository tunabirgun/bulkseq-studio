from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.constants import APP_VERSION, PROJECT_DIRS, SAFE_ID_PATTERN, WORKFLOW_VERSION
from app.core.config_models import AppConfig, default_config
from app.core.paths import data_path, is_wsl_unc_path, workflow_root


_DECIMAL_COMMA_RE = re.compile(r"^-?\d+,\d+$")


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
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".bulkseq_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        messages.append({"status": "FAIL", "message": f"Directory is not writable: {exc}"})
        return messages

    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024**3)
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
    def create_project(self, project_name: str, working_directory: Path) -> Path:
        safe_name = project_name.strip().replace(" ", "_")
        if not safe_name:
            raise ValueError("Project name cannot be empty.")
        # Reject names with characters that break Snakemake wildcards or the
        # filesystem path (slash, colon, #, parentheses, …) rather than silently
        # creating an unusable directory.
        if not re.fullmatch(SAFE_ID_PATTERN, safe_name):
            raise ValueError(
                "Project name may only contain letters, numbers, '_', '-' and '.' "
                f"(spaces become underscores). Got: {project_name!r}")
        root = working_directory.expanduser().resolve() / safe_name
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
        (root / "config" / "samples.auto_generated.tsv").write_text("sample_id\tcondition\tlayout\tfastq_1\tfastq_2\treplicate\tbatch\n", encoding="utf-8")
        (root / "config" / "samples.tsv").write_text("sample_id\tcondition\tlayout\tfastq_1\tfastq_2\treplicate\tbatch\n", encoding="utf-8")
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

    def copy_workflow_metadata(self, project_root: Path) -> None:
        source = workflow_root()
        target = project_root / "workflow"
        if source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True)
        self._write_yaml(
            project_root / "workflow" / "workflow_metadata.yaml",
            {"workflow_version": WORKFLOW_VERSION,
             "workflow_digest": self._bundled_workflow_digest(),
             "copied_at": datetime.now().isoformat(timespec="seconds")},
        )

    @staticmethod
    def _bundled_workflow_digest() -> str:
        # Content hash of the bundled workflow/ so a project re-syncs when the scripts change
        # even without a version bump (this project ships frequent same-version in-place
        # revisions). Excludes the metadata file itself, which carries the digest.
        source = workflow_root()
        if not source.exists():
            return ""
        h = hashlib.sha256()
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            if path.name == "workflow_metadata.yaml":
                continue
            h.update(path.relative_to(source).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
        return h.hexdigest()

    def workflow_digest_of(self, project_root: Path) -> str | None:
        meta = project_root / "workflow" / "workflow_metadata.yaml"
        if not meta.exists():
            return None
        try:
            data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        recorded = data.get("workflow_digest")
        return str(recorded) if recorded else None

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
        meta = project_root / "workflow" / "workflow_metadata.yaml"
        if not meta.exists():
            return None
        try:
            data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        recorded = data.get("workflow_version")
        return str(recorded) if recorded else None

    def sync_workflow_if_outdated(self, project_root: Path) -> str | None:
        # An existing project keeps its own copy of workflow/, so a workflow fix
        # shipped in an app update does not reach it on its own. Re-copy the bundled
        # workflow when the project's recorded version is missing or older than this
        # build's; return the version synced to, or None when already current.
        recorded = self.workflow_version_of(project_root)
        version_current = recorded is not None and self._version_tuple(recorded) >= self._version_tuple(WORKFLOW_VERSION)
        # Also re-sync when the bundled workflow content changed under the SAME version — this
        # project ships frequent in-place same-version revisions, and a version-only check would
        # leave those projects on a stale (buggy) workflow copy.
        digest_current = self.workflow_digest_of(project_root) == self._bundled_workflow_digest()
        if version_current and digest_current:
            return None
        self.copy_workflow_metadata(project_root)
        return WORKFLOW_VERSION

    @staticmethod
    def _write_yaml(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
