#!/usr/bin/env python3
"""Render documentation screenshots of the CLI from its REAL output.

    python scripts/capture_cli_screenshots.py [--out docs/assets/cli]

Each figure is produced by actually running the command and capturing what it printed —
nothing here is typed by hand, so a screenshot cannot drift from the program. Re-run it
after changing the CLI and the docs update with it.

Output is SVG rather than PNG on purpose: it stays crisp at any zoom, diffs as text in
review, is a few KB instead of a few hundred, and needs no image library. It also renders
identically on Windows and Linux, so the same command regenerates the same figure on
either platform.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Terminal palette, close to the docs site's dark code blocks.
BG = "#15162a"
FG = "#e8e8f5"
DIM = "#9aa1b2"
PROMPT = "#7ee2b8"
TITLE_BAR = "#1d1f38"
CHAR_W = 8.4
LINE_H = 20.0
PAD = 18.0


# A figure wider than this is unreadable in a page: it either forces a horizontal scrollbar
# or gets scaled down to illegibility. Long command lines are wrapped instead.
MAX_COLS = 96
CONT = "    \\"          # trailing marker on a wrapped line, as a shell continuation reads


def _sanitize(text: str, workdir: Path, project: Path) -> str:
    """Strip machine-specific paths and the user's name out of captured output.

    Both the native form (C:\\Users\\...\\Temp\\bulkseq_shots_ab12) and the WSL form
    (/mnt/c/Users/.../Temp/bulkseq_shots_ab12) appear, because the local profile is wrapped
    for WSL. Replacing only one leaks the other into a published asset.
    """
    def variants(path: Path) -> list[str]:
        native = str(path)
        return [native, native.replace("\\", "/"),
                "/mnt/" + native[0].lower() + native[2:].replace("\\", "/")]

    for form in variants(project):
        text = text.replace(form, "~/pasilla_demo")
    for form in variants(workdir):
        text = text.replace(form, "~")
    # Catch-all. The explicit forms above can still miss: Windows may hand back an 8.3
    # short path (C:\Users\TUNABI~1\...) where the app's own WSL translation produced the
    # long one, so the two strings differ while naming the same directory. Anything still
    # carrying the throwaway workdir's name is collapsed whole, rather than left to leak a
    # real username into a published asset.
    text = re.sub(r"\S*" + re.escape(workdir.name), "~", text)
    text = re.sub(r"/mnt/[a-z]/Users/[^/\s\"]+", "~", text)
    text = re.sub(r"[A-Za-z]:\\\\?Users\\\\?[^\\\s\"]+", "~", text)
    return text


def _wrap(text: str, width: int = MAX_COLS) -> list[str]:
    """Wrap long lines at word boundaries, marking the continuation."""
    out: list[str] = []
    for line in text.split("\n"):
        if len(line) <= width:
            out.append(line)
            continue
        indent = " " * (len(line) - len(line.lstrip()))
        current = ""
        for token in line.split(" "):
            candidate = token if not current else f"{current} {token}"
            if len(candidate) > width - len(CONT) and current:
                out.append(current + CONT)
                current = indent + "  " + token
            else:
                current = candidate
        if current:
            out.append(current)
    return out


def _run(argv: list[str], cwd: Path) -> str:
    """Run a command and return stdout+stderr exactly as a user would see it."""
    env = dict(os.environ)
    env["BULKSEQ_NO_BANNER"] = ""          # let the banner logic decide
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("BULKSEQ_NO_BANNER")            # not set at all == default behaviour
    result = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300, env=env)
    out = (result.stdout or "") + (result.stderr or "")
    return out.replace("\r\n", "\n").rstrip("\n")


def _svg(title: str, blocks: list[tuple[str, str]]) -> str:
    """Render prompt/output pairs as a terminal window.

    blocks is a list of (command, output). The command is drawn on a prompt line, the
    output verbatim beneath it.
    """
    lines: list[tuple[str, str]] = []
    for command, output in blocks:
        for i, part in enumerate(_wrap(command)):
            lines.append(("prompt" if i == 0 else "prompt-cont", part))
        for line in _wrap(output):
            lines.append(("out", line))
        lines.append(("out", ""))
    while lines and lines[-1][1] == "":
        lines.pop()

    # The title sits centred in the bar and the window dots occupy the left ~62px, so the
    # canvas must be wide enough that they cannot collide however short the content is.
    title_w = len(title) * 12 * 0.601
    min_for_title = (62.0 + title_w / 2) * 2 + PAD
    width_chars = max((len(text) for _, text in lines), default=40)
    # +2 columns of slack so a glyph slightly wider than the estimate still clears the edge.
    width = max(560.0, min_for_title, (width_chars + 2) * CHAR_W + PAD * 2)
    height = len(lines) * LINE_H + PAD * 2 + 34

    rows = []
    y = PAD + 34 + LINE_H * 0.8
    for kind, text in lines:
        if kind == "prompt":
            rows.append(
                f'<text x="{PAD}" y="{y:.1f}" xml:space="preserve">'
                f'<tspan fill="{PROMPT}">$ </tspan>'
                f'<tspan fill="{FG}">{html.escape(text)}</tspan></text>'
            )
        elif kind == "prompt-cont":
            rows.append(
                f'<text x="{PAD}" y="{y:.1f}" fill="{FG}" xml:space="preserve">'
                f'  {html.escape(text)}</text>'
            )
        else:
            rows.append(
                f'<text x="{PAD}" y="{y:.1f}" fill="{DIM}" xml:space="preserve">'
                f'{html.escape(text)}</text>'
            )
        y += LINE_H

    dots = "".join(
        f'<circle cx="{18 + i * 18}" cy="17" r="5.5" fill="{c}"/>'
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" \
viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{html.escape(title)}">
  <rect width="{width:.0f}" height="{height:.0f}" rx="10" fill="{BG}"/>
  <rect width="{width:.0f}" height="34" rx="10" fill="{TITLE_BAR}"/>
  <rect y="24" width="{width:.0f}" height="10" fill="{TITLE_BAR}"/>
  {dots}
  <text x="{width / 2:.0f}" y="22" fill="{DIM}" font-size="12" text-anchor="middle"
        font-family="ui-monospace, 'IBM Plex Mono', monospace">{html.escape(title)}</text>
  <g font-family="ui-monospace, 'IBM Plex Mono', 'SFMono-Regular', Consolas, monospace"
     font-size="13.5">
{chr(10).join('    ' + r for r in rows)}
  </g>
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/assets/cli")
    args = parser.parse_args()

    out_dir = REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    cli = [py, "-m", "app.cli"]

    workdir = Path(tempfile.mkdtemp(prefix="bulkseq_shots_"))
    project = workdir / "pasilla_demo"
    figures: list[tuple[str, str, list[tuple[str, str]]]] = []

    try:
        # 1. Help — the command surface at a glance.
        figures.append((
            "cli-help.svg", "bulkseq --help",
            [("bulkseq --help", _run(cli + ["--help"], REPO))],
        ))

        # 2. Create a project and inspect it.
        create_out = _run(cli + ["project", "create", "--name", "pasilla_demo",
                                 "--workdir", str(workdir)], REPO)
        info_out = _run(cli + ["project", "info", "-C", str(project)], REPO)
        figures.append((
            "cli-project.svg", "Creating and inspecting a project",
            [("bulkseq project create --name pasilla_demo --workdir ~", create_out),
             ("bulkseq project info", info_out)],
        ))

        # 3. Configuration: read, write, and a rejected value.
        get_out = _run(cli + ["config", "get", "deseq2.alpha", "-C", str(project)], REPO)
        set_out = _run(cli + ["config", "set", "deseq2.alpha", "0.01", "-C", str(project)], REPO)
        bad_out = _run(cli + ["config", "set", "deseq2.alpha", "1.5", "-C", str(project)], REPO)
        figures.append((
            "cli-config.svg", "Configuration is validated, not just written",
            [("bulkseq config get deseq2.alpha", get_out),
             ("bulkseq config set deseq2.alpha 0.01", set_out),
             ("bulkseq config set deseq2.alpha 1.5", bad_out)],
        ))

        # 4. Execution targets: the same project, three places to run it.
        blocks = []
        for profile in ("local", "slurm", "kubernetes"):
            out = _run(cli + ["print-command", "-C", str(project), "--exec-profile", profile], REPO)
            blocks.append((f"bulkseq print-command --exec-profile {profile}", out))
        figures.append((
            "cli-exec-profiles.svg", "One project, three execution targets", blocks,
        ))

        for name, title, blocks in figures:
            clean = [(cmd, _sanitize(out, workdir, project)) for cmd, out in blocks]
            (out_dir / name).write_text(_svg(title, clean), encoding="utf-8")
            print(f"wrote {args.out}/{name}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
