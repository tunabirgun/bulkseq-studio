from __future__ import annotations

import sys

import pytest

import app.core.snakemake_runner as runner
from app.core.paths import without_bundle_library_path

BUNDLE = "/tmp/_MEIsentinel"


def test_frozen_child_gets_the_callers_library_path_back(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    env = without_bundle_library_path(
        {"LD_LIBRARY_PATH": BUNDLE, "LD_LIBRARY_PATH_ORIG": "/opt/lib", "HOME": "/home/u"})
    assert env == {"LD_LIBRARY_PATH": "/opt/lib", "HOME": "/home/u"}


def test_frozen_child_without_an_original_value_gets_none(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "LD_LIBRARY_PATH" not in without_bundle_library_path({"LD_LIBRARY_PATH": BUNDLE})


def test_unfrozen_environment_is_left_alone(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = {"LD_LIBRARY_PATH": "/user/set", "LD_LIBRARY_PATH_ORIG": "x"}
    assert without_bundle_library_path(env) == env


@pytest.mark.parametrize("use_wsl", [False, True])
def test_pipeline_rules_do_not_inherit_the_bundle_path(monkeypatch, use_wsl):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", BUNDLE)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    env = runner._snakemake_child_env(use_wsl)
    assert BUNDLE not in env.get("LD_LIBRARY_PATH", "")
    assert env["LC_NUMERIC"] == "C"
