"""Project read declarations remain bounded, exact-file and read-only evidence."""

import json
from types import SimpleNamespace

import pytest
from sicostate import project_root

from sico.core.read_dependencies import MANIFEST, MAX_BYTES, ProjectReadDependencies


@pytest.fixture
def declared(tmp_path):
    file = tmp_path / 'readme.txt'
    file.write_text('independent evidence')
    manifest = project_root(tmp_path, create=True) / MANIFEST
    manifest.parent.mkdir(parents=True)
    raw = json.dumps({'version': 1, 'files': [{'path': 'readme.txt', 'depends_on': []}]}).encode()
    manifest.write_bytes(raw)
    context = SimpleNamespace(snapshot={'cwd': str(tmp_path)})
    return tmp_path, manifest, context, raw


@pytest.mark.parametrize('raw', [
    b'{"version":1,"version":1,"files":[]}',
    b'{"version":1,"files":[{"path":"readme.txt","depends_on":[],"depends_on":["pdk"]}]}',
    b'{"version":NaN,"files":[]}', b'{"version":Infinity,"files":[]}',
    b'{"version":1e9999,"files":[]}', b'[]', b'null', b'{} trailing',
    b'{"version":1,"files":[],"bad":"\xff"}',
    b'{"version":1,"files":' + b'[' * 1200 + b']' * 1200 + b'}',
])
def test_invalid_json_never_supplies_independence(declared, raw):
    root, manifest, context, _ = declared
    manifest.write_bytes(raw)
    resolver = ProjectReadDependencies(root)
    assert resolver({'path': 'readme.txt'}, context) is None
    assert manifest.read_bytes() == raw


def test_byte_limit_accepts_boundary_and_rejects_growth(declared):
    root, manifest, context, raw = declared
    manifest.write_bytes(raw + b' ' * (MAX_BYTES - len(raw)))
    resolver = ProjectReadDependencies(root)
    assert resolver({'path': 'readme.txt'}, context) == frozenset()
    with manifest.open('ab') as stream:
        stream.write(b' ')
    assert resolver({'path': 'readme.txt'}, context) is None
    assert ProjectReadDependencies(root)({'path': 'readme.txt'}, context) is None


def test_file_limit_and_no_partial_admission(declared):
    root, manifest, context, _ = declared
    rows = []
    for index in range(65):
        name = f'evidence-{index}.txt'
        (root / name).write_text('evidence')
        rows.append({'path': name, 'depends_on': ['target']})
    manifest.write_text(json.dumps({'version': 1, 'files': rows[:64]}))
    resolver = ProjectReadDependencies(root)
    assert resolver({'path': rows[63]['path']}, context) == frozenset({'target'})
    assert resolver({'path': rows[64]['path']}, context) is None
    manifest.write_text(json.dumps({'version': 1, 'files': rows}))
    assert resolver({'path': rows[0]['path']}, context) is None
    assert ProjectReadDependencies(root)({'path': rows[0]['path']}, context) is None


@pytest.mark.parametrize('fault', ['self', 'same_file_alias', 'duplicate_dependency', 'missing_file'])
def test_bad_entry_invalidates_whole_declaration(declared, fault):
    root, manifest, context, raw = declared
    value = json.loads(raw)
    if fault == 'self':
        value['files'].append({'path': manifest.relative_to(root).as_posix(), 'depends_on': []})
    elif fault == 'same_file_alias':
        (root / 'alias.txt').symlink_to(root / 'readme.txt')
        value['files'].append({'path': 'alias.txt', 'depends_on': ['pdk']})
    elif fault == 'duplicate_dependency':
        value['files'][0]['depends_on'] = ['pdk', 'pdk']
    else:
        value['files'].append({'path': 'missing.txt', 'depends_on': []})
    manifest.write_text(json.dumps(value))
    before = manifest.read_bytes()
    assert ProjectReadDependencies(root)({'path': 'readme.txt'}, context) is None
    assert manifest.read_bytes() == before


def test_manifest_moved_outside_project_is_not_trusted(declared):
    root, manifest, context, raw = declared
    resolver = ProjectReadDependencies(root)
    assert resolver({'path': 'readme.txt'}, context) == frozenset()
    outside = root.parent / (root.name + '-manifest.json')
    outside.write_bytes(raw)
    manifest.unlink()
    manifest.symlink_to(outside)
    assert resolver({'path': 'readme.txt'}, context) is None
    assert ProjectReadDependencies(root)({'path': 'readme.txt'}, context) is None
    assert outside.read_bytes() == raw


def test_dependency_is_not_a_content_snapshot_or_filesystem_permission(declared):
    root, _, context, _ = declared
    resolver = ProjectReadDependencies(root)
    file = root / 'readme.txt'
    file.write_text('updated independent evidence')
    assert resolver({'path': 'readme.txt'}, context) == frozenset()
    file.unlink()
    assert resolver({'path': 'readme.txt'}, context) is None
    assert resolver({'path': '../outside.txt'}, context) is None
    assert resolver({'path': 'readme.txt'}, SimpleNamespace(snapshot={})) is None


def test_legacy_state_root_is_read_only_and_missing_state_is_not_created(tmp_path):
    context = SimpleNamespace(snapshot={'cwd': str(tmp_path)})
    assert ProjectReadDependencies(tmp_path)({'path': 'readme.txt'}, context) is None
    assert not (tmp_path / '.sico').exists()
    # Explicit historical-state fixture: the resolver does not migrate or write.
    path = tmp_path / '.cad' / MANIFEST
    path.parent.mkdir(parents=True)
    (tmp_path / 'readme.txt').write_text('legacy evidence')
    raw = b'{"version":1,"files":[{"path":"readme.txt","depends_on":[]}]}'
    path.write_bytes(raw)
    assert ProjectReadDependencies(tmp_path)({'path': 'readme.txt'}, context) == frozenset()
    assert path.read_bytes() == raw and not (tmp_path / '.sico').exists()


def test_strict_parser_is_the_existing_shared_object():
    from cadai import json_values
    from sico.core import read_dependencies
    from sico.transport import framing

    assert read_dependencies.strict_json is json_values.strict_json is framing.strict_json
