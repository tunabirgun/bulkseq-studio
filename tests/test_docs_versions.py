"""Check the user-facing tool inventory against the workflow lock."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_science_inventory_matches_documented_release_and_lock():
    data = json.loads((ROOT / 'docs_src/tool-versions.json').read_text(encoding='utf-8'))
    declared = re.search(r"documentedVersion = '([^']+)'", (ROOT / 'docs_src/site-config.mjs').read_text()).group(1)
    assert data['release'] == 'v' + declared
    lock = (ROOT / 'workflow/envs/bulkseq.lock.yaml').read_text(encoding='utf-8')
    pins = dict(re.findall(r'^\s*- ([A-Za-z0-9_.-]+)==([^=\s]+)', lock, re.MULTILINE))
    assert data['tools'] and len({row['package'] for row in data['tools']}) == len(data['tools'])
    for row in data['tools']:
        assert pins[row['package']] == row['version'], row['tool']
