"""Release-boundary gates for the published documentation and the README.

tests/test_docs_site.py holds the generated site to its own source: it re-runs
docs_src/build.mjs, compares the result with the committed docs/, resolves every internal
link and image, and checks each page header against the living ``APP_VERSION``. This file
holds the same tree to the *release it claims to document*. ``PUBLIC_VERSION`` below is
that claim, written by hand when a release is cut, so the two gates fail on different
mistakes: a site rebuilt from an un-bumped source fails there, a site whose published
version claim was never advanced fails here, and the README -- which the build never
touches -- is only covered here.

Checks that belong to the generated site alone (build parity, internal links and anchors,
images and their alt text, the per-page label against APP_VERSION) live in
tests/test_docs_site.py and are deliberately not repeated below.
"""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs"
DOCS_SRC = ROOT / "docs_src"
README_PATH = ROOT / "README.md"
# The public software version is a release-time constant, not a derivation: the site is
# rebuilt in the same change as the version bump, and this constant is the specification of
# which release the published site documents. Writing it by hand is what makes a forgotten
# site rebuild fail here instead of shipping -- a derived value would simply follow the bump
# and agree with itself. The tree is otherwise held to one version: the assertions below
# require the site source and APP_VERSION to match this constant, so documenting a release
# older than the tree during a candidate cycle means editing this constant deliberately, not
# deleting that check. The deposited benchmark archive has its own version and must not be
# relabelled when the application advances.
PUBLIC_VERSION = "0.31.0"
ARCHIVE_VERSION = "0.26.6"
CANONICAL_RELEASE_LINK = "https://github.com/tunabirgun/bulkseq-studio/releases/latest"
RELEASE_TAG_LINK = "https://github.com/tunabirgun/bulkseq-studio/releases/tag/v"
SITE_BASE = "https://tunabirgun.github.io/bulkseq-studio/"
REPO_SLUG = "tunabirgun/bulkseq-studio"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _documented_version() -> str:
    """The version the site build stamps into every page header."""
    match = re.search(r"documentedVersion\s*=\s*['\"]([^'\"]+)['\"]", _read(DOCS_SRC / "site-config.mjs"))
    assert match, "docs_src/site-config.mjs does not declare documentedVersion"
    return match.group(1)


def _app_version() -> str:
    match = re.search(r'^APP_VERSION = "([^"]+)"', _read(ROOT / "app" / "constants.py"), re.MULTILINE)
    assert match, "app/constants.py does not declare APP_VERSION"
    return match.group(1)


def _header_label(version: str) -> str:
    """The rendered header label, derived from the build template rather than restated.

    The template is read from docs_src/build.mjs so a change to its wording is visible here
    instead of silently passing a hardcoded copy of last release's label.
    """
    labels = re.findall(r'class="brand-meta">(.*?)</span>', _read(DOCS_SRC / "build.mjs"))
    assert len(labels) == 1, f"expected one brand-meta template in docs_src/build.mjs, found {labels}"
    rendered = re.sub(r"\$\{[^}]*documentedVersion[^}]*\}", version, labels[0])
    assert "${" not in rendered, f"brand-meta template carries an unresolved expression: {labels[0]}"
    assert rendered != labels[0], f"brand-meta template does not interpolate documentedVersion: {labels[0]}"
    return rendered


def _versions_with_scientific_notice(changelog: str) -> list[str]:
    """Returns released versions whose changelog section contains '(*scientific*)'."""
    released_versions = re.findall(r"^## (\d+\.\d+\.\d+)", changelog, re.MULTILINE)
    changelog_sections = re.split(r"^## (?=\d+\.\d+\.\d+)", changelog, flags=re.MULTILINE)
    versions_with_scientific = []
    for i, version in enumerate(released_versions):
        section = changelog_sections[i + 1] if i + 1 < len(changelog_sections) else ""
        if "(*scientific*)" in section:
            versions_with_scientific.append(version)
    return versions_with_scientific


