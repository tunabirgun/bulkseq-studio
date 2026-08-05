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

from app.cli import EXIT_INVALID, EXIT_OK, main
from app.cli_banner import banner_text, should_show_banner
from app.constants import APP_VERSION
from app.core.project import ProjectManager
from app.core.snakemake_runner import build_snakemake_args, build_snakemake_command

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def project(tmp_path) -> Path:
    return ProjectManager().create_project("clitest", tmp_path)


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


def test_commands_refuse_a_directory_that_is_not_a_project(tmp_path) -> None:
    assert main(["project", "info", "-C", str(tmp_path)]) == EXIT_INVALID


def test_project_create_refuses_to_overwrite(tmp_path) -> None:
    assert main(["project", "create", "--name", "p", "--workdir", str(tmp_path)]) == EXIT_OK
    assert main(["project", "create", "--name", "p", "--workdir", str(tmp_path)]) == EXIT_INVALID


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
