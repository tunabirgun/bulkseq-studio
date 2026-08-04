"""Generate app/assets/icons/bulkseq.icns from bulkseq_256.png using Pillow.

Why this exists instead of `iconutil` (the normal way to build a .icns): iconutil
only runs on macOS, and this repo is developed on Windows. Pillow's ICNS encoder
(PIL.IcnsImagePlugin) writes the Apple ICNS container format directly in pure
Python, so it produces a real, structurally valid .icns without needing macOS —
verified below by round-tripping the magic header and declared length against
actual file size. Re-run this script whenever bulkseq_256.png changes.

Quality note: the only source art checked into app/assets/icons/ is 256x256
(bulkseq_256.png; bulkseq_logo.svg exists but this repo has no SVG rasterizer
dependency, see requirements-build.txt). Apple's full icon set goes up to
1024x1024 (the 512x512@2x "retina" slot). Sizes above 256px here are produced
by Lanczos upsampling from the 256px source, not from higher-resolution art, so
they will look softer than a true 512/1024px master at Finder's largest zoom.
If a higher-resolution (ideally 1024x1024) source PNG is added later, point
SOURCE at it and drop the upsampled sizes' caveat.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "app" / "assets" / "icons" / "bulkseq_256.png"
OUTPUT = ROOT / "app" / "assets" / "icons" / "bulkseq.icns"

# Standard macOS icon slots. Pillow's ICNS writer maps each (w, h) to the
# matching ic## tag itself; sizes above the 256px source are upsampled (see
# module docstring).
SIZES = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)]


def build() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"source icon not found: {SOURCE}")

    im = Image.open(SOURCE).convert("RGBA")
    if im.size != (256, 256):
        raise SystemExit(f"expected a 256x256 source, got {im.size}: {SOURCE}")

    im.save(OUTPUT, sizes=SIZES)

    # Verify the file we just wrote is structurally a real ICNS: magic 'icns'
    # followed by a big-endian uint32 total length that must equal the file
    # size. This is the same check a distracted engineer would otherwise skip.
    data = OUTPUT.read_bytes()
    if data[:4] != b"icns":
        raise SystemExit(f"generated file is missing the 'icns' magic header: {OUTPUT}")
    declared_len = struct.unpack(">I", data[4:8])[0]
    if declared_len != len(data):
        raise SystemExit(
            f"generated ICNS length field ({declared_len}) does not match "
            f"actual file size ({len(data)}): {OUTPUT}"
        )

    print(f"wrote {OUTPUT} ({len(data):,} bytes, {len(SIZES)} sizes, magic+length verified)")


if __name__ == "__main__":
    sys.exit(build() or 0)
