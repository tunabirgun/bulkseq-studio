"""Run one bundled benchmark end to end through the native BulkSeq Studio GUI.

This local QA harness deliberately accepts only one preset per process.  It
reuses the exercised create/validate/dry-run path, then clicks Start Run and
keeps append-only evidence while the real WSL workflow runs.  It never removes
or overwrites a project, including after a failed or timed-out run.
"""

from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import time

from run_gui_preflight import (  # noqa: E402
    DialogGuard,
    EVIDENCE_ROOT,
    HarnessFailure,
    create_window,
    exercise_benchmark,
    load_benchmark_catalog,
    pump,
    save_widget_screenshot,
    sha256,
    source_manifest,
    validate_resource_request,
    wait_until,
    write_json,
)

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.constants import APP_VERSION, WORKFLOW_VERSION  # noqa: E402
from app.core.paths import (  # noqa: E402
    usable_disk_free_bytes,
    windows_to_wsl_path,
    wsl_default_distro,
    wsl_recommended_workdir,
    wsl_unc_distro,
)
from app.core.readiness import check_readiness  # noqa: E402
from app.ui.theme import apply_theme  # noqa: E402


FULL_ROOT = EVIDENCE_ROOT / "full-runs"
HASH_LIMIT = 128 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, payload)
    temporary.replace(path)


