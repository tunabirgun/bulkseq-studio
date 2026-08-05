#!/usr/bin/env python3
"""Geometric overlap and overflow check for generated SVG figures.

    python scripts/check_svg_layout.py docs/assets/cli/*.svg

Measures every <text> element's bounding box from its position, font size and character
count, then reports:

  * text that overlaps other text,
  * text that overlaps a non-text element (the window chrome dots),
  * text that runs past the canvas edge and would be clipped,
  * text that collides with the canvas top/bottom padding.

A monospace advance of 0.601 em is used, which is what IBM Plex Mono / SFMono / Consolas
all sit at; the estimate is rounded UP so the check errs toward reporting a collision that
is merely tight rather than missing a real one.

Exit code is non-zero when anything is found, so it can gate a build.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MONO_ADVANCE = 0.601   # em per character for the monospace stack used in these figures
EDGE_MARGIN = 4.0      # px a glyph must keep clear of the canvas edge


class Box:
    def __init__(self, x0: float, y0: float, x1: float, y1: float, label: str) -> None:
        self.x0, self.y0, self.x1, self.y1, self.label = x0, y0, x1, y1, label

    def overlaps(self, other: "Box") -> bool:
        return not (self.x1 <= other.x0 or other.x1 <= self.x0
                    or self.y1 <= other.y0 or other.y1 <= self.y0)

    def __repr__(self) -> str:
        return f"[{self.x0:.0f},{self.y0:.0f} -> {self.x1:.0f},{self.y1:.0f}] {self.label!r}"


def _unescape(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&#39;", "'").replace("&#183;", "·").replace("&amp;", "&"))


def _attr(attrs: str, name: str):
    """An SVG presentation attribute, whether written as an attribute or inside style=""."""
    m = re.search(rf'''\b{name}=["']([^"']+)["']''', attrs)
    if m:
        return m.group(1)
    m = re.search(rf'''style=["'][^"']*\b{name}\s*:\s*([^;"']+)''', attrs)
    return m.group(1).strip() if m else None


def _num(value, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    m = re.match(r"\s*(-?[\d.]+)", str(value))
    return float(m.group(1)) if m else fallback


class UnmeasurableSVG(Exception):
    """The file uses a construct this checker cannot position.

    Raised rather than returning boxes at a guessed origin. A geometric checker that cannot
    locate an element must say so: reporting thousands of overlaps derived from a fallback
    coordinate is worse than reporting nothing, because it trains the reader to ignore it.
    """


def _text_boxes(svg: str, default_size: float) -> list[Box]:
    boxes: list[Box] = []
    unplaced = 0
    for match in re.finditer(r"<text\b([^>]*)>(.*?)</text>", svg, re.S):
        attrs, inner = match.group(1), match.group(2)
        t_x, t_y = _num(_attr(attrs, "x")), _num(_attr(attrs, "y"))
        t_size = _num(_attr(attrs, "font-size"), default_size)
        anchor = _attr(attrs, "text-anchor") or "start"

        # One box per BASELINE, not per tspan. Hand-laid diagrams put x/y on the tspans and
        # leave the parent <text> unpositioned, so the parent cannot be treated as a single
        # string; but several tspans sharing a baseline render as one continuous line (a
        # prompt and its command, a word given its own colour), and comparing those against
        # each other reports an overlap where the text simply continues.
        spans = re.findall(r"<tspan\b([^>]*)>(.*?)</tspan>", inner, re.S)
        if spans:
            by_baseline: dict[float | None, list] = {}
            for s_attrs, s_inner in spans:
                y_s = _num(_attr(s_attrs, "y"), t_y)
                by_baseline.setdefault(y_s, []).append((
                    _num(_attr(s_attrs, "x"), t_x),
                    _num(_attr(s_attrs, "font-size"), t_size),
                    _attr(s_attrs, "text-anchor") or anchor,
                    _unescape(re.sub(r"<[^>]+>", "", s_inner)),
                ))
            lines = []
            for y_s, runs in by_baseline.items():
                xs = [r[0] for r in runs if r[0] is not None]
                lines.append((
                    min(xs) if xs else None, y_s,
                    max((r[1] for r in runs if r[1]), default=t_size),
                    runs[0][2],
                    "".join(r[3] for r in runs),
                ))
        else:
            lines = [(t_x, t_y, t_size, anchor, _unescape(re.sub(r"<[^>]+>", "", inner)))]

        for x, y, size, anc, content in lines:
            if not content.strip():
                continue
            if x is None or y is None:
                unplaced += 1
                continue
            size = size or default_size
            width = len(content) * size * MONO_ADVANCE
            x0 = x - width / 2 if anc == "middle" else x - width if anc == "end" else x
            # Baseline-relative box: ascent above, descent below.
            boxes.append(Box(x0, y - size * 0.80, x0 + width, y + size * 0.22,
                             content.strip()[:64]))

    if unplaced:
        raise UnmeasurableSVG(
            f"{unplaced} text run(s) carry no x/y on the element or its tspans, so their "
            "position is unknown; this checker cannot measure the file")
    return boxes


def _shape_boxes(svg: str) -> list[Box]:
    shapes: list[Box] = []
    for m in re.finditer(r'''<circle\s+cx=["']([\d.]+)["']\s+cy=["']([\d.]+)["']\s+r=["']([\d.]+)["']''', svg):
        cx, cy, r = (float(g) for g in m.groups())
        shapes.append(Box(cx - r, cy - r, cx + r, cy + r, "window-dot"))
    return shapes


# The width model is a monospace advance, which is right for the terminal screenshots this
# tool was written for and wrong for a proportional face: EB Garamond sets far narrower than
# 0.601 em, so every box would be over-wide and near-margin text would be reported as
# overflowing when it does not. Rather than guess a per-font advance, the checker declines
# files it cannot measure to that standard.
_MONOSPACE_HINTS = ("mono", "consolas", "menlo", "courier")


def _is_monospace(svg: str) -> bool:
    fonts = re.findall(r"""font-family\s*[:=]\s*["']?([^"';]+)""", svg, re.I)
    return any(any(h in f.lower() for h in _MONOSPACE_HINTS) for f in fonts)


def check(path: Path) -> list[str]:
    svg = path.read_text(encoding="utf-8")
    problems: list[str] = []

    size_m = re.search(r"""font-size=["']?([\d.]+)""", svg)
    default_size = float(size_m.group(1)) if size_m else 13.5
    # svglite writes single-quoted attributes and a root size in points ("538.58pt"), so a
    # double-quote, bare-number pattern reported such a file as malformed when it is merely
    # written by a different generator. The viewBox is preferred where present, being the
    # coordinate system the text is actually placed in.
    vb = re.search(r"""viewBox=["'][\d.\s-]*?([\d.]+)[\s,]+([\d.]+)["']""", svg)
    root = re.search(
        r"""<svg[^>]*\bwidth=["']([\d.]+)[a-z%]*["'][^>]*\bheight=["']([\d.]+)[a-z%]*["']""", svg)
    if vb:
        width, height = float(vb.group(1)), float(vb.group(2))
    elif root:
        width, height = float(root.group(1)), float(root.group(2))
    else:
        return [f"{path.name}: no viewBox or width/height on <svg>"]

    if not _is_monospace(svg):
        raise UnmeasurableSVG(
            "the figure uses a proportional font; this checker's monospace width model "
            "would misplace every box, so it is not measured here")
    texts = _text_boxes(svg, default_size)
    shapes = _shape_boxes(svg)

    for i, a in enumerate(texts):
        if a.x1 > width - EDGE_MARGIN:
            problems.append(f"{path.name}: text runs past the right edge "
                            f"(x1={a.x1:.0f} > {width:.0f}): {a.label!r}")
        if a.x0 < EDGE_MARGIN:
            problems.append(f"{path.name}: text starts past the left edge: {a.label!r}")
        if a.y1 > height - EDGE_MARGIN or a.y0 < 0:
            problems.append(f"{path.name}: text outside the canvas vertically: {a.label!r}")
        for b in texts[i + 1:]:
            if a.overlaps(b):
                problems.append(f"{path.name}: TEXT/TEXT overlap {a.label!r} <-> {b.label!r}")
        for s in shapes:
            if a.overlaps(s):
                problems.append(f"{path.name}: TEXT/SHAPE overlap {a.label!r} <-> {s.label}")
    return problems


def main(argv: list[str]) -> int:
    paths: list[Path] = []
    for arg in argv or ["docs/assets/cli"]:
        p = Path(arg)
        paths.extend(sorted(p.glob("*.svg")) if p.is_dir() else [p])
    if not paths:
        print("no SVG files given")
        return 2

    all_problems: list[str] = []
    unmeasurable: list[str] = []
    for path in paths:
        # A file the checker cannot position is reported as SKIP, not as a pass and not as a
        # heap of findings derived from a guessed coordinate. Silence and noise are both
        # worse than saying which files were actually examined.
        try:
            found = check(path)
        except UnmeasurableSVG as exc:
            print(f"  SKIP {path.name}: {exc}")
            unmeasurable.append(path.name)
            continue
        status = "OK  " if not found else "FAIL"
        print(f"  {status} {path.name}")
        all_problems.extend(found)

    if all_problems:
        print("\nproblems:")
        for problem in all_problems:
            print("  -", problem)
        return 1
    measured = len(paths) - len(unmeasurable)
    print(f"\n{measured} figure(s) measured: no overlaps, no overflow"
          + (f"; {len(unmeasurable)} skipped as unmeasurable" if unmeasurable else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
