"""Gates on the shared bash/Rscript capability probes in tests/_runtime.py.

A probe that resolves nothing is indistinguishable from a host that has nothing: every
test built on it would skip, report green, and exercise no pipeline code. So one gate
asserts the probe resolves what this host independently advertises, and the others assert
that when the capability is gone every caller skips rather than errors.
"""

from __future__ import annotations

import ast
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import _runtime
from _runtime import bash_runtime, reset_probe_cache, rscript_runtime
from app.core.paths import windows_to_wsl_path


# Asks the host directly, through a shell rather than through the code under test, so a
# probe stubbed to return None fails this gate instead of silently skipping it. The script
# goes to a file because wsl.exe relays its arguments through a shell of its own, which
# mangles a quoted one-liner.
_DIRECT_PROBE = """
prefixes="$HOME/micromamba /root/micromamba $HOME/.local/share/mamba"
for p in $(command -v Rscript) $(for m in $prefixes; do echo "$m/envs/bulkseq/bin/Rscript"; done)
do
  [ -x "$p" ] || continue
  "$p" --vanilla -e 'quit(status = if (requireNamespace("jsonlite", quietly = TRUE)) 0 else 1)'
  [ $? -eq 0 ] && exit 0
done
exit 1
"""


def _host_advertises_r_with_jsonlite(tmp_path: Path) -> bool:
    script = tmp_path / "direct_probe.sh"
    script.write_text(_DIRECT_PROBE, encoding="utf-8", newline="\n")
    if sys.platform.startswith("win"):
        wsl = shutil.which("wsl")
        command = [wsl, "bash", windows_to_wsl_path(script)] if wsl else None
    else:
        bash = shutil.which("bash")
        command = [bash, str(script)] if bash else None
    if command is None:
        return False
    try:
        return subprocess.run(command, capture_output=True, timeout=180).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _callers(tmp_path: Path):
    """Every test helper that resolves a runtime through the shared probe."""
    harness = tmp_path / "harness.R"
    harness.write_text("quit(status = 0)\n", encoding="utf-8")
    modules = {
        name: importlib.import_module(name)
        for name in (
            "test_custom_enrichment",
            "test_de_common",
            "test_de_contrasts",
            "test_enrichment_eligibility",
            "test_enrichment_mapping",
            "test_external_results_safety",
            "test_figure_rendering_contracts",
            "test_kegg_identity_catalogue",
            "test_microarray_probe_mapping",
            "test_meta_enrichment_threshold",
            "test_per_sample_strandedness",
            "test_sample_labels",
            "test_ppi_mapping_case",
            "test_reclaim_run_space",
            "test_setup_bootstrap",
            "test_setup_installer",
        )
    }
    return [
        ("test_custom_enrichment", lambda: modules["test_custom_enrichment"]._r_runtime(harness)),
        ("test_de_contrasts", lambda: modules["test_de_contrasts"]._r_runtime(harness)),
        ("test_enrichment_eligibility",
         lambda: modules["test_enrichment_eligibility"]._r_runtime(harness)),
        ("test_enrichment_mapping", lambda: modules["test_enrichment_mapping"]._r_runtime(harness)),
        ("test_ppi_mapping_case", lambda: modules["test_ppi_mapping_case"]._r_runtime(harness)),
        ("test_external_results_safety",
         lambda: modules["test_external_results_safety"]._r_runtime(harness)),
        ("test_figure_rendering_contracts",
         lambda: modules["test_figure_rendering_contracts"]._r_runtime()),
        ("test_kegg_identity_catalogue",
         lambda: modules["test_kegg_identity_catalogue"]._r_runtime(harness)),
        ("test_microarray_probe_mapping",
         lambda: modules["test_microarray_probe_mapping"]._r_runtime(harness)),
        ("test_meta_enrichment_threshold",
         lambda: modules["test_meta_enrichment_threshold"]._r_runtime(harness)),
        ("test_external_results_safety._wsl_bulkseq_snakemake",
         lambda: modules["test_external_results_safety"]._wsl_bulkseq_snakemake(tmp_path)),
        ("test_per_sample_strandedness",
         lambda: modules["test_per_sample_strandedness"]._run_read_length_shell([], tmp_path)),
        ("test_de_common", lambda: modules["test_de_common"]._bash_or_skip()),
        ("test_sample_labels", lambda: modules["test_sample_labels"]._bash_or_skip()),
        ("test_reclaim_run_space", lambda: modules["test_reclaim_run_space"]._bash_or_skip()),
        ("test_setup_bootstrap", lambda: modules["test_setup_bootstrap"]._bash_or_skip()),
        ("test_setup_installer", lambda: modules["test_setup_installer"]._bash_or_skip()),
    ]


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    reset_probe_cache()
    yield
    reset_probe_cache()


