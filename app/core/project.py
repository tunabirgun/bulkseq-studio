from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.constants import APP_VERSION, PROJECT_DIRS, WORKFLOW_VERSION
from app.core.config_models import AppConfig, default_config
from app.core.paths import workflow_root


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

    if use_wsl and path.drive:
        messages.append({"status": "WARNING", "message": "Under WSL this path is reached via /mnt/<drive>; staging the project in the WSL home directory is often faster for genomics I/O."})

    if not messages:
        messages.append({"status": "PASS", "message": "Working directory is writable and has sufficient free space."})
    return messages


class ProjectManager:
    def create_project(self, project_name: str, working_directory: Path) -> Path:
        safe_name = project_name.strip().replace(" ", "_")
        if not safe_name:
            raise ValueError("Project name cannot be empty.")
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
        self.copy_workflow_metadata(root)
        return root

    def save_config(self, project_root: Path, config: AppConfig) -> None:
        self._write_yaml(project_root / "config" / "config.yaml", config.model_dump(mode="json"))

    def load_config(self, project_root: Path) -> AppConfig:
        with (project_root / "config" / "config.yaml").open("r", encoding="utf-8") as handle:
            return AppConfig.model_validate(yaml.safe_load(handle))

    def copy_workflow_metadata(self, project_root: Path) -> None:
        source = workflow_root()
        target = project_root / "workflow"
        if source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True)
        self._write_yaml(
            project_root / "workflow" / "workflow_metadata.yaml",
            {"workflow_version": WORKFLOW_VERSION, "copied_at": datetime.now().isoformat(timespec="seconds")},
        )

    @staticmethod
    def _write_yaml(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
