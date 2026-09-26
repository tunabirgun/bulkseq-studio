from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_a_startup_probe_that_outlives_close_does_not_abort_the_exit(tmp_path):
    # The WSL work-directory probe starts with the window and can outlast closeEvent's wait
    # on a cold machine; the QThread was then destroyed while running and Qt aborted the
    # process (0xC0000409 on Windows, SIGABRT on Linux) after a passing self-test.
    sentinel = tmp_path / "selftest.json"
    probe = textwrap.dedent('''
        import sys, time
        import app.ui.main_window as mw
        def slow_workdir():
            time.sleep(6)
        mw.wsl_recommended_workdir = slow_workdir
        mw.shutil.which = lambda name, *a, **k: "wsl" if name == "wsl" else None
        from app.main import main
        sys.exit(main())
    ''')
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "BULKSEQ_SELFTEST": "1",
           "BULKSEQ_SKIP_READINESS_DIALOG": "1", "BULKSEQ_SELFTEST_OUT": str(sentinel),
           "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu --no-sandbox"}
    done = subprocess.run([sys.executable, "-c", probe], cwd=REPO, env=env, capture_output=True,
                          text=True, timeout=180, check=False)
    assert "QThread: Destroyed while thread" not in done.stderr, done.stderr[-2000:]
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])
    assert json.loads(sentinel.read_text(encoding="utf-8"))["pass"] is True