def test_probe_resolves_the_runtime_this_host_advertises(tmp_path) -> None:
    if not _host_advertises_r_with_jsonlite(tmp_path):
        pytest.skip("no R that loads jsonlite on this host (probed directly, not via _runtime)")
    runtime = rscript_runtime("jsonlite")
    assert runtime is not None
    command, convert = runtime
    assert command[-1] == "--vanilla"
    # The same host ran the direct probe through a shell, so bash is reachable too.
    assert bash_runtime() is not None
    # Exercise the runtime the way every caller does: a harness file, reached through the
    # translation the probe handed back.
    harness = tmp_path / "resolved.R"
    harness.write_text(
        'cat(requireNamespace("jsonlite", quietly = TRUE))\n', encoding="utf-8", newline="\n"
    )
    result = subprocess.run(
        [*command, convert(harness)], capture_output=True, text=True, timeout=180, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "TRUE"


def test_every_caller_skips_when_no_runtime_is_live(monkeypatch, tmp_path) -> None:
    """A Windows host with wsl.exe but no distribution, and no native tool either."""
    monkeypatch.delenv("BULKSEQ_REQUIRE_R", raising=False)
    monkeypatch.delenv("BULKSEQ_REQUIRE_R_FULL", raising=False)

    def refuse(command, *args, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(_runtime.subprocess, "run", refuse)
    # _runtime.shutil is the shutil module every caller sees, so this also removes the
    # native tools their own fallbacks look for.
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: None)
    reset_probe_cache()

    assert bash_runtime() is None
    assert rscript_runtime() is None
    skipped = []
    callers = _callers(tmp_path)
    for name, call in callers:
        try:
            call()
        except pytest.skip.Exception:
            skipped.append(name)
            continue
        pytest.fail(f"{name} did not skip without a runtime")
    # Every helper in the list was reached: a loop that silently iterated none of them
    # would report the same pass.
    assert skipped == [name for name, _ in callers]


def test_every_r_caller_skips_when_the_packages_are_missing(monkeypatch, tmp_path) -> None:
    """R starts but loads none of the packages asked for: still a skip, never an error."""
    monkeypatch.delenv("BULKSEQ_REQUIRE_R", raising=False)
    monkeypatch.delenv("BULKSEQ_REQUIRE_R_FULL", raising=False)
    monkeypatch.setattr(_runtime, "_runs_with", lambda command, packages: False)
    reset_probe_cache()

    assert rscript_runtime("jsonlite") is None
    for name, call in _callers(tmp_path):
        # Both of these resolve bash, not an R package set, so a package-less R is not their
        # skip condition; the no-runtime gate above is what covers them.
        if name.endswith("_wsl_bulkseq_snakemake") or name in {
                "test_per_sample_strandedness", "test_de_common", "test_sample_labels",
                "test_reclaim_run_space", "test_setup_bootstrap", "test_setup_installer"}:
            continue
        try:
            call()
        except pytest.skip.Exception:
            continue
        pytest.fail(f"{name} did not skip without the R packages it needs")


def test_the_caller_list_covers_every_module_built_on_the_probe(tmp_path) -> None:
    """The two gates above are only worth their pass if they reach every caller."""
    here = Path(__file__)
    users = {
        path.stem
        for path in here.parent.glob("test_*.py")
        if path != here and "from _runtime import" in path.read_text(encoding="utf-8")
    }
    assert users == {name.split(".")[0] for name, _ in _callers(tmp_path)}


def _ad_hoc_wsl_probes() -> list[str]:
    """Test modules that decide a Linux runtime exists without going through the probe.

    Two separate modules shipped with `shutil.which("wsl")` as their availability check and
    `["wsl", "bash", ...]` as their command. Windows installs wsl.exe whether or not a
    distribution is present -- the hosted runner is exactly that host -- so both ran the
    harness and failed instead of skipping, and neither local suite could reproduce it.
    _runtime.bash_runtime() exists to answer this question by running something.
    """
    here = Path(__file__)
    offenders: list[str] = []
    for path in sorted(here.parent.glob("test_*.py")):
        if path == here:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # shutil.which("wsl") as an availability test
            if (isinstance(func, ast.Attribute) and func.attr == "which"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "wsl"):
                offenders.append(f"{path.name}: shutil.which(\"wsl\")")
            # subprocess.run/Popen(["wsl", ...]) as the command itself
            if (isinstance(func, ast.Attribute) and func.attr in {"run", "Popen"}
                    and node.args and isinstance(node.args[0], (ast.List, ast.Tuple))
                    and node.args[0].elts
                    and isinstance(node.args[0].elts[0], ast.Constant)
                    and node.args[0].elts[0].value == "wsl"):
                offenders.append(f"{path.name}: subprocess call starting with \"wsl\"")
    return sorted(set(offenders))


def test_no_test_module_probes_for_wsl_on_its_own() -> None:
    offenders = _ad_hoc_wsl_probes()
    assert not offenders, (
        "resolve the runtime through _runtime.bash_runtime() instead; wsl.exe exists on a "
        f"Windows host with no distribution installed: {offenders}"
    )
