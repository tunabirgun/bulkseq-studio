"""Check the figure explorers against their source fixtures."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_browser_network_statistics_match_union_find():
    script = """
const c=require('./docs_src/explorer-core.cjs'),d=require('./docs_src/network-example.json');
console.log(JSON.stringify(Array.from({length:61},(_,i)=>{const cutoff=400+10*i,s=c.graphStats(d,cutoff);return {cutoff,edges:s.edges.length,components:s.components,isolates:s.isolates,degrees:d.nodes.map(n=>s.neighbors.get(n.id).size)}})));
"""
    result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True, check=True)
    data = json.loads((ROOT / 'docs_src/network-example.json').read_text())
    for state in json.loads(result.stdout):
        parent = {node['id']: node['id'] for node in data['nodes']}
        degree = dict.fromkeys(parent, 0)

        def find(node):
            while parent[node] != node:
                node = parent[node]
            return node

        edges = [edge for edge in data['edges'] if edge['score'] >= state['cutoff']]
        for edge in edges:
            source, target = edge['source'], edge['target']
            parent[find(source)] = find(target)
            degree[source] += 1
            degree[target] += 1
        assert state['edges'] == len(edges)
        assert state['components'] == len({find(node) for node in parent})
        assert state['isolates'] == sum(value == 0 for value in degree.values())
        assert state['degrees'] == [degree[node['id']] for node in data['nodes']]


def test_explorers_are_scoped_and_data_is_not_search_prose():
    for path in (ROOT / 'docs').glob('*.html'):
        text = path.read_text(encoding='utf-8')
        assert ('src="assets/explorers.js"' in text) == (path.stem in {'science', 'outputs'})
    index = (ROOT / 'docs/assets/search-index.js').read_text(encoding='utf-8')
    pages = json.loads(index.split('window.manualSearch = ', 1)[1].split(';\nwindow.manualGlossary', 1)[0])
    for page in pages:
        assert '"ciLow"' not in page['text']
        assert '"querySymbols"' not in page['text']
