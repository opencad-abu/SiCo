"""Acceptance receipts gate design readiness and expire with content or sources."""

import hashlib
import json

import pytest
from pdk_publication_fixture import acceptance_evidence, ready_candidate
from pdk_standard_fixture import replace, update
from sicostate import project_directory

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard import gate
from cadai.pdk_standard.publication import publish
from cadai.pdk_standard.query import Queries
from cadai.pdk_standard.receipts import OPERATIONS, SCHEMA, VERIFIER
from cadai.pdk_standard.service import StandardData
from cadai.pdk_standard.workspace import select


def evidence(path, runs, verifier=VERIFIER):
    from cadai.pdk_standard.acceptance_subject import capture, device_dependencies
    current = select(path.parent, 'synthetic', {})
    subject = capture(current)
    # Synthetic gate records; no real operations are claimed by this fixture.
    for row in runs:
        row['subject'] = subject
        row['fingerprints'] = {device: device_dependencies(current, device) if device == 'cell0'
                               else {'ghost_source': 'sha256:' + '0' * 64} for device in row['devices']}
    path.write_text(json.dumps({'schema_version': SCHEMA, 'verifier': verifier, 'runs': runs}))
    return str(path)


def run(devices, operations=None, **extra):
    operations = list(operations if operations is not None else OPERATIONS)
    return {'operations': operations,
            'devices': list(devices), 'at': '2026-10-11T00:00:00Z',
            'results': {device: {operation: 'passed' for operation in operations} for device in devices},
            'run_ref': 'acceptance:test', 'evidence_ref': 'acceptance:test', **extra}


