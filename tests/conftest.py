from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Run every test in its own temporary working directory.

    Several tests create scratch projects via relative paths (``manual_test_*``).
    Without isolation those land in the repository tree and accumulate as litter.
    Application code resolves its own paths from ``__file__`` (see app.core.paths),
    so changing the working directory does not affect it.
    """
    monkeypatch.chdir(tmp_path)
    yield
