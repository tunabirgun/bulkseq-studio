from __future__ import annotations

# The startup banner.
#
# Two constraints shape this, and both come from real failure modes:
#
# 1. PURE 7-BIT ASCII. No box-drawing or block glyphs. The banner can print before stream
#    encoding is reconfigured, and a non-ASCII glyph on a cp1252 console raises
#    UnicodeEncodeError — the exact problem that forced the reconfigure workaround in
#    app/benchmark_cli.py. A logo that crashes the program is not a logo.
#
# 2. IT GOES TO STDERR, NEVER STDOUT. stdout carries only what a command was asked to
#    produce, so `bulkseq config show --json > c.json` cannot pick up decoration.

import os
import sys

_LOGO = r"""
  ___        _ _   ___
 | _ ) _  _ | | |_/ __| ___  __ _
 | _ \| || || | | \__ \/ -_)/ _` |
 |___/ \_,_||_|_| |___/\___|\__, |
                            |___/   S T U D I O
"""


def banner_text(version: str, subtitle: str = "") -> str:
    """The banner as a string, so tests can assert on it without capturing a stream."""
    lines = _LOGO.strip("\n").splitlines()
    lines.append("")
    tail = f"  v{version}"
    if subtitle:
        tail += f"  |  {subtitle}"
    lines.append(tail)
    return "\n".join(lines) + "\n"


def should_show_banner(*, json_output: bool = False, quiet: bool = False,
                       stream=None) -> bool:
    """Whether to print the banner at all.

    Precedence, most specific first: --json (machine-readable output must stay clean),
    then --quiet, then the BULKSEQ_NO_BANNER escape hatch for scripts and CI, and finally
    a TTY check. The TTY check is what makes `bulkseq check 2> run.log` produce a clean log
    without the user having to know a flag exists.
    """
    if json_output or quiet:
        return False
    if os.environ.get("BULKSEQ_NO_BANNER"):
        return False
    stream = stream if stream is not None else sys.stderr
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        # A closed or exotic stream is not a terminal.
        return False


def print_banner(version: str, subtitle: str = "", *, json_output: bool = False,
                 quiet: bool = False, stream=None) -> None:
    stream = stream if stream is not None else sys.stderr
    if not should_show_banner(json_output=json_output, quiet=quiet, stream=stream):
        return
    try:
        stream.write(banner_text(version, subtitle))
        stream.flush()
    except (OSError, UnicodeEncodeError):
        # Decoration must never take the run down.
        pass
