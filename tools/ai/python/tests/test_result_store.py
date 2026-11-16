"""Shared result persistence and workspace-independent, read-only archive access."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cadai import result_store, result_tools
from cadai.result_contract import ResultError, canonical, digest
from sico.storage.workbench import WorkbenchIndex, content_digest


def archived(tmp_path, prefix='results'):
    value = {'schema': result_store.ARTIFACT_SCHEMAS[prefix], 'rows': [{'value': 1}]}
    store = result_store.ResultStore(tmp_path)
    ref, artifact = store.put(prefix, value)
    return store, ref, artifact, value


def index_for(state, ref, artifact):
    index = WorkbenchIndex(SimpleNamespace(root=state / 'ai/agent', session_id='session'))
    value = {'data': {'result_ref': ref, 'artifact': artifact}}
    index.data['saved'] = {'value': value, 'data_type': 'tool',
                           'digest': content_digest({'value': value, 'inputs': {}})}
    return index


def test_historical_exports_retain_identity():
    assert result_tools.ResultStore is result_store.ResultStore
    assert result_tools.ARTIFACT_SCHEMAS is result_store.ARTIFACT_SCHEMAS
    assert result_tools.MAX_BYTES is result_store.MAX_BYTES


@pytest.mark.parametrize('prefix', tuple(result_store.ARTIFACT_SCHEMAS))
def test_all_archived_kinds_share_integrity_checks_without_reselecting_workspace(tmp_path, monkeypatch, prefix):
    store, ref, artifact, value = archived(tmp_path, prefix)
    state = Path(artifact['path']).parents[2]
    index = index_for(state, ref, artifact)
    original = deepcopy(index.data)
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in state.rglob('*') if p.is_file()}
    # Workbench reads the admitted archive even with an unrelated/invalid ambient state setting.
    monkeypatch.setenv('SICO_TEMP_DIR', '/must-not-be-selected/.sico')
    monkeypatch.setenv('CAD_TEMP_DIR', '/must-not-be-selected/.cad')
    read = index.material('saved')
    assert read == value
    read['rows'].clear()
    assert result_store.read_archived_result(state, ref) == value
    assert index.data == original
    assert before == {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in state.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['missing', 'symlink', 'directory', 'json', 'digest', 'schema', 'oversize'])
def test_read_and_write_owners_reject_the_same_damaged_artifact(tmp_path, monkeypatch, damage):
    store, ref, artifact, _ = archived(tmp_path)
    path = Path(artifact['path'])
    state = path.parents[2]
    if damage in {'missing', 'symlink', 'directory'}:
        original = path.read_bytes()
        path.unlink()
        if damage == 'symlink':
            outside = tmp_path / 'outside.json'
            outside.write_bytes(original)
            path.symlink_to(outside)
        elif damage == 'directory':
            path.mkdir()
    elif damage == 'json':
        path.write_text('{invalid')
    elif damage == 'digest':
        path.write_text('{}')
    elif damage == 'schema':
        value = {'schema': 'unregistered'}
        ref = 'results_' + digest(value)
        path.with_name(ref + '.json').write_bytes(canonical(value))
    else:
        monkeypatch.setattr(result_store, 'MAX_BYTES', path.stat().st_size - 1)
    messages = []
    for operation in (lambda: store.get(ref), lambda: result_store.read_archived_result(state, ref)):
        with pytest.raises(ResultError) as caught:
            operation()
        messages.append(str(caught.value))
    assert messages[0] == messages[1]


@pytest.mark.parametrize('component', ['ai', 'measurement-results'])
@pytest.mark.parametrize('damage', ['missing', 'symlink'])
def test_archive_directory_is_never_created_or_followed(tmp_path, component, damage):
    _, ref, artifact, _ = archived(tmp_path)
    state = Path(artifact['path']).parents[2]
    directory = state / 'ai'
    if component == 'measurement-results':
        directory /= component
    moved = tmp_path / 'moved'
    directory.rename(moved)
    if damage == 'symlink':
        directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match='Result archive directory is unavailable'):
        result_store.read_archived_result(state, ref)
    assert directory.is_symlink() if damage == 'symlink' else not directory.exists()


def test_invalid_reference_does_not_create_state(tmp_path):
    for ref in ('../escape', 'results_' + '0' * 63, 'unknown_' + '0' * 64):
        with pytest.raises(ResultError, match='invalid result reference'):
            result_store.ResultStore(tmp_path).get(ref)
        with pytest.raises(ResultError, match='invalid result reference'):
            result_store.read_archived_result(tmp_path, ref)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('change', ['relative', 'foreign', 'filename', 'reference_digest', 'snapshot'])
def test_material_requires_registered_path_and_unchanged_evidence(tmp_path, change):
    _, ref, artifact, _ = archived(tmp_path)
    state = Path(artifact['path']).parents[2]
    if change == 'relative':
        artifact['path'] = Path(artifact['path']).name
    elif change == 'foreign':
        artifact['path'] = str(tmp_path / Path(artifact['path']).name)
    elif change == 'filename':
        artifact['path'] += '.other'
    elif change == 'reference_digest':
        artifact['sha256'] = '0' * 64
    index = index_for(state, ref, artifact)
    if change == 'snapshot':
        index.data['saved']['value']['data']['artifact']['bytes'] += 1
    with pytest.raises(ValueError):
        index.material('saved')


def test_non_result_evidence_remains_without_material(tmp_path):
    index = index_for(tmp_path, 'other_123', {'path': '/not/a/result', 'sha256': 'unknown'})
    assert index.material('saved') is None
