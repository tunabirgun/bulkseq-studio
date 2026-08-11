from __future__ import annotations

from collections import Counter
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pytest
import yaml

from app.constants import APP_VERSION


ROOT = Path(__file__).parents[1]
DOCS_ROOT = ROOT / "docs"
README_PATH = ROOT / "README.md"
# The public version is an external release-state fact, not a mirror of APP_VERSION.
PUBLIC_VERSION = "0.26.6"


def _product_stem(version: str) -> str:
    major, minor, *_ = (int(part) for part in version.split("."))
    return f"product-v{major}{minor:02d}"


PRODUCT_STEM = _product_stem(APP_VERSION)
EXPECTED_FOOTER = """    <div class="footer">
      <p>Free and open-source under the MIT License · <a href="https://github.com/tunabirgun" target="_blank" rel="noopener">tunabirgun</a></p>
    </div>"""


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.references: list[tuple[str, str, str]] = []
        self.images: list[dict[str, str]] = []
        self.footer_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if values.get("id"):
            self.ids.append(values["id"])
        if "footer" in values.get("class", "").split():
            self.footer_count += 1
        for attribute in ("href", "src"):
            if attribute in values:
                self.references.append((tag, attribute, values[attribute]))
        if tag == "img":
            self.images.append(values)


def _catalog_names() -> tuple[str, ...]:
    payload = yaml.safe_load(
        (ROOT / "app" / "data" / "benchmark_datasets.yaml").read_text(encoding="utf-8")
    )
    names = tuple(str(item["name"]) for item in payload.get("benchmarks", []))
    if not names or len(names) != len(set(names)):
        raise AssertionError("benchmark catalog names must be non-empty and unique")
    return names