def _latest_scientific_version(public_version: str, changelog: str) -> str | None:
    """The newest released version at or before public_version marked '(*scientific*)'.

    None when the public version is not released yet, or when no released version up to
    it changed scientific output -- in both cases the site owes no notice.
    """
    released_versions = re.findall(r"^## (\d+\.\d+\.\d+)", changelog, re.MULTILINE)
    if public_version not in released_versions:
        return None
    versions_with_scientific = _versions_with_scientific_notice(changelog)
    for version in released_versions[released_versions.index(public_version):]:
        if version in versions_with_scientific:
            return version
    return None


CHANGELOG_TEXT = _read(ROOT / "CHANGELOG.md")
LATEST_SCIENTIFIC_VERSION = _latest_scientific_version(PUBLIC_VERSION, CHANGELOG_TEXT)
# One label for every page; tests/test_docs_site.py separately binds the same label to the
# application's own APP_VERSION, so between them the published claim, the build input and the
# shipped application are held to one version rather than to each other in a loop.
HEADER_LABEL = _header_label(_documented_version())


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.heading_levels: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if values.get("id"):
            self.ids.append(values["id"])
        if re.fullmatch(r"h[1-6]", tag):
            self.heading_levels.append(int(tag[1]))


def _page_sources(overrides: dict[str, str] | None = None) -> dict[str, str]:
    overrides = overrides or {}
    pages = {
        path.name: overrides.get(path.name, _read(path))
        for path in sorted(DOCS_ROOT.glob("*.html"))
    }
    if not pages:
        raise AssertionError("documentation gate found no HTML pages")
    unknown = set(overrides) - set(pages)
    if unknown:
        raise AssertionError(f"documentation overrides named unknown pages: {sorted(unknown)}")
    return pages


