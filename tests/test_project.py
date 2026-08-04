from __future__ import annotations

import pytest

from app.core.benchmark_datasets import create_benchmark_project
from app.core.project import ProjectExistsError, ProjectManager, is_project_root


def test_create_project_creates_network_and_stats_dirs(tmp_path) -> None:
    # results/networks and results/stats are written by pipeline rules; they must
    # exist from project creation so pre-run file access doesn't fail.
    root = ProjectManager().create_project("demo_proj", tmp_path)
    assert (root / "results" / "networks").is_dir()
    assert (root / "results" / "stats").is_dir()


def test_create_project_spaces_become_underscores(tmp_path) -> None:
    root = ProjectManager().create_project("my project", tmp_path)
    assert root.name == "my_project"


@pytest.mark.parametrize("bad", ["a/b", "a:b", "proj#1", "x(y)", "a*b", "  "])
def test_create_project_rejects_unsafe_names(tmp_path, bad) -> None:
    # Names with characters that break Snakemake wildcards / the filesystem path
    # must be rejected rather than silently creating an unusable directory.
    with pytest.raises(ValueError):
        ProjectManager().create_project(bad, tmp_path)


def test_create_project_refuses_to_overwrite_an_existing_project(tmp_path) -> None:
    # Scaffolding resets samples.tsv/contrasts/config to empty defaults. Re-creating
    # a project at the same path must NOT silently destroy the user's sample sheet.
    manager = ProjectManager()
    root = manager.create_project("study", tmp_path)
    sheet = root / "config" / "samples.tsv"
    sheet.write_text("sample_id\tcondition\nS1\ttreated\n", encoding="utf-8")

    with pytest.raises(ProjectExistsError) as excinfo:
        manager.create_project("study", tmp_path)
    assert excinfo.value.root == root
    # The refusal must leave the existing project completely untouched.
    assert sheet.read_text(encoding="utf-8") == "sample_id\tcondition\nS1\ttreated\n"


def test_create_project_overwrite_is_opt_in(tmp_path) -> None:
    manager = ProjectManager()
    root = manager.create_project("study", tmp_path)
    sheet = root / "config" / "samples.tsv"
    sheet.write_text("sample_id\tcondition\nS1\ttreated\n", encoding="utf-8")

    again = manager.create_project("study", tmp_path, overwrite=True)
    assert again == root
    # Explicit overwrite does reset the sheet — that is the confirmed-by-the-user path.
    assert "S1" not in sheet.read_text(encoding="utf-8")


def test_benchmark_project_refuses_to_overwrite(tmp_path) -> None:
    # The benchmark route defaults the project name to the benchmark id, so clicking
    # "Create Benchmark Project" twice targets one directory. It delegates to
    # create_project, so the guard must reach it too.
    create_benchmark_project("pasilla_paired_subset", tmp_path, "bench")
    with pytest.raises(ProjectExistsError):
        create_benchmark_project("pasilla_paired_subset", tmp_path, "bench")


def test_is_project_root_matches_the_scaffolded_layout(tmp_path) -> None:
    # One definition of "is a project", shared by the overwrite guard and the GUI's
    # open-project validation; they must not drift apart.
    assert not is_project_root(tmp_path / "nope")
    root = ProjectManager().create_project("p", tmp_path)
    assert is_project_root(root)