def _strip_markup(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", "", value)).split())


def _page_sources(overrides: dict[str, str] | None = None) -> dict[str, str]:
    overrides = overrides or {}
    pages = {
        path.name: overrides.get(path.name, path.read_text(encoding="utf-8"))
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


def _local_target(page_name: str, reference: str) -> tuple[Path | None, str]:
    parts = urlsplit(reference)
    if parts.scheme or parts.netloc:
        return None, parts.fragment
    path_part = unquote(parts.path)
    target = (DOCS_ROOT / page_name).parent / path_part if path_part else DOCS_ROOT / page_name
    return target.resolve(), parts.fragment


def _validation_errors(
    *,
    page_overrides: dict[str, str] | None = None,
    readme_override: str | None = None,
) -> list[str]:
    pages = _page_sources(page_overrides)
    readme = readme_override if readme_override is not None else README_PATH.read_text(encoding="utf-8")
    errors: list[str] = []
    parsed = {name: _parser_for(source) for name, source in pages.items()}
    expected_css = f"assets/{PRODUCT_STEM}.css?v=2"
    expected_js = f"assets/{PRODUCT_STEM}.js"
    expected_version_label = f'v{APP_VERSION} source candidate'

    for name, source in pages.items():
        parser = parsed[name]
        if not source.lstrip().lower().startswith("<!doctype html>"):
            errors.append(f"{name}: missing HTML doctype")
        if source.count(expected_css) != 1 or source.count(expected_js) != 1:
            errors.append(f"{name}: product shell must reference exactly {expected_css} and {expected_js}")
        stale_products = {
            match.group(0)
            for match in re.finditer(r"product-v\d+\.(?:css|js)", source)
            if not match.group(0).startswith(PRODUCT_STEM + ".")
        }
        if stale_products:
            errors.append(f"{name}: stale product shell references {sorted(stale_products)}")
        if expected_version_label not in source:
            errors.append(f"{name}: header does not derive from APP_VERSION={APP_VERSION}")
        if "benchmarks.html" in source:
            errors.append(f"{name}: retired benchmark page remains linked")
        if f"Download public v{PUBLIC_VERSION}" not in source:
            errors.append(f"{name}: public-download boundary does not name {PUBLIC_VERSION}")
        if parser.footer_count != 1 or source.count(EXPECTED_FOOTER) != 1:
            errors.append(f"{name}: expected exactly one normalized footer")
        duplicate_ids = sorted(key for key, count in Counter(parser.ids).items() if count > 1)
        if duplicate_ids:
            errors.append(f"{name}: duplicate element ids {duplicate_ids}")
        for paragraph in re.finditer(r"<p(?:\s[^>]*)?>.*?</p>", source, flags=re.IGNORECASE | re.DOTALL):
            if "\n" in paragraph.group(0):
                line = source[: paragraph.start()].count("\n") + 1
                errors.append(f"{name}:{line}: published paragraph is not one physical line")
        for image in parser.images:
            if not image.get("alt", "").strip():
                errors.append(f"{name}: image {image.get('src', '<missing src>')} has no alt text")
            if image.get("src", "").endswith("screenshot-linux.png") and APP_VERSION in image.get("alt", ""):
                errors.append(f"{name}: relabels the earlier Linux screenshot as {APP_VERSION} evidence")

    docs_resolved = DOCS_ROOT.resolve()
    for name, parser in parsed.items():
        for tag, attribute, reference in parser.references:
            if not reference:
                errors.append(f"{name}: empty {attribute} on <{tag}>")
                continue
            if reference.lower().startswith("javascript:"):
                errors.append(f"{name}: javascript URL is not allowed: {reference}")
                continue
            target, fragment = _local_target(name, reference)
            if target is None:
                continue
            if target != docs_resolved and docs_resolved not in target.parents:
                errors.append(f"{name}: local reference escapes docs/: {reference}")
                continue
            if not target.is_file():
                errors.append(f"{name}: missing local reference {reference}")
                continue
            if fragment and target.suffix.lower() == ".html":
                target_source = pages.get(target.name, target.read_text(encoding="utf-8"))
                if fragment not in _parser_for(target_source).ids:
                    errors.append(f"{name}: unresolved fragment {reference}")

    for css_name in ("site.css", f"{PRODUCT_STEM}.css"):
        css_path = DOCS_ROOT / "assets" / css_name
        if not css_path.is_file():
            errors.append(f"assets/{css_name}: missing stylesheet")
            continue
        css = css_path.read_text(encoding="utf-8")
        for raw in re.findall(r"url\(([^)]+)\)", css):
            reference = raw.strip().strip("'\"")
            if not reference or reference.startswith(("data:", "http:", "https:", "#")):
                continue
            target = (css_path.parent / unquote(urlsplit(reference).path)).resolve()
            if target != docs_resolved and docs_resolved not in target.parents:
                errors.append(f"assets/{css_name}: CSS reference escapes docs/: {reference}")
            elif not target.is_file():
                errors.append(f"assets/{css_name}: missing CSS reference {reference}")

    release_sources = {
        "README.md": readme,
        "index.html": pages["index.html"],
        "guide.html": pages["guide.html"],
        "faq.html": pages["faq.html"],
    }
    forbidden_acceptance = (
        rf"{re.escape(APP_VERSION)} has five verified local acceptance-test artifacts",
        rf"five {re.escape(APP_VERSION)} Windows and native-Linux artifacts passed",
        rf"verified local {re.escape(APP_VERSION)} acceptance set",
        rf"{re.escape(APP_VERSION)} AppImage running natively",
    )
    for name, source in release_sources.items():
        lowered = source.lower()
        if APP_VERSION not in source or "source candidate" not in lowered:
            errors.append(f"{name}: missing {APP_VERSION} source-candidate boundary")
        if PUBLIC_VERSION not in source:
            errors.append(f"{name}: missing public {PUBLIC_VERSION} boundary")
        if not re.search(rf"no {re.escape(APP_VERSION)}[^\n<]*(?:package|Windows|installer|AppImage)[^\n<]*(?:built|accepted)", source, re.IGNORECASE):
            errors.append(f"{name}: missing explicit package-build or acceptance boundary")
        if any(re.search(pattern, source, re.IGNORECASE) for pattern in forbidden_acceptance):
            errors.append(f"{name}: forbidden package-acceptance claim for {APP_VERSION}")

    canonical_names = _catalog_names()
    preset_sources = {
        "README.md": readme,
        "guide.html": pages["guide.html"],
    }
    for name, source in preset_sources.items():
        for preset in canonical_names:
            if preset not in source:
                errors.append(f"{name}: missing canonical preset {preset}")
        plain = (
            " ".join(source.replace("*", "").replace("`", "").split())
            if name.endswith(".md")
            else _strip_markup(source)
        )
        if not all(token in plain for token in ("N-terminal", "truncation", "rpd3-delta background")):
            errors.append(f"{name}: missing UME6 provenance for the N-terminal truncation and rpd3-delta background")
        if "not a complete UME6 gene deletion" not in plain:
            errors.append(f"{name}: missing UME6 provenance that this is not a complete deletion")
        for fact in ("467", "61", "85", "FDR-selected", "raw |log2 fold change| ≥ 1"):
            if fact not in plain:
                errors.append(f"{name}: missing Pasilla source-result distinction {fact}")

    for block in readme.split("\n\n"):
        lines = block.splitlines()
        if len(lines) <= 1:
            continue
        first = lines[0].lstrip()
        if first.startswith(("#", "- ", "* ", ">", "|", "```")):
            continue
        errors.append("README.md: published paragraph is not one physical line")

    return errors


def test_documentation_gate_passes_current_tree() -> None:
    assert _validation_errors() == []


def _replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) >= 1, f"negative-control fixture drifted; missing {old!r}"
    return source.replace(old, new, 1)


@pytest.mark.parametrize(
    ("target", "old", "new", "expected_error"),
    [
        ("index.html", f"assets/{PRODUCT_STEM}.css?v=2", "assets/product-v027.css?v=2", "product shell"),
        ("index.html", EXPECTED_FOOTER, EXPECTED_FOOTER + "\n" + EXPECTED_FOOTER, "normalized footer"),
        ("index.html", "assets/bulkseq_logo.svg", "assets/does-not-exist.svg", "missing local reference"),
        ("guide.html", "Rice CY1000 salt-stress paired-end subset", "Rice preset removed", "missing canonical preset"),
        ("guide.html", "not a complete <em>UME6</em> gene deletion", "a complete <em>UME6</em> gene deletion", "not a complete deletion"),
        ("index.html", '<li><a href="faq.html">FAQ &amp; cite</a></li>', '<li><a href="benchmarks.html">Benchmarks</a></li>', "retired benchmark page"),
        ("index.html", "<p class=\"lead\">BulkSeq Studio is for biologists", "<p class=\"lead\">BulkSeq Studio is for\nbiologists", "one physical line"),
        ("guide.html", "</section>", f'<img src="assets/screenshot-linux.png" alt="BulkSeq Studio {APP_VERSION} AppImage">\n</section>', "relabels the earlier Linux screenshot"),
        ("README.md", f"Version {APP_VERSION} is a local source candidate under validation.", f"Version {APP_VERSION} has five verified local acceptance-test artifacts.", "forbidden package-acceptance"),
    ],
    ids=(
        "stale-product-shell",
        "duplicate-footer",
        "missing-asset",
        "missing-canonical-preset",
        "wrong-ume6-provenance",
        "retired-benchmark-link",
        "hard-wrapped-prose",
        "fake-linux-screenshot",
        "unsupported-package-acceptance",
    ),
)
def test_documentation_gate_rejects_negative_mutations(
    target: str,
    old: str,
    new: str,
    expected_error: str,
) -> None:
    if target == "README.md":
        readme = _replace_once(README_PATH.read_text(encoding="utf-8"), old, new)
        errors = _validation_errors(readme_override=readme)
    else:
        source = (DOCS_ROOT / target).read_text(encoding="utf-8")
        errors = _validation_errors(page_overrides={target: _replace_once(source, old, new)})
    assert any(expected_error in error for error in errors), errors