def test_publication_requires_recorded_evidence(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    index = project_directory(tmp_path, 'ai/pdk-data') / 'index.json'
    before = index.read_bytes()
    with pytest.raises(PdkUnavailable) as error:
        publish(tmp_path, 'synthetic', current.revision, {})
    assert error.value.code == 'pdk_acceptance_required'
    assert index.read_bytes() == before


def test_incomplete_operation_coverage_keeps_the_index(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    path = evidence(tmp_path / 'evidence.json',
                    [run(['cell0'], operations=['creation', 'wiring'])])
    index = project_directory(tmp_path, 'ai/pdk-data') / 'index.json'
    before = index.read_bytes()
    with pytest.raises(PdkUnavailable) as error:
        publish(tmp_path, 'synthetic', current.revision, {}, acceptance=path)
    assert error.value.code == 'pdk_acceptance_incomplete'
    assert 'cell0:netlist' in str(error.value) and 'cell0:simulation_config' in str(error.value)
    assert index.read_bytes() == before


def test_declared_devices_cannot_be_left_uncovered():
    from cadai.pdk_standard.receipts import gaps

    def allowed():
        return {'use': {'circuit': 'allow', 'testbench': 'deny', 'extraction': 'unknown',
                        'verification': 'unknown'}, 'dir': 'device'}

    class _Stub:
        def document(self, name):
            assert name == 'device.json'
            return {'items': {'cell0': allowed(), 'cell1': allowed(), 'denied': {
                **allowed(), 'use': {**allowed()['use'], 'circuit': 'deny'}}}}

    block = {'operations': list(OPERATIONS),
             'receipts': {'run_01_ab': run(['cell0'])}}
    missing = gaps(_Stub(), block)
    assert {device for device, _ in missing} == {'cell1'}
    assert len(missing) == len(OPERATIONS)


def test_unregistered_verifier_is_rejected(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    path = evidence(tmp_path / 'evidence.json', [run(['cell0'])], verifier='anonymous')
    with pytest.raises(PdkUnavailable) as error:
        publish(tmp_path, 'synthetic', current.revision, {}, acceptance=path)
    assert error.value.code == 'unsupported_pdk_standard'
    assert 'verifier' in str(error.value)


def test_unknown_devices_cannot_be_covered(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    path = evidence(tmp_path / 'evidence.json', [run(['cell0', 'ghost'])])
    with pytest.raises(PdkUnavailable, match='unknown or non-allowed'):
        publish(tmp_path, 'synthetic', current.revision, {}, acceptance=path)


def test_publication_records_the_bound_receipts(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    path = evidence(tmp_path / 'evidence.json', [run(['cell0'])])
    result = publish(tmp_path, 'synthetic', current.revision, {}, acceptance=path)
    assert result['design_ready'] and result['acceptance'] == {
        'verifier': VERIFIER, 'receipts': 1, 'devices': 1}
    published = select(tmp_path, 'synthetic', {})
    acceptance = published.manifest['publication']['acceptance']
    assert acceptance['operations'] == list(OPERATIONS)
    assert acceptance['evidence_ref'] == path
    assert acceptance['evidence_digest'] == 'sha256:' + hashlib.sha256(
        (tmp_path / 'evidence.json').read_bytes()).hexdigest()
    receipt = next(iter(acceptance['receipts'].values()))
    fingerprints = receipt['fingerprints']['cell0']
    assert set(fingerprints) >= {'cell0_cdf', 'cell0_symbol'}
    assert all(digest.startswith('sha256:') for digest in fingerprints.values())


def test_content_change_expires_the_acceptance(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {},
            acceptance=evidence(tmp_path / 'evidence.json', [run(['cell0'])]))
    published = select(tmp_path, 'synthetic', {})
    from cadai.pdk_standard.publication import require
    require(published)
    updates, preview = update(tmp_path, published, [replace('/items/cell0/reason', 'edited')])
    updates.apply({'update_ref': preview['update_ref']})
    moved = select(tmp_path, 'synthetic', {})
    # A workspace overlay invalidates the publication until it is republished,
    # so the acceptance cannot silently carry over to the changed content.
    with pytest.raises(PdkUnavailable) as error:
        require(moved)
    assert error.value.code == 'pdk_generation_incomplete'
    response = Queries().read(moved, {})
    assert response['design_ready'] is False
    assert response['code'] == 'pdk_generation_incomplete'


def test_tampered_fingerprints_are_detected(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {},
            acceptance=evidence(tmp_path / 'evidence.json', [run(['cell0'])]))
    index = json.loads((project_directory(tmp_path, 'ai/pdk-data') / 'index.json').read_text())
    manifest_path = (project_directory(tmp_path, 'ai/pdk-data')
                     / index['packages']['synthetic']['path'] / 'package.json')
    manifest = json.loads(manifest_path.read_text())
    receipt = next(iter(manifest['publication']['acceptance']['receipts'].values()))
    receipt['fingerprints']['cell0']['cell0_cdf'] = 'sha256:' + '0' * 64
    manifest_path.write_text(json.dumps(manifest))
    published = select(tmp_path, 'synthetic', {})
    from cadai.pdk_standard.publication import require
    with pytest.raises(PdkUnavailable, match='stale'):
        require(published)


def test_publication_without_receipts_is_readable_but_not_design_ready(tmp_path):
    current, bridge, device = ready_candidate(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {},
            acceptance=evidence(tmp_path / 'evidence.json', [run(['cell0'])]))
    index = json.loads((project_directory(tmp_path, 'ai/pdk-data') / 'index.json').read_text())
    manifest_path = (project_directory(tmp_path, 'ai/pdk-data')
                     / index['packages']['synthetic']['path'] / 'package.json')
    manifest = json.loads(manifest_path.read_text())
    manifest['required_features'] = ['qualified-publication-v1']
    del manifest['publication']['acceptance']
    manifest_path.write_text(json.dumps(manifest))
    readable = select(tmp_path, 'synthetic', {})
    result = Queries().read(readable, {})
    assert result['items'] == [] and result['design_ready'] is False
    assert result['code'] == 'pdk_acceptance_required'
    service = StandardData(tmp_path, {})
    with pytest.raises(PdkUnavailable) as error:
        gate.obtain(service, device)
    assert error.value.code == 'pdk_acceptance_required'


def test_accepted_publication_survives_relocation(tmp_path):
    import shutil

    generation = tmp_path / 'generation'
    current, _, _ = ready_candidate(generation)
    publish(generation, 'synthetic', current.revision, {},
            acceptance=acceptance_evidence(generation))
    data = project_directory(generation, 'ai/pdk-data')
    relocated = tmp_path / 'relocated'
    shutil.copytree(data, relocated / 'data')
    from cadai.pdk_standard.publication import require
    moved = select(tmp_path, 'synthetic', {'SICO_PDK_DATA': str(relocated / 'data')})
    require(moved)
    assert moved.manifest['publication']['acceptance']['receipts']
    assert Queries().read(moved, {})['design_ready'] is True


def test_publication_records_the_data_only_scope(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    result = publish(tmp_path, 'synthetic', current.revision, {},
                     acceptance=acceptance_evidence(tmp_path),
                     unfinished=['iv', 'all_corner_scans'])
    scope = result['scope']
    assert scope['covers'] == 'data'
    assert scope['devices'] == 1 and scope['libraries'] == ['synthetic']
    assert scope['simulators'] == ['spectre']
    assert scope['unfinished'] == ['all_corner_scans', 'iv']
    published = select(tmp_path, 'synthetic', {})
    assert published.manifest['publication']['scope'] == scope


def test_interface_free_devices_declare_the_registered_targets(tmp_path):
    from cadai.pdk_standard.simulation_interfaces import TARGETS

    current, _, _ = ready_candidate(tmp_path, interface_free=True)
    result = publish(tmp_path, 'synthetic', current.revision, {},
                     acceptance=acceptance_evidence(tmp_path))
    assert result['scope']['simulators'] == sorted(TARGETS)
    assert result['scope']['devices'] == 1
    assert result['scope']['corners'] == []


def test_large_receipt_sets_are_sharded_below_the_file_limit(tmp_path):
    from cadai.pdk_standard.jsonio import LIMIT, encode
    from cadai.pdk_standard.publication import require

    current, _, _ = ready_candidate(tmp_path)
    path = evidence(tmp_path / 'big.json', [run(['cell0']) for _ in range(60)])
    publish(tmp_path, 'synthetic', current.revision, {}, acceptance=path)
    published = select(tmp_path, 'synthetic', {})
    physical = published.base.root / 'package.json'
    assert physical.stat().st_size <= LIMIT
    physical_manifest = json.loads(physical.read_text())
    assert len(encode(
        physical_manifest['publication']['acceptance']['receipts'])) <= 1024
    assert any(name.startswith('package.acceptance') for name in published.manifest['files'])
    receipts = published.manifest['publication']['acceptance']['receipts']
    assert len(receipts) == 60
    require(published)
    assert Queries().read(published, {})['design_ready'] is True


def test_compact_receipt_directory_leaves_room_for_large_manifests(tmp_path):
    """A real 121-receipt package must not push package.json over 16 KiB."""
    from cadai.pdk_standard.jsonio import LIMIT, Writer, encode, mapping

    writer = Writer(tmp_path)
    values = {f'receipt_{index:03d}': {'padding': 'x' * 80}
              for index in range(121)}
    descriptor = writer.shard('package.json', 'acceptance', values, target=1024)
    assert len(encode(descriptor)) <= 1024
    assert mapping(tmp_path, descriptor) == values
    manifest = {
        'publication': {'acceptance': {'receipts': descriptor}},
        'files': {f'file_{index:04d}': 'sha256:' + '0' * 64 for index in range(64)},
        'dependencies': {f'dep_{index:04d}': {
            'target': 'device', 'path': 'source_%04d' % index}
            for index in range(48)},
    }
    assert len(encode(manifest)) < LIMIT
