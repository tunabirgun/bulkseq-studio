#!/usr/bin/env python3
"""Run every release gate, and report which ones could not run.

    python scripts/preflight.py [--skip-tests]

The manuscript and benchmark gates live under `article/`, which is gitignored, so no CI job can
run them: their inputs are not in the repository. That is a real constraint rather than an
oversight, and it means the gates only ever run when someone runs them. This is that someone's
one command.

Exit status is the number of gates that failed. A gate that cannot run counts as a failure, not
as a pass: five gates were found inert during the 0.26.5 cycle, and every one of them reported
success while checking nothing.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class Gate:
    def __init__(self, name: str, cmd: list[str], needs: list[Path], why: str):
        self.name, self.cmd, self.needs, self.why = name, cmd, needs, why


def gates(skip_tests: bool) -> list[Gate]:
    py = sys.executable
    g = [
        Gate("consistency", [py, "-X", "utf8", str(REPO / "article/scripts/check_consistency.py")],
             [REPO / "article/scripts/check_consistency.py"],
             "manuscript, docs and deposit agree with the deposited tables"),
        Gate("typography", [py, "-X", "utf8", str(REPO / "article/scripts/check_typography.py")],
             [REPO / "article/scripts/check_typography.py"],
             "italics, superscripts and subscripts"),
        Gate("table generation", [py, "-X", "utf8", str(REPO / "article/scripts/build_main_tables.py")],
             [REPO / "article/scripts/build_main_tables.py"],
             "every table caption has a body, and the deliverables regenerate"),
        Gate("B19 interval self-test",
             [py, "-X", "utf8", str(REPO / "article/bench/B19_meta/scripts/score_b19.py"), "--self-test"],
             [REPO / "article/bench/B19_meta/scripts/score_b19.py"],
             "the exact binomial matches binom.test"),
    ]
    if not skip_tests:
        g.append(Gate("python tests", [py, "-X", "utf8", "-m", "pytest", str(REPO / "tests"),
                                       "-q", "--no-header", "-p", "no:cacheprovider"],
                      [REPO / "tests"], "the application test suite"))
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tests", action="store_true",
                    help="skip the pytest run, which takes about five minutes")
    args = ap.parse_args()

    results: list[tuple[str, str, str]] = []
    for g in gates(args.skip_tests):
        missing = [n for n in g.needs if not n.exists()]
        if missing:
            results.append((g.name, "CANNOT RUN", f"missing {missing[0].name}"))
            continue
        r = subprocess.run(g.cmd, cwd=str(REPO), capture_output=True, text=True)
        tail = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
        results.append((g.name, "pass" if r.returncode == 0 else "FAIL",
                        tail[-1][:70] if tail else f"exit {r.returncode}"))

    width = max(len(n) for n, _, _ in results)
    print("\nrelease preflight\n")
    for name, status, detail in results:
        mark = {"pass": "  ok  ", "FAIL": " FAIL ", "CANNOT RUN": " ???? "}[status]
        print(f"{mark} {name:<{width}}  {detail}")
    bad = [n for n, s, _ in results if s != "pass"]
    print(f"\n{len(results) - len(bad)}/{len(results)} gates passed")
    if bad:
        print("not releasable: " + ", ".join(bad))
    return len(bad)


if __name__ == "__main__":
    raise SystemExit(main())
