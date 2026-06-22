from __future__ import annotations

import pytest

from app.core.project import ProjectManager


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
