from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_wsl_bioenv.sh"


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
