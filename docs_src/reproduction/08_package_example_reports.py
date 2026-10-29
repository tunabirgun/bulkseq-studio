"""Package the two prepared report examples with their linked files."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / 'docs' / 'examples'
ENTRIES = {'standard': 'results_report.html', 'meta': 'meta_analysis_report.html'}
EXTENSIONS = {'.html', '.csv', '.tsv', '.json', '.yaml', '.yml', '.txt', '.png', '.svg'}


def payload(path):
    data = path.read_bytes()
    return data if path.suffix.lower() == '.png' else data.replace(b'\r\n', b'\n')


for name, report in ENTRIES.items():
    project = EXAMPLES / name
    entry = project / 'results' / 'reports' / report
    if not entry.is_file():
        raise FileNotFoundError(entry)
    files = sorted(path for path in project.rglob('*') if path.is_file())
    for path in files:
        relative = path.relative_to(project)
        if path.is_symlink() or path.suffix.lower() not in EXTENSIONS:
            raise ValueError(f'Unexpected example member: {relative}')
        if any(part.lower() in {'logs', 'tmp', 'planning', '__pycache__'} or part.startswith('.') for part in relative.parts):
            raise ValueError(f'Local working material is not an example export: {relative}')
    bundle = EXAMPLES / f'{name}-example.zip'
    if bundle.exists():
        with ZipFile(bundle) as previous:
            if any(not member.startswith(name + '/') for member in previous.namelist()):
                raise ValueError(f'Existing archive has an unexpected scope: {bundle.name}')
    with ZipFile(bundle, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            info = ZipInfo(f'{name}/{path.relative_to(project).as_posix()}')
            info.create_system = 0
            info.external_attr = 0x20
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, payload(path))
    with ZipFile(bundle) as archive:
        assert len(archive.infolist()) == len(files)
        for path in files:
            member = f'{name}/{path.relative_to(project).as_posix()}'
            assert archive.read(member) == payload(path), member
    print(f'{bundle.name}: {len(files)} verified files')