def full_source_manifest() -> list[dict[str, object]]:
    """Snapshot every workflow source plus GUI/harness launch-critical files."""
    entries = list(source_manifest())
    candidates = [Path(__file__).resolve(), REPO_ROOT / "app/core/provenance.py"]
    for source_root in (REPO_ROOT / "app", REPO_ROOT / "workflow"):
        candidates.extend(
            path for path in source_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.casefold() not in {".pyc", ".pyo"}
        )
    seen = {str(entry["path"]) for entry in entries}
    for path in sorted(candidates, key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in seen:
            continue
        seen.add(relative)
        entries.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    return entries


def assert_source_manifest_current(entries: list[dict[str, object]]) -> None:
    changed: list[str] = []
    for entry in entries:
        path = REPO_ROOT / str(entry["path"])
        if (not path.is_file() or path.stat().st_size != int(entry["bytes"])
                or sha256(path) != str(entry["sha256"])):
            changed.append(str(entry["path"]))
    if changed:
        raise HarnessFailure(
            "Application/workflow source changed during the benchmark: " + ", ".join(changed)
        )


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_local_fastq(source: Path, target: Path) -> str:
    """Copy within WSL natively when possible; otherwise use a normal file copy."""
    source_text = str(source)
    target_text = str(target)
    if source_text.startswith("\\\\wsl") and target_text.startswith("\\\\wsl"):
        distro = wsl_default_distro()
        if not distro:
            raise HarnessFailure("WSL cache copy requested but no default distro is available")
        result = subprocess.run(
            [
                "wsl", "-d", distro, "--exec", "cp", "--reflink=auto", "--",
                windows_to_wsl_path(source), windows_to_wsl_path(target),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform.startswith("win") else 0
            ),
        )
        if result.returncode != 0:
            raise HarnessFailure(
                f"WSL-native cache copy failed for {source.name}: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        return "WSL cp --reflink=auto"
    shutil.copyfile(source, target)
    return "shutil.copyfile"


def seed_fastq_inputs(project_root: Path, cache_dir: Path) -> dict[str, object]:
    """Copy only sample-sheet-declared FASTQs from a verified local cache."""
    project_root = project_root.resolve()
    cache_dir = cache_dir.resolve()
    sample_sheet = project_root / "config" / "samples.tsv"
    if not cache_dir.is_dir():
        raise HarnessFailure(f"Input cache directory is missing: {cache_dir}")
    if not sample_sheet.is_file():
        raise HarnessFailure(f"Project sample sheet is missing: {sample_sheet}")
    if cache_dir == project_root or project_root in cache_dir.parents:
        raise HarnessFailure("Input cache must be outside the new benchmark project")

    seeded: list[dict[str, object]] = []
    with sample_sheet.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise HarnessFailure("Project sample sheet has no data rows")
    for row in rows:
        for field, md5_field in (("fastq_1", "fastq_1_md5"), ("fastq_2", "fastq_2_md5")):
            relative_text = str(row.get(field, "")).strip()
            if not relative_text:
                continue
            expected_md5 = str(row.get(md5_field, "")).strip().casefold()
            if len(expected_md5) != 32 or any(ch not in "0123456789abcdef" for ch in expected_md5):
                raise HarnessFailure(f"Missing or malformed {md5_field} for {relative_text}")
            relative = Path(relative_text)
            target = (project_root / relative).resolve()
            if project_root not in target.parents:
                raise HarnessFailure(f"FASTQ target escapes the project: {relative_text}")
            source = cache_dir / relative.name
            if not source.is_file() or source.is_symlink():
                raise HarnessFailure(f"Cached FASTQ is missing or not a regular file: {source}")
            if source.stat().st_size < 1:
                raise HarnessFailure(f"Cached FASTQ is empty: {source}")
            actual_md5 = _md5(source)
            if actual_md5 != expected_md5:
                raise HarnessFailure(
                    f"Cached FASTQ MD5 mismatch for {source.name}: "
                    f"expected {expected_md5}, observed {actual_md5}"
                )
            if target.exists():
                raise HarnessFailure(f"Refusing to overwrite seeded FASTQ target: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".cache-seed.part")
            if temporary.exists():
                raise HarnessFailure(f"Refusing to overwrite cache-seed temporary file: {temporary}")
            try:
                copy_method = _copy_local_fastq(source, temporary)
                if temporary.stat().st_size != source.stat().st_size or _md5(temporary) != expected_md5:
                    raise HarnessFailure(f"Copied FASTQ failed size/MD5 verification: {target.name}")
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            seeded.append({
                "target": relative.as_posix(),
                "source": str(source),
                "bytes": target.stat().st_size,
                "md5": expected_md5,
                "copy_method": copy_method,
            })
    if not seeded:
        raise HarnessFailure("No FASTQ inputs were declared for local-cache seeding")
    return {
        "method": "verified local FASTQ copy",
        "cache_dir": str(cache_dir),
        "seeded_at": utc_now(),
        "files": seeded,
    }


def _validate_delimited(path: Path, *, delimiter: str,
                        required_columns: set[str], allow_comments: bool = False) -> None:
    with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header: list[str] | None = None
        data_rows = 0
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if allow_comments and row[0].lstrip().startswith("#"):
                continue
            if header is None:
                header = [cell.strip().lstrip("# ") for cell in row]
                missing = sorted(required_columns.difference(header))
                if missing:
                    raise ValueError(f"required columns are missing: {missing}")
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"data row has {len(row)} fields but the header has {len(header)}"
                )
            data_rows += 1
    if header is None:
        raise ValueError("delimited header is missing")
    if data_rows < 1:
        raise ValueError("delimited artifact has no data rows")


def readiness_payload(benchmark: dict) -> dict[str, object]:
    items = check_readiness()
    serialized = [item.__dict__ for item in items]
    by_name = {item.name: item for item in items}
    required = {
        "WSL distribution",
        "WSL env:bulkseq",
        "WSL snakemake",
        "WSL Rscript",
        "WSL R packages",
    }
    if str(benchmark.get("type", "sra")).lower() != "microarray":
        required.update({
            "WSL fastqc", "WSL multiqc", "WSL fastp", "WSL STAR",
            "WSL featureCounts", "WSL samtools",
        })
    failures = [
        name for name in sorted(required)
        if name not in by_name or by_name[name].status != "PASS"
    ]
    return {"checked_at": utc_now(), "required": sorted(required),
            "failures": failures, "items": serialized}


def wsl_sample(
    project_root: Path, distro: str, run_tag: str, *, include_disk: bool,
) -> dict[str, object]:
    linux_root = windows_to_wsl_path(project_root)
    tag_assignment = shlex.quote(f"{run_tag}=1")
    disk = (
        f"du -sb -- {shlex.quote(linux_root)} 2>/dev/null | awk '{{print \"PROJECT_BYTES=\"$1}}'; "
        f"df -B1 --output=avail {shlex.quote(linux_root)} 2>/dev/null | tail -1 | "
        "awk '{print \"DISK_AVAILABLE=\"$1}'; "
        if include_disk else ""
    )
    script = (
        "awk '/MemTotal:/{print \"MEM_TOTAL_KB=\"$2} "
        "/MemAvailable:/{print \"MEM_AVAILABLE_KB=\"$2}' /proc/meminfo; "
        "awk '{print \"LOAD1=\"$1}' /proc/loadavg; "
        "for env_file in /proc/[0-9]*/environ; do "
        "pid=${env_file#/proc/}; pid=${pid%/environ}; "
        f"if (tr '\\0' '\\n' < \"$env_file\") 2>/dev/null | grep -Fqx -- {tag_assignment}; "
        "then echo \"TAGPID=$pid\"; fi; done; "
        "ps -eo pid=,ppid=,rss=,pcpu=,comm=,args= | sed 's/^/PROC\\t/'; "
        + disk
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapped = f"echo {encoded} | base64 -d | bash"
    try:
        result = subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-lc", wrapped],
            capture_output=True, text=True, timeout=30, check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"time": utc_now(), "error": f"{type(exc).__name__}: {exc}"}
    payload: dict[str, object] = {
        "time": utc_now(), "returncode": result.returncode,
    }
    processes: dict[int, dict[str, object]] = {}
    tagged_pids: set[int] = set()
    for line in (result.stdout or "").splitlines():
        if line.startswith("PROC\t"):
            fields = line[5:].split(None, 5)
            if len(fields) == 6:
                try:
                    pid, ppid, rss = (int(fields[index]) for index in range(3))
                    cpu = float(fields[3])
                except ValueError:
                    continue
                processes[pid] = {
                    "ppid": ppid, "rss": rss, "cpu": cpu,
                    "comm": fields[4], "args": fields[5],
                }
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "TAGPID":
            try:
                tagged_pids.add(int(value))
            except ValueError:
                pass
            continue
        try:
            payload[key.lower()] = float(value) if "." in value else int(value)
        except ValueError:
            payload[key.lower()] = value
    active = tagged_pids.intersection(processes)
    roots = {
        pid for pid in active if int(processes[pid]["ppid"]) not in active
    }
    changed = True
    while changed:
        changed = False
        for pid, process in processes.items():
            if pid not in active and int(process["ppid"]) in active:
                active.add(pid)
                changed = True
    payload.update({
        "run_tag_roots": len(roots),
        "pipeline_rss_kb": sum(int(processes[pid]["rss"]) for pid in active),
        "pipeline_cpu_percent": round(
            sum(float(processes[pid]["cpu"]) for pid in active), 3
        ),
        "pipeline_processes": len(active),
        "pipeline_commands": sorted({str(processes[pid]["comm"]) for pid in active}),
    })
    if result.returncode != 0:
        payload["stderr"] = (result.stderr or "").strip()[-1000:]
    return payload


def require_host_disk_reserve(project_root: Path, minimum_host_free_gb: float) -> int:
    """Return physical backing-drive free bytes, or fail before WSL can fill it."""
    minimum_host_free_bytes = max(1, int(minimum_host_free_gb * 1024**3))
    available = usable_disk_free_bytes(project_root)
    if available < minimum_host_free_bytes:
        raise HarnessFailure(
            "The physical drive backing WSL is below the benchmark safety reserve: "
            f"available={available / 1024**3:.1f} GiB, "
            f"required={minimum_host_free_gb:.1f} GiB"
        )
    return available


def final_artifacts(project_root: Path, input_type: str) -> dict[str, object]:
    required = [
        "results/reports/run_summary.json",
        "results/reports/run_summary.txt",
        "results/reports/results_report.html",
        "results/deseq2/deseq2_results.csv",
        "results/export/normalized_expression_matrix.csv",
    ]
    if input_type == "microarray":
        required.append("results/microarray/normalized_expression.tsv")
    else:
        required.extend([
            "results/counts/counts.txt",
            "results/qc/multiqc/multiqc_report.html",
            "references/reference.lock.json",
            "checks/05_reference_validation.passed",
        ])
    missing = [
        relative for relative in required
        if not (project_root / relative).is_file()
        or (project_root / relative).stat().st_size == 0
    ]

    invalid_required: list[str] = []
    summary_payload: dict[str, object] | None = None
    for relative in required:
        path = project_root / relative
        if not path.is_file() or path.stat().st_size == 0:
            continue
        try:
            if relative == "results/reports/run_summary.json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("top-level JSON value is not an object")
                summary_payload = payload
                required_summary = {
                    "app_version", "workflow_version", "input", "sanity_checks",
                    "output_paths", "software_versions",
                }
                missing_keys = sorted(required_summary.difference(payload))
                if missing_keys:
                    raise ValueError(f"run summary keys are missing: {missing_keys}")
            elif relative == "results/deseq2/deseq2_results.csv":
                _validate_delimited(
                    path, delimiter=",",
                    required_columns={"gene_id", "log2FoldChange", "padj"},
                )
            elif relative == "results/export/normalized_expression_matrix.csv":
                _validate_delimited(path, delimiter=",", required_columns={"gene_id"})
            elif relative == "results/microarray/normalized_expression.tsv":
                _validate_delimited(path, delimiter="\t", required_columns={"gene_id"})
            elif relative == "results/counts/counts.txt":
                _validate_delimited(
                    path, delimiter="\t", required_columns={"Geneid"}, allow_comments=True,
                )
            elif path.suffix == ".html":
                content = path.read_text(encoding="utf-8", errors="strict")
                lowered = content.casefold()
                if "<html" not in lowered or "</html>" not in lowered:
                    raise ValueError("HTML document root is missing or incomplete")
                if relative == "results/reports/results_report.html" and "bulkseq studio" not in lowered:
                    raise ValueError("BulkSeq Studio report identity is missing")
            elif path.suffix == ".txt":
                content = path.read_text(encoding="utf-8", errors="strict")
                if not content.strip():
                    raise ValueError("text artifact is empty")
                if relative == "results/reports/run_summary.txt" and "run summary" not in content.casefold():
                    raise ValueError("run-summary identity is missing")
        except Exception as exc:
            invalid_required.append(f"{relative}: {type(exc).__name__}: {exc}")

    ppi_config = (summary_payload or {}).get("ppi")
    ppi_enabled = isinstance(ppi_config, dict) and bool(ppi_config.get("enabled"))
    if ppi_enabled:
        sidecar = project_root / "results" / "networks" / "string_ppi_provenance.json"
        provenance = (summary_payload or {}).get("ppi_provenance")
        if not sidecar.is_file() or sidecar.stat().st_size == 0:
            invalid_required.append(
                "results/networks/string_ppi_provenance.json: active PPI sidecar is missing"
            )
        if not isinstance(provenance, dict) or provenance.get("status") not in {"PASS", "WARNING"}:
            invalid_required.append(
                "results/reports/run_summary.json: active PPI realized provenance is not PASS/WARNING"
            )

    checks: list[dict[str, object]] = []
    bad_checks: list[str] = []
    for path in sorted((project_root / "checks").glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            checks.append({"path": path.name, "status": "INVALID", "error": str(exc)})
            bad_checks.append(path.name)
            continue
        status = payload.get("status") if isinstance(payload, dict) else None
        checks.append({"path": path.name, "status": status})
        if status not in {"PASS", "WARNING"}:
            bad_checks.append(path.name)

    evidence_paths = [project_root / relative for relative in required]
    evidence_paths.extend(sorted((project_root / "checks").glob("*.json")))
    evidence_paths.extend(sorted((project_root / "references").glob("*.integrity.json")))
    evidence_paths.extend(sorted((project_root / "results" / "networks").glob("*provenance.json")))
    inventory: list[dict[str, object]] = []
    seen: set[Path] = set()
    for path in evidence_paths:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        size = path.stat().st_size
        item: dict[str, object] = {
            "path": path.relative_to(project_root).as_posix(), "bytes": size,
        }
        if size <= HASH_LIMIT:
            item["sha256"] = sha256(path)
        else:
            item["sha256"] = None
            item["hash_note"] = f"not hashed by QA harness above {HASH_LIMIT} bytes"
        inventory.append(item)

    if input_type != "microarray":
        lock_path = project_root / "references" / "reference.lock.json"
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception as exc:
            lock = {"status": "INVALID", "error": str(exc)}
        if lock.get("status") != "PASS":
            bad_checks.append("references/reference.lock.json")
        for key in ("genome", "annotation"):
            integrity = ((lock.get(key) or {}).get("integrity") or {})
            if integrity.get("md5_status") != "VERIFIED":
                bad_checks.append(f"reference.{key}.md5_status")
    else:
        lock = {}

    return {
        "required": required,
        "missing": missing,
        "invalid_required": invalid_required,
        "checks": checks,
        "bad_checks": sorted(set(bad_checks)),
        "reference_lock": lock,
        "inventory": inventory,
    }


def execute_run(window, guard: DialogGuard, project_root: Path, benchmark: dict,
                evidence_dir: Path, manifest: dict[str, object], manifest_path: Path,
                *, timeout_s: float, sample_seconds: float,
                minimum_host_free_gb: float, launch_timeout_s: float) -> dict[str, object]:
    check_path = project_root / "checks" / "01_input_validation.json"
    check = json.loads(check_path.read_text(encoding="utf-8"))
    if check.get("status") not in {"PASS", "WARNING"}:
        raise HarnessFailure(f"Pre-run validation is not startable: {check.get('status')!r}")

    minimum_host_free_bytes = max(1, int(minimum_host_free_gb * 1024**3))
    initial_host_free = require_host_disk_reserve(project_root, minimum_host_free_gb)
    manifest["disk_safety"] = {
        "initial_host_backing_free_bytes": initial_host_free,
        "minimum_host_backing_free_bytes": minimum_host_free_bytes,
    }
    atomic_json(manifest_path, manifest)

    immutable_paths = [project_root / "config" / "config.yaml"]
    configured_samples = str(getattr(window.config.input, "samples", "config/samples.tsv"))
    sample_path = Path(configured_samples)
    if not sample_path.is_absolute():
        sample_path = project_root / sample_path
    immutable_paths.append(sample_path)
    missing_immutable = [str(path) for path in immutable_paths if not path.is_file()]
    if missing_immutable:
        raise HarnessFailure(
            "Scientific launch inputs are missing before Start Run: "
            + ", ".join(missing_immutable)
        )
    immutable_hashes = {
        path.resolve(): sha256(path)
        for path in immutable_paths
    }

    def assert_immutable_inputs(context: str) -> None:
        changed = [
            str(path) for path, recorded in immutable_hashes.items()
            if not path.is_file() or sha256(path) != recorded
        ]
        if changed:
            raise HarnessFailure(
                f"Scientific configuration changed {context}: " + ", ".join(changed)
            )

    window.tabs.setCurrentIndex(8)
    pump(100)
    window.execution_details_toggle.setChecked(False)
    window.command_text.clear()
    window.log_text.clear()
    start_button = window.run_action_buttons["run"]
    if not start_button.isVisible() or not start_button.isEnabled():
        raise HarnessFailure("Start Run is not visible and enabled")
    before = save_widget_screenshot(window, evidence_dir / "run-before-start.png")
    QTest.mouseClick(start_button, Qt.MouseButton.LeftButton)

    wait_until(
        lambda: (
            window._run_mode == "run"
            and window.runner is not None
            and window.runner.is_running()
            and bool(window.command_text.text().strip())
        ),
        timeout_s=max(180.0, launch_timeout_s),
        label="validated GUI Start Run launch",
    )
    guard.assert_clean("full-run launch")
    runner = window.runner
    if runner is None or not runner.run_tag:
        raise HarnessFailure("The WSL run started without a durable run tag")

    distro = wsl_unc_distro(project_root) or wsl_default_distro()
    if not distro:
        raise HarnessFailure("Could not identify the WSL distribution for monitoring")
    if runner.distro not in {None, distro}:
        raise HarnessFailure(
            f"Runner distro {runner.distro!r} disagrees with project distro {distro!r}"
        )

    started_wall = utc_now()
    started = time.monotonic()
    command_path = evidence_dir / "run-command.txt"
    command_path.write_text(window.command_text.text() + "\n", encoding="utf-8")
    streamed_path = evidence_dir / "run-lines.txt"
    streamed = streamed_path.open("a", encoding="utf-8", buffering=1)

    def append_line(line: str) -> None:
        streamed.write(line.rstrip("\n") + "\n")

    window.runner_thread.line.connect(append_line)
    transitions: list[dict[str, object]] = []
    samples_path = evidence_dir / "resource-samples.jsonl"
    previous_phase = ""
    thresholds_seen: set[int] = set()
    next_sample = started
    next_disk = started
    next_heartbeat = started
    completion_grace: float | None = None
    screenshots = [before, save_widget_screenshot(window, evidence_dir / "run-started.png")]
    try:
        while True:
            pump(350)
            guard.assert_clean("full pipeline run")
            now = time.monotonic()
            elapsed = now - started
            if elapsed > timeout_s:
                raise HarnessFailure(f"Full run exceeded {timeout_s / 3600:.1f} hours")

            phase = window.phase_label.text().strip()
            progress = window.progress.value()
            if phase and phase != previous_phase:
                previous_phase = phase
                transition = {
                    "time": utc_now(), "elapsed_seconds": round(elapsed, 3),
                    "phase": phase, "progress": progress,
                }
                transitions.append(transition)
                slug = "".join(character if character.isalnum() else "-" for character in phase)
                slug = "-".join(part for part in slug.split("-") if part)[:70] or "phase"
                screenshots.append(save_widget_screenshot(
                    window, evidence_dir / f"phase-{len(transitions):03d}-{slug}.png"))
            for threshold in (25, 50, 75):
                if progress >= threshold and threshold not in thresholds_seen:
                    thresholds_seen.add(threshold)
                    screenshots.append(save_widget_screenshot(
                        window, evidence_dir / f"progress-{threshold:02d}.png"))

            if now >= next_sample:
                include_disk = now >= next_disk
                sample = wsl_sample(
                    project_root, distro, runner.run_tag, include_disk=include_disk
                )
                sample["host_backing_free_bytes"] = usable_disk_free_bytes(project_root)
                sample["elapsed_seconds"] = round(elapsed, 3)
                with samples_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
                required_sample_keys = {
                    "returncode", "mem_total_kb", "mem_available_kb", "load1",
                    "pipeline_rss_kb", "pipeline_cpu_percent", "pipeline_processes",
                    "host_backing_free_bytes",
                }
                if include_disk:
                    required_sample_keys.update({"project_bytes", "disk_available"})
                missing_sample_keys = sorted(required_sample_keys.difference(sample))
                if sample.get("returncode") != 0 or missing_sample_keys:
                    raise HarnessFailure(
                        "WSL resource sampling failed: "
                        f"returncode={sample.get('returncode')!r} "
                        f"missing={missing_sample_keys} error={sample.get('error')!r} "
                        f"stderr={sample.get('stderr')!r}"
                    )
                if int(sample["host_backing_free_bytes"]) < minimum_host_free_bytes:
                    require_host_disk_reserve(project_root, minimum_host_free_gb)
                next_sample = now + sample_seconds
                if include_disk:
                    next_disk = now + max(sample_seconds, 300)

            if now >= next_heartbeat:
                assert_immutable_inputs("during the run")
                heartbeat = {
                    "event": "heartbeat", "benchmark": benchmark["id"],
                    "elapsed_seconds": round(elapsed, 1), "progress": progress,
                    "phase": phase or window.status_label.text(),
                }
                print(json.dumps(heartbeat), flush=True)
                manifest["active_run"] = heartbeat
                manifest["transitions"] = transitions
                atomic_json(manifest_path, manifest)
                next_heartbeat = now + 60

            complete_marker = "Process finished with exit code" in window.log_text.toPlainText()
            if window._run_mode is None and complete_marker:
                break
            if runner.process is not None and runner.process.poll() is not None:
                if completion_grace is None:
                    completion_grace = now + 15
                elif now >= completion_grace and not complete_marker:
                    raise HarnessFailure(
                        "Runner exited but the GUI completion callback did not finish"
                    )
    finally:
        try:
            window.runner_thread.line.disconnect(append_line)
        except (RuntimeError, TypeError):
            pass
        streamed.close()

    elapsed = time.monotonic() - started
    assert_immutable_inputs("between the final heartbeat and completion")
    final_log = window.log_text.toPlainText()
    gui_log_path = evidence_dir / "run-gui-log.txt"
    gui_log_path.write_text(final_log + "\n", encoding="utf-8")
    screenshots.append(save_widget_screenshot(window, evidence_dir / "run-completed.png"))
    pump(100)
    guard.assert_clean("full pipeline completion")
    if (
        window.status_label.text() != "Completed"
        or getattr(window, "_run_status_key", None) != "PASS"
        or window.progress.value() != 100
        or "Process finished with exit code 0" not in final_log
        or getattr(window, "_run_error_detected", False)
    ):
        raise HarnessFailure(
            "GUI completion contract failed: "
            f"status={window.status_label.text()!r}, progress={window.progress.value()}, "
            f"error_detected={getattr(window, '_run_error_detected', None)!r}"
        )

    artifacts = final_artifacts(project_root, str(window.config.input.type))
    if artifacts["missing"] or artifacts["invalid_required"] or artifacts["bad_checks"]:
        raise HarnessFailure(
            f"Final artifact gate failed: missing={artifacts['missing']} "
            f"invalid={artifacts['invalid_required']} "
            f"bad_checks={artifacts['bad_checks']}"
        )
    return {
        "started_at": started_wall,
        "finished_at": utc_now(),
        "elapsed_seconds": round(elapsed, 3),
        "run_tag": runner.run_tag,
        "distro": distro,
        "command_path": str(command_path),
        "command_sha256": sha256(command_path),
        "streamed_log_path": str(streamed_path),
        "streamed_log_sha256": sha256(streamed_path),
        "gui_log_path": str(gui_log_path),
        "gui_log_sha256": sha256(gui_log_path),
        "resource_samples_path": str(samples_path),
        "resource_samples_sha256": sha256(samples_path),
        "transitions": transitions,
        "screenshots": screenshots,
        "artifacts": artifacts,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-timeout-hours", type=float, default=48.0)
    parser.add_argument("--resource-sample-seconds", type=float, default=30.0)
    parser.add_argument("--minimum-host-free-gb", type=float, default=50.0)
    parser.add_argument(
        "--validation-timeout-minutes",
        type=float,
        default=20.0,
        help="Positive fail-closed ceiling for GUI pre-run fingerprint validation.",
    )
    parser.add_argument(
        "--resource-profile", choices=("low", "balanced", "high", "custom"), default="high",
    )
    parser.add_argument(
        "--resource-threads",
        type=int,
        help="Required positive CPU-worker count for --resource-profile custom.",
    )
    parser.add_argument(
        "--resource-memory-gb",
        type=int,
        help="Required positive RAM allocation for --resource-profile custom.",
    )
    parser.add_argument(
        "--input-cache-dir",
        type=Path,
        help=(
            "Optional local directory containing sample-sheet FASTQ basenames. "
            "Every source and copied target is verified against the declared MD5."
        ),
    )
    args = parser.parse_args(argv)
    try:
        validate_resource_request(
            args.resource_profile,
            args.resource_threads,
            args.resource_memory_gb,
        )
    except HarnessFailure as exc:
        parser.error(str(exc))
    if not (args.validation_timeout_minutes > 0):
        parser.error("--validation-timeout-minutes must be positive")
    return args


def main() -> int:
    args = parse_args()
    run_dir = FULL_ROOT / args.run_id
    if run_dir.exists():
        raise HarnessFailure(f"Refusing to overwrite existing evidence directory: {run_dir}")
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / "run_manifest.json"
    manifest: dict[str, object] = {
        "status": "RUNNING", "run_id": args.run_id,
        "benchmark": args.benchmark, "started_at": utc_now(),
        "python": sys.executable, "platform": platform.platform(),
        "app_version": APP_VERSION, "workflow_version": WORKFLOW_VERSION,
        "requested_resource_profile": args.resource_profile,
        "requested_resource_threads": args.resource_threads,
        "requested_resource_memory_gb": args.resource_memory_gb,
        "requested_resources": {
            "profile": args.resource_profile,
            "total_threads": args.resource_threads,
            "total_memory_gb": args.resource_memory_gb,
        },
        "requested_input_cache_dir": (
            str(args.input_cache_dir.resolve()) if args.input_cache_dir is not None else None
        ),
        "validation_timeout_seconds": args.validation_timeout_minutes * 60.0,
        "policy": "one native-GUI benchmark; no deletion; fail closed on dialogs/checks",
    }
    atomic_json(manifest_path, manifest)

    try:
        catalog = load_benchmark_catalog()
        matches = [item for item in catalog if str(item["id"]) == args.benchmark]
        if len(matches) != 1:
            raise HarnessFailure(f"Unknown or duplicate benchmark id: {args.benchmark}")
        benchmark = matches[0]
        recommended = wsl_recommended_workdir(f"bulkseq-gui-benchmark-full\\{args.run_id}")
        if not recommended:
            raise HarnessFailure("No WSL-native working directory is available")
        project_workdir = Path(recommended)
        if project_workdir.exists():
            raise HarnessFailure(f"Refusing to reuse existing project directory: {project_workdir}")

        readiness = readiness_payload(benchmark)
        manifest["project_workdir"] = str(project_workdir)
        manifest["readiness"] = readiness
        if readiness["failures"]:
            raise HarnessFailure(f"Required environment checks failed: {readiness['failures']}")
        manifest["source_files"] = full_source_manifest()
        atomic_json(manifest_path, manifest)
    except Exception as exc:
        manifest["status"] = "FAIL"
        manifest["finished_at"] = utc_now()
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        atomic_json(manifest_path, manifest)
        print(json.dumps({"event": "full_benchmark_fail", "benchmark": args.benchmark,
                          "run_dir": str(run_dir), "error": manifest["error"]}), flush=True)
        return 1

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("BulkSeqStudioQA")
    app.setApplicationName(f"gui-full-{args.run_id}")
    QSettings().clear()
    QSettings().setValue("theme_mode", "light")
    guard = DialogGuard()
    app.installEventFilter(guard)
    window = None
    try:
        window = create_window(app)
        apply_theme(app, "light")

        def record_resource_evidence(payload: dict[str, object]) -> None:
            manifest["resource_evidence"] = payload
            atomic_json(manifest_path, manifest)

        setup = exercise_benchmark(
            window, guard, benchmark, catalog.index(benchmark), project_workdir,
            run_dir, 1, configure_resources=True,
            resource_profile=args.resource_profile,
            resource_threads=args.resource_threads,
            resource_memory_gb=args.resource_memory_gb,
            resource_evidence_callback=record_resource_evidence,
            project_seed_callback=(
                (lambda root: seed_fastq_inputs(root, args.input_cache_dir))
                if args.input_cache_dir is not None else None
            ),
            validation_timeout_s=args.validation_timeout_minutes * 60.0,
        )
        project_root = Path(str(setup["project_root"]))
        manifest["setup"] = setup
        manifest["resource_evidence"] = setup["resources"]
        atomic_json(manifest_path, manifest)
        result = execute_run(
            window, guard, project_root, benchmark, run_dir, manifest, manifest_path,
            timeout_s=args.run_timeout_hours * 3600,
            sample_seconds=max(5.0, args.resource_sample_seconds),
            minimum_host_free_gb=max(1.0, args.minimum_host_free_gb),
            launch_timeout_s=args.validation_timeout_minutes * 60.0,
        )
        guard.assert_clean("final manifest assembly")
        assert_source_manifest_current(manifest["source_files"])
        manifest["status"] = "PASS"
        manifest["finished_at"] = utc_now()
        manifest["result"] = result
        manifest["unexpected_dialogs"] = guard.unexpected
        manifest.pop("active_run", None)
        atomic_json(manifest_path, manifest)
        print(json.dumps({"event": "full_benchmark_pass", "benchmark": args.benchmark,
                          "run_dir": str(run_dir),
                          "elapsed_seconds": result["elapsed_seconds"]}), flush=True)
        return 0
    except Exception as exc:
        manifest["status"] = "FAIL"
        manifest["finished_at"] = utc_now()
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        manifest["unexpected_dialogs"] = guard.unexpected
        if window is not None and getattr(window, "_run_mode", None) is not None:
            try:
                window._stop_run(announce=False)
                wait_until(
                    lambda: getattr(window, "_run_mode", None) is None,
                    timeout_s=45, label="failed-run shutdown",
                )
            except Exception as stop_exc:
                manifest["stop_error"] = f"{type(stop_exc).__name__}: {stop_exc}"
        if window is not None:
            try:
                failed_log = run_dir / "run-gui-log-failure.txt"
                failed_log.write_text(window.log_text.toPlainText() + "\n", encoding="utf-8")
                manifest["failure_gui_log"] = {
                    "path": str(failed_log), "sha256": sha256(failed_log),
                }
            except Exception as log_exc:
                manifest["failure_log_error"] = f"{type(log_exc).__name__}: {log_exc}"
            try:
                root_value = Path(window.project_root) if window.project_root else None
                if root_value is not None and root_value.exists():
                    manifest["failure_artifacts"] = final_artifacts(
                        root_value, str(window.config.input.type)
                    )
            except Exception as artifact_exc:
                manifest["failure_artifact_error"] = (
                    f"{type(artifact_exc).__name__}: {artifact_exc}"
                )
        atomic_json(manifest_path, manifest)
        print(json.dumps({"event": "full_benchmark_fail", "benchmark": args.benchmark,
                          "run_dir": str(run_dir), "error": manifest["error"]}), flush=True)
        return 1
    finally:
        app.removeEventFilter(guard)
        if window is not None:
            window.close()
            pump(100)


if __name__ == "__main__":
    raise SystemExit(main())
