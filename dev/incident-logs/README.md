# Incident logs (developer reference)

Real failure logs kept for regression context. Not part of the published docs site.

## `0.17.2-firstrun-empty-json-cache.excerpt.log`

First-run Check Environment on a clean Windows machine (WSL, micromamba), 2026-07-08. The setup could not finish and repeated the same failure on every retry. This is a trimmed, path/username-redacted excerpt of the load-bearing lines (this is a public repo); the full 5.4 MB raw log is kept off-repo by the maintainer. Line numbers below refer to the original raw log.

Key lines in the log:

- **line 447** — `error libmamba Could not set lock (Resource temporarily unavailable)` on the `11:24:39` run, 31 s after the `11:24:08` run. Two setups ran concurrently. This one self-recovered (`Waiting for other mamba process to finish`), so it is not the fatal error.
- **line 88512, 88541** — `critical libmamba [json.exception.parse_error.101] parse error at line 1, column 1: attempting to parse an empty input`. This is the fatal, recurring failure: a micromamba shard-cache JSON was left empty/truncated by the earlier interrupted/concurrent fetch, so every later `env update` re-read the same empty file and died at the parse step. The machine was stuck in this state.

Fixed in 0.17.2 (`scripts/setup_wsl_bioenv.sh`):

- **Self-healing cache** — on a failed env step the setup now clears only the index/shard cache and retries once (downloaded packages are kept, so the retry is fast).
- **Single-setup lock** — an atomic `mkdir` lock serializes the whole run so two setups can no longer corrupt the shared cache; a stale lock from a dead process is reclaimed.
- **GUI** (`app/ui/main_window.py`) — `show_readiness_dialog()` reuses an open dialog instead of spawning a second one, removing the double-launch that produced the concurrent runs.
