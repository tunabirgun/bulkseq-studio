"""app.core must import on a machine with no Qt.

The command-line entry points are meant to run on HPC login and compute nodes, where
PySide6 is not installed and there is no display. Any top-level Qt import inside app.core
makes the whole layer unimportable there — and the failure surfaces on the cluster, not
here, unless something checks it.

Runs in a subprocess: this test session has already imported PySide6 (the GUI smoke tests
do), so blocking it in-process would not reproduce a clean interpreter.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Every module the CLI may legitimately reach. runtime_calibration is included on purpose:
# it stores per-machine timings and used to import QSettings at module scope.
CORE_MODULES = [
    "app.constants",
    "app.core.benchmark_datasets",
    "app.core.config_models",
    "app.core.geo_metadata",
    "app.core.input_detection",
    "app.core.metadata",
    "app.core.paths",
    "app.core.ppi_graph",
    "app.core.project",
    "app.core.provenance",
    "app.core.readiness",
    "app.core.reference_manager",
    "app.core.resources",
    "app.core.runtime_calibration",
    "app.core.runtime_estimator",
    "app.core.sanity_checks",
    "app.core.setup_installer",
    "app.core.snakemake_runner",
    "app.core.sra_metadata",
    "app.benchmark_cli",
]

_PROBE = textwrap.dedent(
    """
    import importlib, sys

    sys.path.insert(0, {repo!r})


    class _NoQt:
        # Refuse PySide6 the way a machine without it would.
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] == "PySide6":
                raise ImportError("PySide6 is not installed")
            return None


    sys.meta_path.insert(0, _NoQt())

    failed = []
    for mod in {modules!r}:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            failed.append(f"{{mod}}: {{type(exc).__name__}}: {{exc}}")

    print("FAILED=" + "|".join(failed))
    """
)


def test_core_layer_imports_without_qt() -> None:
    code = _PROBE.format(repo=str(REPO), modules=CORE_MODULES)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=180, cwd=str(REPO))
    assert result.returncode == 0, result.stderr[-2000:]
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("FAILED="))
    failed = [f for f in line[len("FAILED="):].split("|") if f]
    assert not failed, (
        "these app.core modules cannot be imported without Qt, so the CLI would not run "
        "on a cluster node:\n  " + "\n  ".join(failed)
    )


def test_runtime_calibration_round_trips_without_qt(tmp_path) -> None:
    # The Qt-free fallback must actually persist, not silently drop samples — an estimate
    # that never calibrates is a slow regression nobody notices.
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(REPO)!r})


        class _NoQt:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "PySide6":
                    raise ImportError("PySide6 is not installed")
                return None


        sys.meta_path.insert(0, _NoQt())

        import os
        os.environ["LOCALAPPDATA"] = {str(tmp_path)!r}
        os.environ["XDG_DATA_HOME"] = {str(tmp_path)!r}

        from app.core import runtime_calibration as rc

        assert rc.calibration_factor(8) == (1.0, 0), "uncalibrated machine must report 1.0"
        rc.record_run(8, predicted_raw_compute_min=10.0, actual_wall_min=20.0)
        rc.record_run(8, predicted_raw_compute_min=10.0, actual_wall_min=20.0)
        factor, n = rc.calibration_factor(8)
        assert n == 2, n
        assert abs(factor - 2.0) < 1e-9, factor
        print("ROUNDTRIP_OK")
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=120, cwd=str(REPO))
    assert "ROUNDTRIP_OK" in result.stdout, (result.stdout + result.stderr)[-2000:]
