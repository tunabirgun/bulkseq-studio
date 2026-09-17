from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _runtime import bash_runtime
from app.core.paths import windows_to_wsl_path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_wsl_bioenv.sh"


def _bash_or_skip():
    runtime = bash_runtime()
    if runtime is None:
        pytest.skip("no runnable bash environment")
    return runtime


def _python_heredoc() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"python3 - \"\$1\" \"\$2\" \"\$3\" <<'PY'\n(.*?)\nPY\n", text, re.S)
    assert match, "python3 bootstrap heredoc not found"
    return match.group(1)


def test_bootstrap_download_failure_is_one_line_without_traceback(tmp_path: Path) -> None:
    # .invalid is a reserved TLD (RFC 2606): name resolution fails at once on every platform.
    dest = tmp_path / "bin" / "micromamba"
    completed = subprocess.run(
        [sys.executable, "-", "http://bootstrap.invalid/micromamba.tar.bz2", str(dest), "0" * 64],
        input=_python_heredoc(), capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode != 0
    assert "Traceback" not in completed.stderr + completed.stdout
    assert "download failed:" in completed.stderr
    assert not dest.exists()


def test_bootstrap_interrupted_response_retries_cleanly(tmp_path: Path) -> None:
    import hashlib
    import http.server
    import io
    import socket
    import tarfile
    import threading

    binary = b"synthetic micromamba\n"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:bz2") as archive:
        info = tarfile.TarInfo("bin/micromamba")
        info.size = len(binary)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(binary))
    payload = stream.getvalue()

    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self):  # noqa: N802
            type(self).requests += 1
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if type(self).requests == 1:
                self.wfile.write(payload[: len(payload) // 2])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dest = tmp_path / "micromamba"
    url = f"http://127.0.0.1:{server.server_port}/micromamba.tar.bz2"
    digest = hashlib.sha256(payload).hexdigest()
    try:
        interrupted = subprocess.run(
            [sys.executable, "-", url, str(dest), digest], input=_python_heredoc(),
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert interrupted.returncode != 0
        assert "download failed:" in interrupted.stderr
        assert "Traceback" not in interrupted.stderr + interrupted.stdout
        assert not dest.exists()
        assert not Path(str(dest) + ".partial").exists()
        retried = subprocess.run(
            [sys.executable, "-", url, str(dest), digest], input=_python_heredoc(),
            capture_output=True, text=True, timeout=30, check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not Path(str(dest) + ".partial").exists()
    assert retried.returncode == 0, retried.stderr + retried.stdout
    assert dest.read_bytes() == binary


def test_bootstrap_checksum_mismatch_is_reported(tmp_path: Path) -> None:
    # Negative control for the gate itself: a served file with the wrong hash must be refused.
    import http.server
    import threading

    payload = b"not a real archive"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/micromamba.tar.bz2"
        completed = subprocess.run(
            [sys.executable, "-", url, str(tmp_path / "micromamba"), "0" * 64],
            input=_python_heredoc(), capture_output=True, text=True, timeout=60, check=False,
        )
    finally:
        server.shutdown()
    assert completed.returncode != 0
    assert "checksum mismatch" in completed.stderr
    assert not (tmp_path / "micromamba").exists()


def test_setup_script_names_the_download_cause_before_the_missing_tools_message() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.index("could not be downloaded from") < text.index("could not be installed automatically")
    assert "fetch_failed" in text


@pytest.mark.skipif(sys.platform == "darwin", reason="native Linux installer initializer")
def test_setup_log_initializes_outside_a_read_only_bundle(tmp_path: Path) -> None:
    """The pre-fix bundled log path fails here; this stops before any bootstrap."""
    runtime, convert = _bash_or_skip()
    bundle = tmp_path / "read only bundle"
    scripts = bundle / "scripts"
    scripts.mkdir(parents=True)
    copied = scripts / SCRIPT.name
    source = SCRIPT.read_text(encoding="utf-8")
    initializer, sentinel, _ = source.partition("acquire_lock\n")
    assert sentinel, "setup initializer boundary no longer precedes the bootstrap"
    assert 'exec > >(tee -a "$LOG_FILE") 2>&1' in initializer
    for forbidden in ("Stage 1/3", "sudo -n true", "bootstrap_with_python3"):
        assert forbidden not in initializer, f"initializer test reached bootstrap content: {forbidden}"
    initializer += "exit 91\n"
    copied.write_text(initializer, encoding="utf-8", newline="\n")
    legacy = scripts / "legacy_setup.sh"
    legacy_log_line = next(line for line in initializer.splitlines() if line.startswith("LOG_DIR="))
    legacy.write_text(initializer.replace(legacy_log_line, 'LOG_DIR="$REPO_DIR/scripts/logs"'),
                      encoding="utf-8", newline="\n")
    copied.chmod(0o555)
    scripts.chmod(0o555)
    bundle.chmod(0o555)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    mkdir_stub = stubs / "mkdir"
    mkdir_stub.write_text(
        '#!/usr/bin/env bash\nif [ "${1:-}" = "-p" ] && [[ "${2:-}" == */scripts/logs ]]; then exit 73; fi\nexec /usr/bin/mkdir "$@"\n',
        encoding="utf-8", newline="\n",
    )
    mkdir_stub.chmod(0o755)
    log_dir = tmp_path / "user data" / "Tuna O'Brien" / "logs"
    try:
        if os.name == "nt":
            launcher = [runtime[0], "--exec", "env", f"HOME={windows_to_wsl_path(tmp_path / 'home')}",
                        f"BULKSEQ_SETUP_LOG_DIR={windows_to_wsl_path(log_dir)}",
                        f"PATH={windows_to_wsl_path(stubs)}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                        "bash"]
            legacy_completed = subprocess.run(
                [*launcher, windows_to_wsl_path(legacy), "audit-env", "core"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            completed = subprocess.run(
                [*launcher, windows_to_wsl_path(copied), "audit-env", "core"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        else:
            env = dict(os.environ)
            env.update({
                "HOME": str(tmp_path / "home"),
                "BULKSEQ_SETUP_LOG_DIR": str(log_dir),
                "PATH": str(stubs) + os.pathsep + env.get("PATH", ""),
            })
            legacy_completed = subprocess.run([*runtime, convert(legacy), "audit-env", "core"], env=env,
                                              capture_output=True, text=True, timeout=30, check=False)
            completed = subprocess.run([*runtime, convert(copied), "audit-env", "core"], env=env,
                                       capture_output=True, text=True, timeout=30, check=False)
    finally:
        bundle.chmod(0o755)
        scripts.chmod(0o755)
        copied.chmod(0o755)
    assert legacy_completed.returncode == 73
    assert completed.returncode == 91
    log_file = log_dir / "wsl_bioenv_install.log"
    assert log_file.exists(), completed.stdout + completed.stderr
    assert "BulkSeq Studio WSL bioinformatics setup" in log_file.read_text(encoding="utf-8")
    assert not (scripts / "logs").exists()
