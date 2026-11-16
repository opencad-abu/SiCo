"""Acceptance evidence must identify what was tested before publication."""

import json
from pathlib import Path

import pytest
from sicostate import project_directory
from pdk_publication_fixture import acceptance_evidence, ready_candidate
from pdk_standard_fixture import replace, update

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.publication import publish
from cadai.pdk_standard.receipts import device_dependencies
from cadai.pdk_standard.workspace import select


def rejected(workspace, current, path):
    index = project_directory(workspace, 'ai/pdk-data') / 'index.json'
    before = index.read_bytes()
    evidence = path.read_bytes()
    with pytest.raises(PdkUnavailable):
        publish(workspace, 'synthetic', current.revision, {}, acceptance=str(path))
    assert index.read_bytes() == before
    assert path.read_bytes() == evidence


@pytest.mark.parametrize('fault', ['missing', 'wrong', 'empty'])
def test_publication_does_not_supply_or_replace_tested_fingerprints(tmp_path, fault):
    current, _, _ = ready_candidate(tmp_path)
    path = Path(acceptance_evidence(tmp_path))
    evidence = json.loads(path.read_text())
    run = evidence['runs'][0]
    if fault == 'missing':
        run.pop('fingerprints', None)
    elif fault == 'empty':
        run['fingerprints'] = {'cell0': {}}
    else:
        run['fingerprints'] = {'cell0': {key: 'sha256:' + '0' * 64
                                        for key in device_dependencies(current, 'cell0')}}
    path.write_text(json.dumps(evidence))
    rejected(tmp_path, current, path)


def test_old_evidence_cannot_be_rebound_after_effective_overlay_changes(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    path = Path(acceptance_evidence(tmp_path))
    publish(tmp_path, 'synthetic', current.revision, {}, acceptance=str(path))
    current = select(tmp_path, 'synthetic', {})
    updates, preview = update(tmp_path, current, [replace('/parameters/w/domain/max', 3e-6)],
                              'cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    rejected(tmp_path, select(tmp_path, 'synthetic', {}), path)


@pytest.mark.parametrize('fault', ['missing_subject', 'digest', 'revision', 'package',
                                 'failed', 'missing_result', 'partial_results', 'legacy'])
def test_missing_stale_or_failed_run_never_replaces_the_index(tmp_path, fault):
    current, _, _ = ready_candidate(tmp_path)
    path = Path(acceptance_evidence(tmp_path))
    data = json.loads(path.read_text())
    row = data['runs'][0]
    if fault == 'missing_subject':
        del row['subject']
    elif fault == 'digest':
        row['subject']['content_digest'] = 'sha256:' + '0' * 64
    elif fault == 'revision':
        row['subject']['revision'] = 'old-revision'
    elif fault == 'package':
        row['subject']['package_id'] = 'other-package'
    elif fault == 'failed':
        row['results']['cell0']['netlist'] = 'failed'
    elif fault == 'missing_result':
        del row['results']
    elif fault == 'partial_results':
        del row['results']['cell0']['wiring']
    else:
        data.update(schema_version='sico.pdk.acceptance-evidence.v1',
                    verifier='sico-device-acceptance-v1')
        del row['subject'], row['results']
    path.write_text(json.dumps(data))
    rejected(tmp_path, current, path)


@pytest.mark.parametrize('bad_json', ['duplicate', 'nonfinite', 'array'])
def test_acceptance_evidence_uses_strict_json(tmp_path, bad_json):
    current, _, _ = ready_candidate(tmp_path)
    path = Path(acceptance_evidence(tmp_path))
    raw = path.read_bytes()
    if bad_json == 'duplicate':
        raw = b'{"verifier":"forged",' + raw[1:]
    elif bad_json == 'nonfinite':
        raw = b'{"x_value":NaN,' + raw[1:]
    else:
        raw = b'[]'
    path.write_bytes(raw)
    rejected(tmp_path, current, path)


def test_publisher_preserves_the_original_tested_identity_and_results(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    path = Path(acceptance_evidence(tmp_path))
    original = json.loads(path.read_text())['runs'][0]
    publish(tmp_path, 'synthetic', current.revision, {}, acceptance=str(path))
    published = select(tmp_path, 'synthetic', {})
    receipts = published.manifest['publication']['acceptance']['receipts']
    assert list(receipts.values()) == [original]
    assert original['subject']['revision'] != published.revision


def test_legacy_published_receipts_remain_readable_without_design_qualification(tmp_path):
    from cadai.pdk_standard.publication import require
    from cadai.pdk_standard.query import Queries
    current, _, _ = ready_candidate(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {}, acceptance=acceptance_evidence(tmp_path))
    published = select(tmp_path, 'synthetic', {})
    path = published.base.root / 'package.json'
    data = json.loads(path.read_text())
    data['required_features'] = ['qualified-publication-v1', 'verified-acceptance-v1']
    block = data['publication']['acceptance']
    block['verifier'] = 'sico-device-acceptance-v1'
    for row in block['receipts'].values():
        del row['subject'], row['results']
    path.write_text(json.dumps(data))
    readable = select(tmp_path, 'synthetic', {})
    assert readable.document('device.json')['items']['cell0']['cell'] == 'cell0'
    with pytest.raises(PdkUnavailable) as error:
        require(readable)
    assert error.value.code == 'pdk_acceptance_required'
    assert Queries().read(readable, {})['design_ready'] is False
    from cadai.pdk_standard.package import install
    installed = install(tmp_path / 'installed', 'synthetic', readable.base.root,
                        {'pdk': str(tmp_path)})
    assert installed['design_ready'] is False


def test_publisher_detects_same_revision_content_change_before_index_swap(tmp_path, monkeypatch):
    from copy import deepcopy
    from cadai.pdk_standard import ingest, workspace
    current, _, _ = ready_candidate(tmp_path)
    path = Path(acceptance_evidence(tmp_path))
    original = ingest.write_documents
    read = current.document
    def race(stage, manifest, content):
        original(stage, manifest, content)
        def changed(name):
            value = deepcopy(read(name))
            if name == 'cell0/cdf.json':
                value['parameters']['w']['domain']['max'] = 3e-6
            return value
        current.document = changed
    monkeypatch.setattr(workspace, 'select', lambda *_: current)
    monkeypatch.setattr(ingest, 'write_documents', race)
    rejected(tmp_path, current, path)


@pytest.mark.parametrize('fault', ['empty', 'omitted', 'failed', 'masked_failure'])
def test_design_consumer_rejects_incomplete_or_failed_receipts(tmp_path, fault):
    from copy import deepcopy
    from cadai.pdk_standard.publication import require
    current, _, _ = ready_candidate(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {}, acceptance=acceptance_evidence(tmp_path))
    current = select(tmp_path, 'synthetic', {})
    receipts = current.manifest['publication']['acceptance']['receipts']
    row = next(iter(receipts.values()))
    if fault == 'empty':
        row['fingerprints']['cell0'].clear()
    elif fault == 'omitted':
        row['fingerprints']['cell0'].pop('cell0_cdf')
    else:
        if fault == 'masked_failure':
            receipts['older_pass'] = deepcopy(row)
        row['results']['cell0']['netlist'] = 'failed'
    with pytest.raises(PdkUnavailable):
        require(current)
