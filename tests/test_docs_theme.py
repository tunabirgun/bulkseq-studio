"""Appearance controls are available consistently across the documentation."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_pages_have_text_controls_and_early_preference_script():
    pages = list((ROOT / 'docs').glob('*.html'))
    assert pages
    for path in pages:
        source = path.read_text(encoding='utf-8')
        assert not re.search(r'id="theme-trigger(?:-sidebar)?"', source), path
        assert source.index('src="assets/appearance.js"') < source.index('rel="stylesheet"'), path
        for appearance in ('light', 'dark'):
            assert source.count(f'data-appearance="{appearance}"') == 1, path
        assert 'role="group" aria-label="Appearance"' in source, path
        assert 'theme-sun' not in source and 'theme-moon' not in source, path


def test_footer_is_the_requested_credit():
    for path in (ROOT / 'docs').glob('*.html'):
        source = path.read_text(encoding='utf-8')
        footers = re.findall(r'<footer[^>]*>(.*?)</footer>', source)
        assert len(footers) == 1, path
        assert re.sub('<[^>]*>', '', footers[0]) == 'Built by @tunabirgun, for research use only.'
        assert 'href="https://github.com/tunabirgun"' in footers[0]