def _parser_for(source: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(source)
    parser.close()
    return parser


def _page_errors(pages: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name, source in pages.items():
        parser = _parser_for(source)
        if not source.lstrip().lower().startswith("<!doctype html>"):
            errors.append(f"{name}: missing HTML doctype")
        if source.count(HEADER_LABEL) != 1:
            errors.append(f"{name}: header does not carry exactly one {HEADER_LABEL!r}")
        if "benchmarks.html" in source:
            errors.append(f"{name}: retired benchmark page remains linked")
        if re.search(r"<h[1-6][^>]*>\s*Benchmarks?\s*<", source, re.IGNORECASE):
            errors.append(f"{name}: retired benchmark section remains")
        duplicate_ids = sorted(key for key, count in Counter(parser.ids).items() if count > 1)
        if duplicate_ids:
            errors.append(f"{name}: duplicate element ids {duplicate_ids}")
        for paragraph in re.finditer(r"<p(?:\s[^>]*)?>.*?</p>", source, flags=re.IGNORECASE | re.DOTALL):
            if "\n" in paragraph.group(0):
                line = source[: paragraph.start()].count("\n") + 1
                errors.append(f"{name}:{line}: published paragraph is not one physical line")
        for previous, current in zip(parser.heading_levels, parser.heading_levels[1:]):
            if current > previous + 1:
                errors.append(f"{name}: heading hierarchy skips from h{previous} to h{current}")
    return errors


def _notice_errors(pages: dict[str, str]) -> list[str]:
    """The site must carry a release notice for the newest scientific change it documents.

    The notice lives in the faq.html version-notices list. A reader who followed an older
    version's results needs to be told the numbers moved, so the entry must both name the
    version and reach that release's own record.
    """
    if not LATEST_SCIENTIFIC_VERSION:
        return []
    faq = pages["faq.html"]
    marker = 'id="version-notices"'
    if marker not in faq:
        return [f'faq.html: no {marker} list to carry the {LATEST_SCIENTIFIC_VERSION} notice']
    notices = faq.split(marker, 1)[1].split("</ul>", 1)[0]
    errors = []
    if f"<strong>{LATEST_SCIENTIFIC_VERSION}:</strong>" not in notices:
        errors.append(
            f"faq.html: missing notice for scientific output changes in {LATEST_SCIENTIFIC_VERSION}"
        )
    if f"{RELEASE_TAG_LINK}{LATEST_SCIENTIFIC_VERSION}" not in notices:
        errors.append(
            f"faq.html: the notice does not link to the {LATEST_SCIENTIFIC_VERSION} release record"
        )
    return errors


def _readme_errors(readme: str, pages: dict[str, str]) -> list[str]:
    errors: list[str] = []
    lowered = readme.lower()
    status_lines = [line for line in readme.splitlines() if "release status" in line.lower()]
    if not status_lines:
        errors.append("README.md: no release-status line")
    elif not any(PUBLIC_VERSION in line for line in status_lines):
        errors.append(f"README.md: release-status line does not name the public release {PUBLIC_VERSION}")
    if f"Download public v{PUBLIC_VERSION}" not in readme:
        errors.append(f"README.md: public-download boundary does not name {PUBLIC_VERSION}")
    if CANONICAL_RELEASE_LINK not in readme:
        errors.append("README.md: missing canonical public-release link")
    if "source candidate" in lowered or "source-candidate" in lowered:
        errors.append("README.md: unpublished source-candidate claim remains")

    # The deposited archive is versioned separately; advancing the application must not
    # relabel it. Its citation block is the place that would silently inherit a bump.
    if ARCHIVE_VERSION not in readme:
        errors.append(f"README.md: the deposited archive version {ARCHIVE_VERSION} is not named")
    citations = [b for b in re.findall(r"```[a-z]*\n(.*?)```", readme, flags=re.DOTALL) if "Zenodo" in b]
    if not citations:
        errors.append("README.md: no fenced Zenodo citation block to check the archive version against")
    for block in citations:
        if f"Version {ARCHIVE_VERSION}." not in block:
            errors.append(f"README.md: the archive citation does not name version {ARCHIVE_VERSION}")
        if PUBLIC_VERSION in block:
            errors.append(f"README.md: the archive citation relabels the deposited archive as {PUBLIC_VERSION}")

    # Every link into the published site must land on a page the site actually builds; the
    # page list is read from docs/ so a renamed or dropped chapter fails here.
    for link in re.findall(r"\((%s[^)\s]*)\)" % re.escape(SITE_BASE), readme):
        # The site root is served as index.html, so a bare base URL must find that page. A
        # deep link carries a fragment, which has to name a heading the page actually renders
        # -- resolving only the file would pass a link that lands at the top of the wrong
        # section after a heading is renamed.
        target, _, anchor = link[len(SITE_BASE):].partition("#")
        page = target or "index.html"
        if page not in pages:
            errors.append(f"README.md: link into the site does not resolve: {link}")
        elif anchor and f'id="{anchor}"' not in pages[page]:
            errors.append(f"README.md: link into the site does not resolve: {link}")

    for block in readme.split("\n\n"):
        lines = block.splitlines()
        if len(lines) <= 1:
            continue
        first = lines[0].lstrip()
        if first.startswith(("#", "- ", "* ", ">", "|", "```")):
            continue
        errors.append("README.md: published paragraph is not one physical line")
    return errors


def _validation_errors(
    *,
    page_overrides: dict[str, str] | None = None,
    readme_override: str | None = None,
) -> list[str]:
    pages = _page_sources(page_overrides)
    readme = readme_override if readme_override is not None else _read(README_PATH)
    return _page_errors(pages) + _notice_errors(pages) + _readme_errors(readme, pages)


def test_scientific_notice_derivation_stops_at_the_public_release() -> None:
    """A notice is owed for the newest scientific version the public release contains.

    A scientific change released after PUBLIC_VERSION must not be demanded of a site that
    documents PUBLIC_VERSION, and the walk must not stop at the first unmarked section.
    """
    newer, public, older, unreleased = "99.3.0", "99.2.0", "99.1.0", "99.4.0"
    changelog = f"""# Changelog

## {newer} - 2026-09-20
- **Later change (*scientific*).** Released after the documented version.

## {public} - 2026-09-11
- An interface correction.

## {older} - 2026-09-10
- **Shipped change (*scientific*).** In the documented release.
"""
    assert _versions_with_scientific_notice(changelog) == [newer, older]
    assert _latest_scientific_version(public, changelog) == older
    assert _latest_scientific_version(unreleased, changelog) is None


def test_the_public_release_owes_a_notice_this_tree_must_carry() -> None:
    """Without this, the notice check sits behind a branch that is never taken.

    _latest_scientific_version returns None for an unreleased PUBLIC_VERSION, and the notice
    gate then passes unconditionally. This pins the current tree to the other case.
    """
    released = re.findall(r"^## (\d+\.\d+\.\d+)", CHANGELOG_TEXT, re.MULTILINE)
    assert PUBLIC_VERSION in released, (
        f"PUBLIC_VERSION {PUBLIC_VERSION} has no released CHANGELOG.md section"
    )
    assert LATEST_SCIENTIFIC_VERSION is not None, (
        "no released version up to the public release is marked (*scientific*), so the notice "
        "gate below would pass without checking anything"
    )


def test_the_site_the_application_and_the_release_claim_one_version() -> None:
    documented = _documented_version()
    assert documented == PUBLIC_VERSION, (
        f"docs_src/site-config.mjs documents {documented}, the public release is {PUBLIC_VERSION}"
    )
    assert documented == _app_version(), (
        f"docs_src/site-config.mjs documents {documented}, APP_VERSION is {_app_version()}"
    )


def test_documentation_gate_passes_current_tree() -> None:
    assert _validation_errors() == []


def _replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) >= 1, f"negative-control fixture drifted; missing {old!r}"
    return source.replace(old, new, 1)


@pytest.mark.parametrize(
    ("target", "old", "new", "expected_error"),
    [
        ("index.html", HEADER_LABEL, HEADER_LABEL.replace(PUBLIC_VERSION, "0.0.1"), "header does not carry"),
        ("index.html", "<article>", '<article id="main">', "duplicate element ids"),
        ("index.html", '<p class="lead">', '<p class="lead">\n', "one physical line"),
        ("index.html", 'href="faq.html"', 'href="benchmarks.html"', "retired benchmark page"),
        ("faq.html", '<h2 id="troubleshooting">', '<h3 id="troubleshooting">', "heading hierarchy skips"),
        ("faq.html", '>Common problems<a class="heading-link"', '>Benchmarks<a class="heading-link"', "retired benchmark section remains"),
        ("faq.html", f"<strong>{LATEST_SCIENTIFIC_VERSION}:</strong>", "<strong>Latest:</strong>", "missing notice for scientific output changes"),
        ("faq.html", f"{RELEASE_TAG_LINK}{LATEST_SCIENTIFIC_VERSION}", f"{RELEASE_TAG_LINK}0.0.1", "does not link to the"),
        ("README.md", f"Version {PUBLIC_VERSION} is the current public release.", f"Version {PUBLIC_VERSION} is a source candidate.", "source-candidate claim"),
        ("README.md", f"Version {PUBLIC_VERSION} is the current public release.", "Version 0.30.1 is the current public release.", "release-status line does not name"),
        ("README.md", f"Download public v{PUBLIC_VERSION}", "Download the latest release", "public-download boundary"),
        ("README.md", CANONICAL_RELEASE_LINK, "https://github.com/tunabirgun/bulkseq-studio/releases", "canonical public-release link"),
        ("README.md", "BulkSeq Studio is a cross-platform desktop application ", "BulkSeq Studio is a cross-platform desktop\napplication ", "one physical line"),
        ("README.md", f"({SITE_BASE}guide.html)", f"({SITE_BASE}guide-notes.html)", "link into the site does not resolve"),
        ("README.md", f"({SITE_BASE}faq.html#version-notices)", f"({SITE_BASE}faq.html#no-such-heading)", "link into the site does not resolve"),
        ("README.md", f"archive. Version {ARCHIVE_VERSION}.", f"archive. Version {PUBLIC_VERSION}.", "relabels the deposited archive"),
        ("README.md", f"Version {ARCHIVE_VERSION}. Zenodo.", f"Version {ARCHIVE_VERSION}.", "no fenced Zenodo citation block"),
    ],
    ids=(
        "stale-version-label",
        "duplicate-element-id",
        "hard-wrapped-page-prose",
        "retired-benchmark-link",
        "heading-level-skip",
        "retired-benchmark-section",
        "missing-scientific-notice",
        "notice-without-a-release-link",
        "unpublished-source-candidate",
        "stale-release-status-line",
        "missing-download-boundary",
        "missing-canonical-release-link",
        "hard-wrapped-readme-prose",
        "broken-site-link",
        "broken-site-anchor",
        "relabelled-archive-citation",
        "citation-block-that-stopped-matching",
    ),
)
def test_documentation_gate_rejects_negative_mutations(
    target: str,
    old: str,
    new: str,
    expected_error: str,
) -> None:
    if target == "README.md":
        readme = _replace_once(_read(README_PATH), old, new)
        errors = _validation_errors(readme_override=readme)
    else:
        source = _read(DOCS_ROOT / target)
        errors = _validation_errors(page_overrides={target: _replace_once(source, old, new)})
    assert any(expected_error in error for error in errors), errors


# --- README badges -----------------------------------------------------------------
# The badges state versions that live elsewhere in the tree, so a bump that forgets one
# would leave the front page claiming the previous release's stack. The release badge is
# resolved by shields.io from the GitHub release list and needs no check beyond naming the
# right repository; the rest are literal text and are held to their source here.
BADGE_SOURCES = {
    "python": lambda: re.search(
        r'requires-python\s*=\s*"\s*>=\s*([0-9.]+)"', _read(ROOT / "pyproject.toml")).group(1) + "+",
    "snakemake": lambda: re.search(
        r"^\s*-\s*snakemake-minimal=([0-9][^=\s]*)",
        _read(ROOT / "workflow" / "envs" / "bulkseq.lock.yaml"), re.MULTILINE).group(1),
    "license": lambda: re.search(
        r'license\s*=\s*\{\s*text\s*=\s*"([^"]+)"', _read(ROOT / "pyproject.toml")).group(1),
}


def _badge_value(readme: str, label: str) -> str | None:
    match = re.search(rf"img\.shields\.io/badge/{label}-([^-)\]]+)-", readme)
    return match.group(1).replace("%2B", "+") if match else None


def _badge_errors(readme: str) -> list[str]:
    errors = [
        f"README.md: the {label} badge reads {_badge_value(readme, label)!r}, the tree says {expected()!r}"
        for label, expected in BADGE_SOURCES.items()
        if _badge_value(readme, label) != expected()
    ]
    release = f"img.shields.io/github/v/release/{REPO_SLUG}"
    if release not in readme:
        errors.append(f"README.md: no self-updating release badge for {REPO_SLUG}")
    return errors


def test_readme_badges_match_the_tree_they_describe() -> None:
    assert _badge_errors(_read(README_PATH)) == []


@pytest.mark.parametrize(
    ("old", "new", "expected_error"),
    (
        ("badge/python-3.11%2B-", "badge/python-3.10%2B-", "the python badge reads"),
        ("badge/snakemake-9.23.1-", "badge/snakemake-9.22.0-", "the snakemake badge reads"),
        ("badge/license-MIT-", "badge/license-BSD-", "the license badge reads"),
        (f"img.shields.io/github/v/release/{REPO_SLUG}", "img.shields.io/badge/release-0.31.0-blue",
         "no self-updating release badge"),
    ),
    ids=("stale-python", "stale-snakemake", "wrong-license", "pinned-release-badge"),
)
def test_badge_gate_rejects_a_badge_that_stopped_matching(old: str, new: str, expected_error: str) -> None:
    errors = _badge_errors(_replace_once(_read(README_PATH), old, new))
    assert any(expected_error in error for error in errors), errors
