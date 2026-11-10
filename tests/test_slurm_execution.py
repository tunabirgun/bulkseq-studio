"""Slurm execution: what `bulkseq run --exec-profile slurm` submits, and what it refuses.

Site settings come from config.yaml's `execution` section, not the copied profile (editing
anything under workflow/ changes the recorded digest and blocks the run). The launch checks
are judged against the PATH the Snakemake child gets, so Windows is refused because it has
no `sbatch`, not by an operating-system branch.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.cli import EXIT_INVALID, main
from app.core.config_models import _SLURM_RESERVED_OPTIONS, ExecutionConfig
from app.core.project import ProjectManager
from app.core.snakemake_runner import (
    SLURM_LOGDIR,
    _snakemake_interpreter,
    build_snakemake_args,
    cluster_default_resources,
    cluster_launch_problems,
)

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / "workflow"
PLUGIN = "snakemake-executor-plugin-slurm"


@pytest.fixture()
def project(tmp_path) -> Path:
    return ProjectManager().create_project("slurmtest", tmp_path)


def _config(project: Path, **execution):
    manager = ProjectManager()
    config = manager.load_config(project)
    for key, value in execution.items():
        setattr(config.execution, key, value)
    manager.save_config(project, config)
    return manager.load_config(project)


def _values(argv: list[str], option: str) -> list[str]:
    values = []
    for token in argv[argv.index(option) + 1:]:
        if token.startswith("--"):
            break
        values.append(token)
    return values


# ---------------------------------------------------------------- argument building

def test_local_arguments_are_unchanged(project) -> None:
    argv = build_snakemake_args(_config(project), "run", project, exec_profile="local")
    for option in ("--profile", "--jobs", "--default-resources", "--slurm-logdir"):
        assert option not in argv
    assert _values(argv, "--resources")[0].startswith("mem_mb=")


def test_slurm_arguments_carry_the_project_execution_settings(project) -> None:
    config = _config(project, slurm_partition="debug", slurm_account="proj_01",
                     slurm_extra="--nice=10 --mail-type=FAIL", jobs=40,
                     default_runtime_min=600, default_mem_mb=6000)
    argv = build_snakemake_args(config, "run", project, exec_profile="slurm")
    assert _values(argv, "--jobs") == ["40"]
    assert _values(argv, "--slurm-logdir") == [SLURM_LOGDIR]
    assert "--slurm-keep-successful-logs" in argv
    assert _values(argv, "--default-resources") == [
        "runtime=600*attempt",
        "mem_mb=max(2*input.size_mb, 6000)",
        "slurm_partition='debug'",
        "slurm_account='proj_01'",
        "slurm_extra='--nice=10 --mail-type=FAIL'",
    ]
    # Exactly one list: a second --default-resources would replace the first.
    assert argv.count("--default-resources") == 1


def test_default_resource_values_are_python_expressions_snakemake_can_evaluate(project) -> None:
    config = _config(project, slurm_partition="a,b", slurm_extra="--nice=5")
    for item in cluster_default_resources(config, "slurm"):
        key, value = item.split("=", 1)
        tree = ast.parse(value, mode="eval")
        if key.startswith("slurm_"):
            # A bare `a,b` would evaluate as a tuple; a quoted literal stays a string.
            assert isinstance(ast.literal_eval(tree), str)


def test_unset_slurm_settings_are_left_to_the_site(project) -> None:
    argv = build_snakemake_args(_config(project), "run", project, exec_profile="slurm")
    assert not [v for v in _values(argv, "--default-resources") if v.startswith("slurm_")]


def test_kubernetes_gets_the_generic_defaults_only(project) -> None:
    argv = build_snakemake_args(_config(project, slurm_partition="debug"), "run", project,
                                exec_profile="kubernetes")
    assert _values(argv, "--default-resources") == ["runtime=240*attempt", "mem_mb=max(2*input.size_mb, 4000)"]
    assert "--slurm-logdir" not in argv


def test_slurm_profile_no_longer_asks_to_be_edited() -> None:
    text = (WORKFLOW / "profiles" / "site" / "slurm" / "config.yaml").read_text(encoding="utf-8")
    profile = yaml.safe_load(text)
    assert not [r for r in profile["default-resources"] if str(r).startswith("slurm_")]
    assert "execution.slurm_partition" in text


# ---------------------------------------------------------------- configuration

@pytest.mark.parametrize("extra", [
    "--partition=x", "--account=y", "--mem=4G", "--time=10", "--cpus-per-task=2", "--output=a",
    "--nice=1 --job-name=y", "--wrap=ls", "--qos=normal", "--constraint=intel", "--comment=x",
    "--gres=gpu:1", "--clusters=c2", "--workdir=/tmp",
])
def test_slurm_extra_refuses_options_the_executor_owns(extra) -> None:
    with pytest.raises(ValidationError, match="may not set"):
        ExecutionConfig(slurm_extra=extra)


@pytest.mark.parametrize("extra", ["nice=1", "-q x", "--nice='1'", "--nice=1;rm", "--nice=$(id)", "--nice=1 b=c"])
def test_slurm_extra_refuses_anything_but_option_tokens(extra) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(slurm_extra=extra)


def test_slurm_extra_accepts_and_normalises_option_tokens() -> None:
    assert ExecutionConfig(slurm_extra="  --nice=10   --mail-type=FAIL --exclusive ").slurm_extra == (
        "--nice=10 --mail-type=FAIL --exclusive")


@pytest.mark.parametrize("name", ["deb ug", "a;b", "x'y", "$(id)"])
def test_partition_and_account_names_are_validated(name) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(slurm_partition=name)
    with pytest.raises(ValidationError):
        ExecutionConfig(slurm_account=name)


@pytest.mark.parametrize("field,value", [("jobs", 0), ("default_runtime_min", 0), ("default_mem_mb", 100)])
def test_execution_limits_are_bounded(field, value) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(**{field: value})


def test_config_set_writes_the_partition(project, capsys) -> None:
    assert main(["config", "set", "-C", str(project), "execution.slurm_partition", "debug"]) == 0
    assert ProjectManager().load_config(project).execution.slurm_partition == "debug"
    assert main(["config", "set", "-C", str(project), "execution.slurm_extra", "--", "--mem=1G"]) == EXIT_INVALID
    assert ProjectManager().load_config(project).execution.slurm_extra == ""
    # A value that starts with `--` needs the `--` separator, as the docs show.
    assert main(["config", "set", "-C", str(project), "execution.slurm_extra", "--", "--nice=10"]) == 0
    assert ProjectManager().load_config(project).execution.slurm_extra == "--nice=10"


def test_reserved_options_cover_everything_the_installed_plugin_refuses() -> None:
    # A value the config accepts but the plugin refuses would fail every job at submission.
    snakemake = shutil.which("snakemake")
    python = _snakemake_interpreter(snakemake, os.environ.get("PATH", "")) if snakemake else None
    if not python:
        pytest.skip("no Snakemake environment on PATH")
    probe = subprocess.run(
        [python, "-c", "from snakemake_executor_plugin_slurm.validation import "
                       "get_forbidden_slurm_options as f; print('\\n'.join(f()))"],
        capture_output=True, text=True, timeout=120)
    if probe.returncode != 0:
        pytest.skip("the Slurm executor plugin is not installed in that environment")
    refused = set(re.findall(r"--[a-z][a-z-]*", probe.stdout))
    assert refused, probe.stdout
    assert not refused - _SLURM_RESERVED_OPTIONS


# ---------------------------------------------------------------- dependencies

def _spec_entries(path: Path) -> list[str]:
    return [str(d) for d in yaml.safe_load(path.read_text(encoding="utf-8"))["dependencies"]
            if isinstance(d, str)]


@pytest.mark.parametrize("spec", ["bulkseq_core.yaml", "bulkseq_full.yaml"])
def test_every_environment_spec_carries_the_slurm_plugin(spec) -> None:
    assert f"{PLUGIN}=2.8.0" in _spec_entries(WORKFLOW / "envs" / spec)


def test_lock_pins_the_plugin_at_the_spec_version_with_its_jobstep() -> None:
    lock = _spec_entries(WORKFLOW / "envs" / "bulkseq.lock.yaml")
    assert f"{PLUGIN}==2.8.0=pyhdfd78af_0" in lock
    assert f"{PLUGIN}-jobstep==0.6.1=pyhdfd78af_0" in lock


def test_setup_script_verifies_the_plugin_import() -> None:
    text = (REPO / "scripts" / "setup_wsl_bioenv.sh").read_text(encoding="utf-8")
    verify = text.split('printf "  %-22s" "Python imports"', 1)[1].split("fi", 1)[0]
    assert "snakemake_executor_plugin_slurm" in verify
    assert f'md.version("{PLUGIN}")' in verify


# ---------------------------------------------------------------- network rules

def _network_rules() -> tuple[str, ...]:
    source = (WORKFLOW / "Snakefile").read_text(encoding="utf-8")
    match = re.search(r"^NETWORK_RULES = (\(.*?\))$", source, re.S | re.M)
    assert match, "NETWORK_RULES is no longer a literal tuple in the Snakefile"
    return ast.literal_eval(match.group(1))


def test_every_network_rule_is_defined_in_the_workflow() -> None:
    defined = set()
    for path in [WORKFLOW / "Snakefile", *WORKFLOW.rglob("*.smk")]:
        defined |= set(re.findall(r"^\s*rule (\w+):", path.read_text(encoding="utf-8"), re.M))
    names = _network_rules()
    assert names and not set(names) - defined


def test_network_rules_are_filtered_and_switchable() -> None:
    source = (WORKFLOW / "Snakefile").read_text(encoding="utf-8")
    assert 'get("network_rules_on_submit_host", True)' in source
    assert "workflow.localrules(*(name for name in NETWORK_RULES if workflow.is_rule(name)))" in source


@pytest.mark.parametrize("on_submit_host", [True, False])
def test_snakefile_parses_in_a_mode_that_lacks_network_rules(tmp_path, on_submit_host) -> None:
    # A count-matrix project defines none of the download, rRNA or GEO rules, so a filter
    # that touched an undefined name would stop every run here, local ones included.
    from test_external_results_safety import _wsl_bulkseq_snakemake

    manager = ProjectManager()
    project = manager.create_project("netrules", tmp_path)
    (project / "config" / "samples.tsv").write_text(
        "sample_id\tcondition\n" + "".join(f"s{i}\t{'a' if i < 3 else 'b'}\n" for i in range(6)),
        encoding="utf-8")
    (project / "config" / "counts.tsv").write_text(
        "gene_id\t" + "\t".join(f"s{i}" for i in range(6)) + "\n"
        + "".join(f"g{g}\t" + "\t".join(str(10 + g + i) for i in range(6)) + "\n" for g in range(20)),
        encoding="utf-8")
    config = manager.load_config(project)
    config.input.type = "count_matrix"
    config.input.count_matrix = "config/counts.tsv"
    config.deseq2.contrasts[0].numerator = "b"
    config.deseq2.contrasts[0].denominator = "a"
    config.deseq2.reference_level = {"condition": "a"}
    config.workflow.enrichment = False
    config.ppi.enabled = False
    config.execution.network_rules_on_submit_host = on_submit_host
    manager.save_config(project, config)

    result = _wsl_bulkseq_snakemake(project, "--dry-run", "--snakefile", "workflow/Snakefile",
                                    "--cores", "1", "--configfile", "config/config.yaml")
    text = result.stdout + result.stderr
    assert result.returncode == 0, text
    assert "not present in the Snakefile" not in text
    assert "deseq2" in text


# ---------------------------------------------------------------- launch checks

def _command(folder: Path, name: str, body: str = "") -> Path:
    # Named the way this platform's own lookup finds an executable: PATHEXT's first suffix
    # where it exists (Windows), no suffix with the executable bit elsewhere.
    suffix = os.environ.get("PATHEXT", "").split(os.pathsep)[0].lower() if os.environ.get("PATHEXT") else ""
    path = folder / f"{name}{suffix}"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture()
def fake_host(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    plugins = tmp_path / "site"
    (plugins / "snakemake_executor_plugin_slurm").mkdir(parents=True)
    (plugins / "snakemake_executor_plugin_slurm" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr("app.core.snakemake_runner._snakemake_interpreter",
                        lambda snakemake, path: sys.executable)

    def env(*commands: str, plugin: bool = True) -> dict[str, str]:
        for name in commands:
            _command(bin_dir, name)
        result = {**os.environ, "PATH": str(bin_dir)}
        result["PYTHONPATH"] = str(plugins) if plugin else ""
        return result

    return env


def test_a_host_without_sbatch_is_refused_with_the_way_forward(project, fake_host) -> None:
    errors, _ = cluster_launch_problems(_config(project), "slurm", "run", fake_host("snakemake"))
    assert len(errors) == 1 and "'sbatch' is not on PATH" in errors[0] and "login node" in errors[0]


def test_a_dry_run_does_not_need_sbatch(project, fake_host) -> None:
    errors, _ = cluster_launch_problems(_config(project), "slurm", "dry-run", fake_host("snakemake"))
    assert errors == []


def test_a_missing_plugin_says_to_repair_the_environment(project, fake_host) -> None:
    errors, _ = cluster_launch_problems(_config(project), "slurm", "run",
                                        fake_host("sbatch", "squeue", "sacct", "snakemake", plugin=False))
    assert len(errors) == 1 and PLUGIN in errors[0] and "Repair the environment" in errors[0]


def test_kubernetes_plugin_is_not_promised_by_an_environment_repair(project, fake_host) -> None:
    errors, _ = cluster_launch_problems(_config(project), "kubernetes", "run",
                                        fake_host("snakemake", plugin=False))
    assert len(errors) == 1
    assert "not part of the BulkSeq environment" in errors[0] and "container image" in errors[0]
    assert "Repair the environment" not in errors[0]


def test_a_missing_snakemake_is_reported(project, fake_host) -> None:
    errors, _ = cluster_launch_problems(_config(project), "slurm", "run", fake_host("sbatch", "squeue", "sacct"))
    assert len(errors) == 1 and "'snakemake' is not on PATH" in errors[0]


def test_a_submit_host_without_squeue_is_refused(project, fake_host) -> None:
    errors, warnings = cluster_launch_problems(_config(project, slurm_partition="debug", slurm_account="a"),
                                               "slurm", "run", fake_host("sbatch", "snakemake"))
    assert len(errors) == 1 and "'squeue' is not on PATH" in errors[0]
    assert any("sacct is not on PATH" in w for w in warnings)


def test_a_ready_host_passes_and_only_empty_site_settings_warn(project, fake_host) -> None:
    env = fake_host("sbatch", "squeue", "sacct", "snakemake")
    errors, warnings = cluster_launch_problems(_config(project), "slurm", "run", env)
    assert errors == [] and len(warnings) == 2
    assert any("slurm_partition" in w for w in warnings) and any("slurm_account" in w for w in warnings)
    errors, warnings = cluster_launch_problems(
        _config(project, slurm_partition="debug", slurm_account="acct"), "slurm", "run", env)
    assert errors == [] and warnings == []
    # Plugin 2.8.0 ignores --slurm-no-account, so the empty-account warning stays.
    errors, warnings = cluster_launch_problems(
        _config(project, slurm_partition="debug", slurm_account="", slurm_no_account=True), "slurm", "run", env)
    assert errors == [] and len(warnings) == 1 and "slurm_account" in warnings[0]


def test_local_runs_are_never_checked(project) -> None:
    assert cluster_launch_problems(_config(project), "local", "run", {"PATH": ""}) == ([], [])


def test_cli_refuses_before_touching_the_project(project, monkeypatch, tmp_path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("MAMBA_ROOT_PREFIX", str(tmp_path / "no-mamba"))
    synced = []
    monkeypatch.setattr(ProjectManager, "sync_workflow_if_outdated", lambda self, root: synced.append(root))
    assert main(["run", "-C", str(project), "--exec-profile", "slurm"]) == EXIT_INVALID
    err = capsys.readouterr().err
    assert "'sbatch' is not on PATH" in err and "login node" in err
    assert not synced


def test_interpreter_comes_from_the_shebang(tmp_path) -> None:
    # A shebang cannot contain a space (the kernel splits there), so the path has none.
    python = tmp_path / "env" / "python3.12"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    script = tmp_path / "snakemake"
    script.write_text(f"#!{python.as_posix()}\nimport snakemake\n", encoding="utf-8")
    assert Path(_snakemake_interpreter(str(script), "")) == python


def test_interpreter_falls_back_to_the_python_beside_a_long_prefix_launcher(tmp_path) -> None:
    # conda rewrites a shebang longer than the kernel limit to /bin/sh + an exec line.
    script = tmp_path / "snakemake"
    script.write_text("#!/bin/sh\n'''exec' \"/very/long/prefix/bin/python\" \"$0\" \"$@\"\n", encoding="utf-8")
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    assert _snakemake_interpreter(str(script), "") == str(python)
