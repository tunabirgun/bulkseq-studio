from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup_wsl_bioenv.sh"

# The micromamba bootstrap is the one download the installer verifies itself: a pinned version,
# one SHA-256 per supported platform, and a URL built from the pinned version. The constants live
# in the script; these assertions check their shape, so they cannot be restated wrongly here.
_VERSION = re.compile(r'^MM_VERSION="(\d+\.\d+\.\d+)"$', re.MULTILINE)
_SHA256 = re.compile(r'^\s*MM_SHA256="([^"]*)"\s*;;$', re.MULTILINE)
_URL = re.compile(r'^MM_URL="([^"]*)"$', re.MULTILINE)


def _assert_bootstrap_pins(text: str) -> None:
    versions = _VERSION.findall(text)
    assert len(versions) == 1, f"expected one pinned MM_VERSION, found {versions}"
    digests = _SHA256.findall(text)
    assert len(digests) == 2, f"expected one MM_SHA256 per supported platform, found {len(digests)}"
    for digest in digests:
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"not a lowercase sha256 digest: {digest!r}"
    assert len(set(digests)) == len(digests), (
        "the platform digests are identical, so one platform is verified against the other "
        "platform's archive")
    urls = _URL.findall(text)
    assert len(urls) == 1, f"expected one MM_URL, found {urls}"
    assert "${MM_VERSION}" in urls[0], (
        f"MM_URL must interpolate the pinned version, not carry its own: {urls[0]!r}")
    assert versions[0] not in urls[0], f"MM_URL repeats the literal version: {urls[0]!r}"


def test_micromamba_bootstrap_is_version_pinned_and_per_platform_hashed() -> None:
    _assert_bootstrap_pins(SETUP.read_text(encoding="utf-8"))


def test_bootstrap_pin_gate_rejects_shared_short_and_literal_version_mutations() -> None:
    text = SETUP.read_text(encoding="utf-8")
    digests = _SHA256.findall(text)
    version = _VERSION.findall(text)[0]

    shared = text.replace(digests[1], digests[0])
    with pytest.raises(AssertionError, match="identical"):
        _assert_bootstrap_pins(shared)

    truncated = text.replace(digests[0], digests[0][:-1])
    with pytest.raises(AssertionError, match="sha256 digest"):
        _assert_bootstrap_pins(truncated)

    literal = re.sub(r'^MM_URL="([^"]*)"$',
                     lambda m: 'MM_URL="%s"' % m.group(1).replace("${MM_VERSION}", version),
                     text, flags=re.MULTILINE)
    with pytest.raises(AssertionError, match="interpolate the pinned version"):
        _assert_bootstrap_pins(literal)
