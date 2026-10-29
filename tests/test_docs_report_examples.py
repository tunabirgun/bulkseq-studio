"""The published examples must remain usable report exports with local support files."""
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'docs' / 'examples'
REPORTS = {'standard': 'results_report.html', 'meta': 'meta_analysis_report.html'}


class Report(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids, self.text = [], set(), []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style'}:
            self.skip += 1
        self.ids.add(attrs.get('id', ''))
        self.links.extend(attrs[key] for key in ('src', 'href') if attrs.get(key))

    def handle_endtag(self, tag):
        if tag in {'script', 'style'} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


@pytest.mark.parametrize('kind', REPORTS)
def test_example_report_links_and_scope(kind):
    project = EXAMPLES / kind
    path = project / 'results' / 'reports' / REPORTS[kind]
    source = path.read_text(encoding='utf-8')
    report = Report()
    report.feed(source)
    assert 'synthetic' in ' '.join(report.text).lower()
    assert 'bsq-lb' in report.ids
    assert not re.search(r'(?i)(?:file:///|[A-Z]:[\\/]Users[\\/]|/home/[^/\s]+/|/mnt/[a-z]/Users/)', source)
    for link in report.links:
        parts = urlsplit(link)
        if parts.scheme or parts.netloc:
            continue
        target = (path.parent / unquote(parts.path)).resolve() if parts.path else path.resolve()
        assert target.is_relative_to(project.resolve()), link
        assert target.is_file(), link
        if parts.fragment and target == path.resolve():
            assert unquote(parts.fragment) in report.ids, link


@pytest.mark.parametrize('kind', REPORTS)
def test_example_bundle_matches_reviewed_project(kind):
    project = EXAMPLES / kind
    files = {f'{kind}/{p.relative_to(project).as_posix()}': p for p in project.rglob('*') if p.is_file()}
    assert files
    with ZipFile(EXAMPLES / f'{kind}-example.zip') as archive:
        assert set(archive.namelist()) == set(files)
        assert archive.testzip() is None
        assert not archive.comment
        for name, path in files.items():
            expected = path.read_bytes()
            if path.suffix.lower() != '.png':
                expected = expected.replace(b'\r\n', b'\n')
            assert archive.read(name) == expected, name
            assert not archive.getinfo(name).extra
            assert not archive.getinfo(name).comment
