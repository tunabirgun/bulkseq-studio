"""Command-line interface: behaviour, and equivalence with the GUI.

The equivalence test is the important one. Two front ends that build their own Snakemake
argument vectors will drift, and the drift shows up as results that differ between the GUI
and the CLI for the same project — the worst possible failure for a scientific tool. Both
call app.core.snakemake_runner.build_snakemake_command(), and this asserts it stays that way.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.cli import EXIT_GATE, EXIT_INVALID, EXIT_OK, main
from app.cli_banner import banner_text, should_show_banner
from app.constants import APP_VERSION, WORKFLOW_VERSION
from app.core.project import ProjectManager
from app.core.snakemake_runner import build_snakemake_args, build_snakemake_command

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def project(tmp_path) -> Path:
    # A reference and an organism as a real project records them, so `bulkseq check` reports
    # only what each test sets up (it shares the Start gate's route and enrichment findings).
    manager = ProjectManager()
    root = manager.create_project("clitest", tmp_path)
    config = manager.load_config(root)
    config.reference.genome_fasta_url = "https://example.org/genome.fa.gz"
    config.reference.annotation_gtf_url = "https://example.org/annotation.gtf.gz"
    config.enrichment.kegg_organism = "dme"
    manager.save_config(root, config)
    return root


def _configure_samples(project: Path, path: Path, input_type: str = "sra") -> None:
    config = ProjectManager().load_config(project)
    config.input.type = input_type
    config.input.samples = str(path)
    ProjectManager().save_config(project, config)


def _pending_reads_sheet(*sample_ids: str) -> str:
    rows = ["sample_id\tcondition\tlayout\tfastq_1"]
    rows.extend(
        f"{sample_id}\t{'control' if index == 0 else 'treated'}\tsingle\t"
        f"data/raw/{sample_id}.fastq.gz"
        for index, sample_id in enumerate(sample_ids)
    )
    return "\n".join(rows) + "\n"


# ---- the banner ---------------------------------------------------------------

def test_banner_is_pure_ascii() -> None:
    # It can print before stream encoding is reconfigured; a non-ASCII glyph on a cp1252
    # console raises UnicodeEncodeError and takes the program down.
    text = banner_text(APP_VERSION, "subtitle")
    text.encode("ascii")
    text.encode("cp1252")
    assert not [c for c in text if ord(c) > 127]


def test_banner_carries_the_version() -> None:
    assert APP_VERSION in banner_text(APP_VERSION)


class _FakeStream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.mark.parametrize(
    ("tty", "json_output", "quiet", "expected"),
    [
        (True, False, False, True),    # interactive terminal: show it
        (False, False, False, False),  # redirected to a file: never decorate a log
        (True, True, False, False),    # --json must keep stdout/stderr machine-readable
        (True, False, True, False),    # --quiet
    ],
)
def test_banner_suppression(tty, json_output, quiet, expected, monkeypatch) -> None:
    monkeypatch.delenv("BULKSEQ_NO_BANNER", raising=False)
    assert should_show_banner(json_output=json_output, quiet=quiet,
                              stream=_FakeStream(tty)) is expected


def test_banner_env_escape_hatch(monkeypatch) -> None:
    monkeypatch.setenv("BULKSEQ_NO_BANNER", "1")
    assert should_show_banner(stream=_FakeStream(True)) is False


# ---- GUI/CLI equivalence ------------------------------------------------------

@pytest.mark.parametrize("mode", ["run", "dry-run", "resume", "unlock", "figures"])
def test_cli_prints_the_same_command_the_gui_builds(project, mode, capsys) -> None:
    config = ProjectManager().load_config(project)
    expected = build_snakemake_command(
        project, config, mode, use_wsl=sys.platform.startswith("win")).display

    assert main(["print-command", "-C", str(project), "--mode", mode]) == EXIT_OK
    printed = capsys.readouterr().out.strip()
    assert printed == expected.strip(), (
        "the CLI and the GUI would launch different Snakemake commands; both must go "
        "through build_snakemake_command()"
    )


def test_local_profile_keeps_the_machine_sized_pools(project) -> None:
    config = ProjectManager().load_config(project)
    argv = build_snakemake_args(config, "run", project, exec_profile="local")
    assert "--cores" in argv
    assert any(a.startswith("mem_mb=") for a in argv), argv


@pytest.mark.parametrize("exec_profile", ["slurm", "kubernetes"])
def test_cluster_profiles_never_pass_a_global_memory_cap(project, exec_profile) -> None:
    # The important one. Snakemake CLAMPS a rule's resource request down to a global pool
    # rather than refusing, and under a cluster executor that clamped number becomes the
    # scheduler's allocation. star_align declares 24 GB; a default project's 8 GB pool
    # would have it submitted with 8 GB and OOM-killed hours into the run.
    config = ProjectManager().load_config(project)
    argv = build_snakemake_args(config, "run", project, exec_profile=exec_profile)
    assert not [a for a in argv if a.startswith("mem_mb=")], (
        f"{exec_profile} passes a global mem_mb pool; it would silently shrink every "
        f"rule's scheduler allocation"
    )
    assert "--cores" not in argv, (
        f"{exec_profile} passes --cores; the per-rule thread ceiling belongs in the profile"
    )
    assert "--profile" in argv
    assert f"site/{exec_profile}" in " ".join(argv)


@pytest.mark.parametrize("exec_profile", ["slurm", "kubernetes"])
def test_cluster_profile_path_stays_relative(project, exec_profile) -> None:
    # Snakemake resolves --profile against the launching process's cwd, and the runner sets
    # cwd=project_root. Keeping it relative is also what keeps an absolute Windows path out
    # of the `bash -lc` string, so one code path serves both platforms.
    config = ProjectManager().load_config(project)
    argv = build_snakemake_args(config, "run", project, exec_profile=exec_profile)
    value = argv[argv.index("--profile") + 1]
    assert not Path(value).is_absolute(), value
    assert value.startswith("workflow/profiles/site/")


def test_cluster_runs_do_not_go_through_wsl(project, capsys) -> None:
    # A cluster executor submits from wherever the CLI runs; wrapping it in `wsl -- bash`
    # would submit from inside the VM instead.
    assert main(["print-command", "-C", str(project), "--exec-profile", "slurm"]) == EXIT_OK
    assert "wsl --" not in capsys.readouterr().out


# ---- `bulkseq run` -------------------------------------------------------------

def test_run_builds_the_same_argv_as_print_command(project, monkeypatch) -> None:
    # run must go through the identical builder print-command uses, not a second
    # hand-assembled argv that could drift from it. run_snakemake_sync mints its own
    # random run tag (for Ctrl-C cleanup of the whole WSL process tree), so the tag itself
    # is excluded from the comparison rather than the whole WSL-wrapped string.
    import re

    from app.core import snakemake_runner

    config = ProjectManager().load_config(project)
    expected = build_snakemake_command(project, config, "run",
                                       use_wsl=sys.platform.startswith("win")).command

    captured = {}

    class _FakeProcess:
        stdout = iter(["dummy line\n"])

        def wait(self):
            return 0

    def _fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return _FakeProcess()

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen", _fake_popen)
    from app.cli import EXIT_OK as _OK
    assert main(["run", "-C", str(project)]) == _OK
    tag_re = re.compile(r"export BULKSEQ_RUN_TAG_[0-9a-f]+=1 && ")
    actual = [tag_re.sub("", part) for part in captured["argv"]]
    assert actual == expected


def test_run_does_not_start_runner_after_workflow_sync_failure(project, monkeypatch, capsys) -> None:
    import app.cli as cli_module

    started: list[bool] = []
    monkeypatch.setattr(
        ProjectManager,
        "sync_workflow_if_outdated",
        lambda self, root: (_ for _ in ()).throw(RuntimeError("project workflow changed since it was copied")),
    )
    monkeypatch.setattr(cli_module, "run_snakemake_sync", lambda *args, **kwargs: started.append(True) or 0)

    assert main(["run", "-C", str(project), "--quiet"]) == EXIT_INVALID
    assert started == []
    assert "Could not refresh project workflow scripts" in capsys.readouterr().err


@pytest.mark.parametrize(("statuses", "overall", "code"), [
    (["WARNING", "REVIEW_REQUIRED"], "REVIEW_REQUIRED", EXIT_OK),
    (["REVIEW_REQUIRED", "WARNING"], "REVIEW_REQUIRED", EXIT_OK),
    (["PASS", "WARNING"], "WARNING", EXIT_OK),
    (["WARNING", "FAIL", "REVIEW_REQUIRED"], "FAIL", EXIT_GATE),
])
def test_check_reports_the_most_severe_finding_in_any_order(project, monkeypatch, capsys,
                                                             statuses, overall, code) -> None:
    # A WARNING listed before a REVIEW_REQUIRED once left the overall status at WARNING.
    messages = [{"status": status, "message": f"finding {n}"} for n, status in enumerate(statuses)]
    monkeypatch.setattr("app.core.preflight_checks.validate_metadata", lambda *args, **kwargs: messages)
    assert main(["check", "-C", str(project), "--json"]) == code
    assert json.loads(capsys.readouterr().out)["status"] == overall


@pytest.mark.parametrize("text", ["input:\n  type: [unclosed\n", "deseq2:\n  alpha: not-a-number\n"])
@pytest.mark.parametrize("command", [["project", "info"], ["config", "show"], ["check"]])
def test_a_corrupt_config_is_an_invalid_project_not_a_crash(project, capsys, text, command) -> None:
    (project / "config" / "config.yaml").write_text(text, encoding="utf-8")
    assert main([*command, "-C", str(project), "--quiet"]) == EXIT_INVALID
    err = capsys.readouterr().err
    assert "config/config.yaml" in err and "Traceback" not in err


def test_help_names_every_exit_status(capsys) -> None:
    import app.cli as cli
    # Derived from the module's own constants, so a new code added without documentation fails.
    defined = {value for name, value in vars(cli).items() if name.startswith("EXIT_") and isinstance(value, int)}
    with pytest.raises(SystemExit):
        main(["--help"])
    listed = {int(line.split()[0]) for line in capsys.readouterr().out.split("exit status:", 1)[1].strip().splitlines()}
    assert listed == defined | {1}


def test_run_detects_a_masked_failure_marker(monkeypatch, project) -> None:
    # `micromamba run` under WSL returns exit 0 even when snakemake failed; a marker line
    # in the output must still fail the CLI run.
    from app.core import snakemake_runner
    from app.cli import EXIT_RUN_FAILED

    class _FakeProcess:
        stdout = iter(["some progress\n", "Error in rule star_align:\n", "more output\n"])

        def wait(self):
            return 0

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen",
                        lambda argv, **kwargs: _FakeProcess())
    assert main(["run", "-C", str(project)]) == EXIT_RUN_FAILED


def test_run_succeeds_when_no_marker_and_exit_code_is_zero(monkeypatch, project) -> None:
    from app.core import snakemake_runner

    class _FakeProcess:
        stdout = iter(["Finished job 0.\n"])

        def wait(self):
            return 0

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen",
                        lambda argv, **kwargs: _FakeProcess())
    assert main(["run", "-C", str(project)]) == EXIT_OK


def test_run_drains_the_output_queued_while_the_last_line_was_printed(monkeypatch, project) -> None:
    # The reader thread finishes the stream while the consumer is still inside emit() for an
    # earlier line, so the tail sits in the queue when the consumer loop ends -- and the tail is
    # where "Error in rule" appears. Without the post-wait drain those lines are never scanned
    # and a failed run reports success.
    import time

    from app.core import snakemake_runner

    class _FakeProcess:
        stdout = iter(["some progress\n", "more output\n", "Error in rule star_align:\n"])

        def wait(self):
            return 0

    seen: list[str] = []

    def slow_first_line(line):
        seen.append(line)
        if len(seen) == 1:
            time.sleep(0.4)  # long enough for the reader to queue the rest and exit

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen", lambda argv, **kwargs: _FakeProcess())
    config = ProjectManager().load_config(project)
    assert snakemake_runner.run_snakemake_sync(project, config, "run", on_line=slow_first_line) == 1
    assert seen == ["some progress", "more output", "Error in rule star_align:"]


def test_run_takes_a_ctrl_c_while_the_pipe_is_quiet(monkeypatch, project) -> None:
    # The interrupt must land while nothing is being printed -- a real run is quiet for hours
    # while an aligner works. A pipe read in the consumer thread swallows it until the next
    # line arrives (verified: interrupt_main() is not observed inside readline() on a pipe),
    # so the consumer must be waiting on the queue, not on the pipe.
    import _thread
    import os
    import threading
    import time

    from app.core import snakemake_runner

    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "r", encoding="utf-8")
    writer = os.fdopen(write_fd, "w", encoding="utf-8")
    writer.write("Building DAG of jobs...\n")
    writer.flush()

    class _FakeProcess:
        stdout = stream

        def wait(self):  # pragma: no cover - the interrupt lands first
            return 0

    stopped = []

    def fake_stop(self):  # the real one kills the tree, which is what closes the pipe
        stopped.append(True)
        writer.close()

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen", lambda argv, **kwargs: _FakeProcess())
    monkeypatch.setattr(snakemake_runner.SnakemakeRunner, "stop", fake_stop)
    config = ProjectManager().load_config(project)

    interrupt = threading.Timer(0.5, _thread.interrupt_main)
    # Bounds a regression: without it a consumer blocked on the pipe would hang the suite.
    watchdog = threading.Timer(5.0, writer.close)
    interrupt.start()
    watchdog.start()
    started = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            snakemake_runner.run_snakemake_sync(project, config, "run", on_line=lambda line: None)
        elapsed = time.monotonic() - started
    finally:
        interrupt.cancel()
        watchdog.cancel()
        for handle in (writer, stream):
            try:
                handle.close()
            except OSError:
                pass
    assert stopped == [True], "the interrupt must stop the whole process tree, not just unwind"
    assert elapsed < 2.0, f"the interrupt took {elapsed:.1f}s to land; it was swallowed by the read"


def test_run_resyncs_a_stale_project_workflow_before_running(monkeypatch, project, capsys) -> None:
    # Mirrors the GUI's pre-run re-sync: a CLI run must not execute a project's stale
    # workflow/ copy just because it never goes through main_window.py's launch path.
    from app.core import snakemake_runner

    meta = project / "workflow" / "workflow_metadata.yaml"
    meta.write_text("workflow_version: 0.1.0\nworkflow_digest: 0\ncopied_at: '2020-01-01T00:00:00'\n",
                    encoding="utf-8")

    class _FakeProcess:
        stdout = iter(["Nothing to be done.\n"])

        def wait(self):
            return 0

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen", lambda argv, **kwargs: _FakeProcess())
    assert main(["run", "--mode", "dry-run", "-C", str(project)]) == EXIT_OK

    err = capsys.readouterr().err
    assert f"Updated project workflow scripts to match this app version ({WORKFLOW_VERSION})." in err
    assert f"workflow_version: {WORKFLOW_VERSION}" in meta.read_text(encoding="utf-8")


def test_run_leaves_a_current_project_workflow_alone(monkeypatch, project, capsys) -> None:
    from app.core import snakemake_runner

    meta = project / "workflow" / "workflow_metadata.yaml"
    before = meta.read_text(encoding="utf-8")  # created_project already stamped at WORKFLOW_VERSION

    class _FakeProcess:
        stdout = iter(["Nothing to be done.\n"])

        def wait(self):
            return 0

    monkeypatch.setattr(snakemake_runner.subprocess, "Popen", lambda argv, **kwargs: _FakeProcess())
    assert main(["run", "--mode", "dry-run", "-C", str(project)]) == EXIT_OK

    err = capsys.readouterr().err
    assert "Updated project workflow scripts" not in err
    assert meta.read_text(encoding="utf-8") == before


def test_cli_does_not_assemble_snakemake_flags_itself() -> None:
    # A regression guard with teeth: if someone hand-writes "--cores" or "--resources"
    # into the CLI, equivalence is gone the moment the GUI's builder changes.
    source = (REPO / "app" / "cli.py").read_text(encoding="utf-8")
    for flag in ('"--cores"', '"--resources"', '"--snakefile"', '"--configfile"'):
        assert flag not in source, f"app/cli.py hand-assembles {flag}; call the builder instead"


# ---- config editing -----------------------------------------------------------

def test_config_set_round_trips(project) -> None:
    assert main(["config", "set", "deseq2.alpha", "0.01", "-C", str(project)]) == EXIT_OK
    assert ProjectManager().load_config(project).deseq2.alpha == pytest.approx(0.01)


def test_config_set_rejects_an_out_of_range_value_without_writing(project) -> None:
    before = ProjectManager().load_config(project).deseq2.alpha
    assert main(["config", "set", "deseq2.alpha", "1.5", "-C", str(project)]) == EXIT_INVALID
    # The file must be untouched: a rejected edit that half-applied would be worse than
    # no validation at all.
    assert ProjectManager().load_config(project).deseq2.alpha == before


def test_config_set_rejects_an_unknown_key(project) -> None:
    assert main(["config", "set", "deseq.alpha", "0.01", "-C", str(project)]) == EXIT_INVALID


def test_config_set_parses_booleans(project) -> None:
    assert main(["config", "set", "workflow.figures", "false", "-C", str(project)]) == EXIT_OK
    assert ProjectManager().load_config(project).workflow.figures is False


# ---- project surface ----------------------------------------------------------

def test_project_info_json_is_parseable(project, capsys) -> None:
    assert main(["project", "info", "-C", str(project), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_name"] == "clitest"
    assert Path(payload["project_root"]) == project


def test_cli_uses_configured_custom_sheet_for_info_show_and_check(project, capsys) -> None:
    configured = project / "config" / "sheets" / "study.tsv"
    configured.parent.mkdir()
    configured.write_text(_pending_reads_sheet("configured_a", "configured_b"), encoding="utf-8")
    # A hardcoded config/samples.tsv must not be used as a quiet fallback for any CLI command.
    (project / "config" / "samples.tsv").write_text("wrong\nvalue\n", encoding="utf-8")
    _configure_samples(project, Path("config/sheets/study.tsv"))

    assert main(["project", "info", "-C", str(project), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["samples"] == 2

    assert main(["samples", "show", "-C", str(project)]) == EXIT_OK
    shown = capsys.readouterr().out
    assert "configured_a" in shown
    assert "wrong" not in shown

    assert main(["check", "-C", str(project)]) == EXIT_OK
    assert "FASTQ R1 does not exist" not in capsys.readouterr().out


def test_cli_uses_an_absolute_configured_sample_sheet(project, capsys) -> None:
    configured = project.parent / "absolute-study.tsv"
    configured.write_text(_pending_reads_sheet("absolute_a", "absolute_b"), encoding="utf-8")
    (project / "config" / "samples.tsv").write_text("wrong\nvalue\n", encoding="utf-8")
    _configure_samples(project, configured)

    assert main(["project", "info", "-C", str(project), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["samples"] == 2
    assert main(["samples", "show", "-C", str(project)]) == EXIT_OK
    assert "absolute_a" in capsys.readouterr().out
    assert main(["check", "-C", str(project)]) == EXIT_OK


def test_cli_reports_missing_configured_sample_sheet(project, capsys) -> None:
    missing = project / "config" / "sheets" / "missing.tsv"
    _configure_samples(project, Path("config/sheets/missing.tsv"))

    assert main(["project", "info", "-C", str(project), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["samples"] == 0
    assert main(["samples", "show", "-C", str(project)]) == EXIT_INVALID
    assert str(missing) in capsys.readouterr().err
    assert main(["check", "-C", str(project)]) == EXIT_GATE
    assert str(missing) in capsys.readouterr().err


def test_cli_reports_an_unparseable_configured_sample_sheet(project, capsys) -> None:
    configured = project / "config" / "sheets" / "broken.tsv"
    configured.parent.mkdir()
    configured.write_text("sample_id\tcondition\n\"unterminated\tcontrol\n", encoding="utf-8")
    _configure_samples(project, Path("config/sheets/broken.tsv"))

    assert main(["samples", "show", "-C", str(project)]) == EXIT_INVALID
    assert str(configured) in capsys.readouterr().err
    assert main(["check", "-C", str(project)]) == EXIT_GATE
    assert str(configured) in capsys.readouterr().err


def test_cli_check_requires_local_fastqs_for_a_fastq_project(project, capsys) -> None:
    samples = project / "config" / "samples.tsv"
    samples.write_text(_pending_reads_sheet("local_a", "local_b"), encoding="utf-8")
    # InputConfig supplies config/sra_accessions.txt by default. That configured string does
    # not make a local FASTQ project an SRA-download route.
    assert main(["check", "-C", str(project)]) == EXIT_GATE
    assert "FASTQ R1 does not exist" in capsys.readouterr().out


def test_cli_check_resolves_project_relative_fastqs_from_another_directory(project, tmp_path, monkeypatch) -> None:
    raw = project / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "local_a.fastq.gz").write_bytes(b"synthetic-a")
    (raw / "local_b.fastq.gz").write_bytes(b"synthetic-b")
    samples = project / "config" / "samples.tsv"
    samples.write_text(_pending_reads_sheet("local_a", "local_b"), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert main(["check", "-C", str(project)]) == EXIT_OK
    assert "data/raw/local_a.fastq.gz" in samples.read_text(encoding="utf-8")


@pytest.mark.parametrize("input_type", ["sra", "count_matrix", "microarray", "deseq2_results"])
def test_cli_check_allows_pending_reads_for_nonlocal_input_routes(project, capsys, input_type) -> None:
    samples = project / "config" / "samples.tsv"
    samples.write_text(_pending_reads_sheet("pending_a", "pending_b"), encoding="utf-8")
    _configure_samples(project, Path("config/samples.tsv"), input_type)
    if input_type == "count_matrix":
        config = ProjectManager().load_config(project)
        (project / "config" / "counts.tsv").write_text("gene_id\tpending_a\tpending_b\n", encoding="utf-8")
        config.input.count_matrix = "config/counts.tsv"
        ProjectManager().save_config(project, config)

    code = main(["check", "-C", str(project)])
    out = capsys.readouterr().out
    assert "FASTQ R1 does not exist" not in out
    # Imported results are judged by their own provenance, which this sheet does not provide.
    assert code == (EXIT_GATE if input_type == "deseq2_results" else EXIT_OK), out


def test_commands_refuse_a_directory_that_is_not_a_project(tmp_path) -> None:
    assert main(["project", "info", "-C", str(tmp_path)]) == EXIT_INVALID


def test_project_create_refuses_to_overwrite(tmp_path) -> None:
    assert main(["project", "create", "--name", "p", "--workdir", str(tmp_path)]) == EXIT_OK
    assert main(["project", "create", "--name", "p", "--workdir", str(tmp_path)]) == EXIT_INVALID


def test_project_create_names_an_occupied_non_project_target(tmp_path, capsys) -> None:
    target = tmp_path / "occupied"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_bytes(b"keep")

    assert main(["project", "create", "--name", "occupied", "--workdir", str(tmp_path)]) == EXIT_INVALID
    assert "Project destination is occupied" in capsys.readouterr().err
    assert marker.read_bytes() == b"keep"


def test_project_create_refuses_a_file_even_with_overwrite(tmp_path, capsys) -> None:
    target = tmp_path / "occupied"
    target.write_bytes(b"keep")

    assert main(["project", "create", "--name", "occupied", "--workdir", str(tmp_path),
                 "--overwrite"]) == EXIT_INVALID
    assert "Choose a new project name or working directory" in capsys.readouterr().err
    assert target.read_bytes() == b"keep"


def test_json_output_stays_clean_on_stdout(project) -> None:
    # stdout must carry only the payload, so `bulkseq ... --json > f.json` is always valid.
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "project", "info", "-C", str(project), "--json"],
        capture_output=True, text=True, cwd=str(REPO), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    json.loads(result.stdout)  # raises if anything decorative leaked onto stdout


# --- documentation parity ---------------------------------------------------------------
#
# docs/cli.html and the README teach the command line by example. A worked example naming a
# subcommand that does not exist is worse than no example: the reader copies it, it fails, and
# nothing on the page says which part was wrong. `bulkseq run --exec-profile slurm` shipped in
# the docs and in both cluster profiles while the CLI had no `run` subcommand at all, so this
# derives the real command set from the parser and holds every documented invocation to it.

def _real_commands() -> set[str]:
    from app.cli import build_parser

    def walk(parser, prefix=""):
        out = set()
        for act in parser._actions:
            for name, sub in getattr(act, "_name_parser_map", {}).items():
                full = f"{prefix} {name}".strip()
                out.add(full)
                out |= walk(sub, full)
        return out

    return walk(build_parser())


def _documented_invocations() -> dict[str, list[str]]:
    import html as _html
    import re as _re
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[1]
    found: dict[str, list[str]] = {}
    for rel in ("docs/cli.html", "README.md", "workflow/profiles/site/slurm/config.yaml",
                "workflow/profiles/site/kubernetes/config.yaml"):
        path = repo / rel
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        # Only look where an invocation can actually be: code spans and command comments.
        # Prose that happens to contain the word ("the bulkseq command runs ...") is not a
        # worked example and must not be parsed as one.
        if path.suffix == ".html":
            spans = _re.findall(r"<(?:code|pre)[^>]*>(.*?)</(?:code|pre)>", raw, _re.S)
            text = "\n".join(_html.unescape(_re.sub(r"<[^>]+>", "", s)) for s in spans)
        elif path.suffix == ".md":
            spans = _re.findall(r"```.*?\n(.*?)```", raw, _re.S) + _re.findall(r"`([^`\n]+)`", raw)
            text = "\n".join(spans)
        else:  # the cluster profiles document usage in leading comments
            text = "\n".join(ln.lstrip("# ") for ln in raw.splitlines() if ln.lstrip().startswith("#"))
        hits = []
        # Single line only: an invocation never wraps, and `\s` would let the trailing word of
        # one code line join the first word of the next ("... bulkseq" + newline + "pip install").
        for m in _re.finditer(r"(?:^|[ \t])bulkseq((?:[ \t]+[a-z][a-z-]*)+)", text, _re.M):
            words = m.group(1).split()
            # Stop at the first token that is not a bare subcommand word.
            cmd = []
            for w in words:
                if w.startswith("-"):
                    break
                cmd.append(w)
            if cmd:
                hits.append(" ".join(cmd))
        if hits:
            found[rel] = hits
    return found


def test_documented_cli_invocations_exist():
    real = _real_commands()
    assert "print-command" in real and "project create" in real, sorted(real)
    documented = _documented_invocations()
    assert documented, "no bulkseq invocations found in the docs; the scan is broken"

    bad = []
    for rel, hits in documented.items():
        for hit in hits:
            words = hit.split()
            # A documented invocation is fine if any leading prefix of it is a real command;
            # trailing words are positional arguments (a config key, a project name).
            if not any(" ".join(words[:i]) in real for i in range(len(words), 0, -1)):
                bad.append(f"{rel}: 'bulkseq {hit}'")
    assert not bad, "documented commands that the CLI does not provide:\n  " + "\n  ".join(bad)
