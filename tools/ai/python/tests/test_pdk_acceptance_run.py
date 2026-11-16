"""Verifier snapshots bind operations across overlays, sources, sharding and moves."""

from copy import deepcopy
from pathlib import Path
import json
import shutil

import pytest
from pdk_publication_fixture import acceptance_evidence, ready_candidate
from pdk_standard_fixture import replace, update
from sicostate import project_directory

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.acceptance_run import begin, finish, evidence
from cadai.pdk_standard.acceptance_subject import capture, data_digest, device_dependencies
from cadai.pdk_standard.publication import publish, require
from cadai.pdk_standard.receipts import OPERATIONS
from cadai.pdk_standard.workspace import select


def passed():
    return {'cell0': {operation: 'passed' for operation in OPERATIONS}}


def complete(package, snapshot, validator=lambda _: None):
    return finish(package, snapshot, passed(), at='2026-10-14', run_ref='run:synthetic',
                  source_validator=validator)


def test_run_retains_pre_operation_snapshot_and_checks_sources_twice(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    seen = []
    validator = lambda package: seen.append(package.revision)
    snapshot = begin(current, ['cell0'], validator)
    row = complete(select(tmp_path, 'synthetic', {}), snapshot, validator)
    assert seen == [current.revision, current.revision]
    assert row['subject'] == snapshot['subject'] and row['results'] == passed()
    assembled = evidence([row])
    row['subject']['revision'] = 'changed-after-compilation'
    assert assembled['runs'][0]['subject'] == snapshot['subject']


@pytest.mark.parametrize('change', ['content', 'dependency'])
def test_run_changed_before_completion_never_gets_a_new_identity(tmp_path, change):
    current, _, _ = ready_candidate(tmp_path)
    snapshot = begin(current, ['cell0'], lambda _: None)
    original = deepcopy(snapshot)
    if change == 'content':
        updates, preview = update(tmp_path, current, [replace('/parameters/w/domain/max', 3e-6)],
                                  'cell0/cdf.json')
        updates.apply({'update_ref': preview['update_ref']})
        current = select(tmp_path, 'synthetic', {})
    else:
        current.manifest['dependencies']['cell0_cdf']['fingerprint'] = 'sha256:' + '0' * 64
    with pytest.raises(PdkUnavailable, match='changed'):
        complete(current, snapshot)
    assert snapshot == original


def test_source_validation_failure_cannot_produce_a_completed_run(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    snapshot = begin(current, ['cell0'], lambda _: None)
    def changed(_):
        raise PdkUnavailable('pdk_source_changed', 'source changed')
    with pytest.raises(PdkUnavailable, match='source changed'):
        complete(current, snapshot, changed)
    with pytest.raises(PdkUnavailable, match='independent source'):
        begin(current, ['cell0'], None)


def test_dependency_ids_are_opaque_and_shared_inputs_are_bound_once(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    from cadai.pdk_standard.ingest import documents
    content = documents(current)
    original = capture(current)
    dependency = current.manifest['dependencies'].pop('cell0_cdf')
    current.manifest['dependencies']['arbitrary_ref'] = dependency
    digest = 'sha256:' + 'c' * 64
    for index in range(120):
        current.manifest['dependencies']['model_include_' + str(index)] = {
            'kind': 'model', 'target': 'resource', 'method': 'sha256-bytes-v1', 'fingerprint': digest}
    def renamed(name):
        value = deepcopy(content[name])
        value['depends_on'] = ['arbitrary_ref' if ref == 'cell0_cdf' else ref
                               for ref in value.get('depends_on', [])]
        return value
    current.document = renamed
    actual = device_dependencies(current, 'cell0')
    assert actual['arbitrary_ref'] == dependency['fingerprint']
    assert 'cell0_cdf' not in actual
    assert not any(ref.startswith('model_include') for ref in actual)
    assert capture(current)['content_digest'] != original['content_digest']
    from cadai.pdk_standard.jsonio import encode, TARGET
    assert len(encode(actual)) < TARGET


def test_effective_overlay_identity_survives_publication_sharding_and_relocation(tmp_path):
    generation = tmp_path / 'generation'
    current, _, _ = ready_candidate(generation, auxiliary=160, dependency_count=80)
    original = capture(current)
    assert original['content_digest'] != data_digest(current.manifest)
    path = Path(acceptance_evidence(generation))
    document = json.loads(path.read_text())
    document['runs'] *= 25
    path.write_text(json.dumps(document))
    publish(generation, 'synthetic', current.revision, {}, acceptance=str(path))
    published = select(generation, 'synthetic', {})
    assert original['content_digest'] == data_digest(published.manifest)
    assert any(name.startswith('package.acceptance.') for name in published.manifest['files'])
    target = tmp_path / 'moved'
    shutil.copytree(project_directory(generation, 'ai/pdk-data'), target)
    moved = select(tmp_path, 'synthetic', {'SICO_PDK_DATA': str(target)})
    require(moved)
    assert data_digest(moved.manifest) == original['content_digest']


def test_design_qualification_uses_inventory_without_rescanning_all_details(tmp_path, monkeypatch):
    current, _, _ = ready_candidate(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {}, acceptance=acceptance_evidence(tmp_path))
    current = select(tmp_path, 'synthetic', {})
    original = current.document
    def bounded(name):
        assert name == 'device.json', 'design qualification expanded device details: ' + name
        return original(name)
    monkeypatch.setattr(current, 'document', bounded)
    require(current)


@pytest.mark.parametrize('when', ['before', 'after'])
def test_shared_byte_changes_are_checked_even_without_a_device_reference(tmp_path, when):
    from cadai.pdk_standard.jsonio import sha
    current, _, _ = ready_candidate(tmp_path)
    resource = tmp_path / 'model.scs'
    resource.write_bytes(b'model original\n')
    current.roots['test'] = str(tmp_path)
    current.manifest['dependencies']['shared_model'] = {
        'kind': 'model', 'target': 'shared', 'method': 'sha256-bytes-v1',
        'fingerprint': sha(resource.read_bytes())}
    original = current.document
    def document(name):
        value = deepcopy(original(name))
        if name == 'file.json':
            value['items']['shared'] = {'kind': 'model', 'root': 'test',
                'path': resource.name, 'purpose': 'test', 'dependency': 'shared_model'}
        return value
    current.document = document
    if when == 'after':
        snapshot = begin(current, ['cell0'], lambda _: None)
    resource.write_bytes(b'model changed\n')
    with pytest.raises(PdkUnavailable, match='Source resource changed'):
        if when == 'before':
            begin(current, ['cell0'], lambda _: None)
        else:
            complete(current, snapshot)
