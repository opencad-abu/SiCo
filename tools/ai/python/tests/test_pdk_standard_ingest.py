"""Document enrichment remains bounded, atomic and separate from user consent."""

from sicostate import project_directory

from copy import deepcopy

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.ingest import publish
from cadai.pdk_standard.jsonio import sha, LIMIT
from cadai.pdk_standard.resource_check import check
from cadai.pdk_standard.workspace import select
from pdk_standard_fixture import package


def enrich(tmp_path, current):
    resource = tmp_path / 'manual.txt'
    resource.write_text('Width is the total channel width in meters.')
    files = current.document('file.json')
    files['items']['manual'] = {'kind': 'document', 'root': 'pdk', 'path': 'manual.txt',
                                'purpose': 'Manual', 'dependency': 'manual_bytes'}
    sources = current.document('sources.json')
    sources['items']['manual'] = {'kind': 'document', 'at': '2026-09-22T00:00:00Z',
        'summary': 'Width definition', 'file_ref': 'manual', 'locator': 'line 1'}
    cdf = current.document('cell0/cdf.json')
    cdf['parameters']['w']['meaning'] = 'Total channel width'
    cdf['depends_on'].append('manual_bytes')
    cdf['evidence'] = {'/parameters/w/meaning': 'manual'}
    return publish(project_directory(tmp_path, "ai/pdk-data", create=True), current.base,
        {'file.json': files, 'sources.json': sources, 'cell0/cdf.json': cdf},
        dependencies={'manual_bytes': {'kind': 'file', 'target': 'manual',
                      'method': 'sha256-bytes-v1', 'fingerprint': sha(resource.read_bytes())}})


def test_sourced_revision_preserves_old_readers(tmp_path):
    current, _, _, _ = package(tmp_path, 160)
    old = current.document('cell0/cdf.json')
    result = enrich(tmp_path, current)
    assert current.document('cell0/cdf.json') == old
    effective = select(tmp_path, 'synthetic', {})
    assert effective.revision == result.revision != current.revision
    assert effective.document('cell0/cdf.json')['parameters']['w']['meaning'] == 'Total channel width'
    assert all(p.stat().st_size <= LIMIT for p in result.root.rglob('*.json'))
    check(effective, 'manual_bytes')
    (tmp_path / 'manual.txt').write_text('changed')
    with pytest.raises(PdkUnavailable, match='Source resource changed'):
        check(effective, 'manual_bytes')
    with pytest.raises(PdkUnavailable, match='baseline changed'):
        enrich(tmp_path, current)


def test_document_ingestion_cannot_authorize_usage(tmp_path):
    current, _, _, _ = package(tmp_path)
    devices = deepcopy(current.document('device.json'))
    devices['items']['cell0']['use']['circuit'] = 'allow'
    before = (tmp_path / '.sico/ai/pdk-data/index.json').read_bytes()
    with pytest.raises(PdkUnavailable, match='cannot grant'):
        publish(project_directory(tmp_path, "ai/pdk-data", create=True), current.base, {'device.json': devices})
    assert (tmp_path / '.sico/ai/pdk-data/index.json').read_bytes() == before


def test_missing_document_resource_does_not_publish(tmp_path):
    current, _, _, _ = package(tmp_path)
    sources = current.document('sources.json')
    sources['items']['missing'] = {'kind': 'document', 'at': '2026-09-22T00:00:00Z',
        'summary': 'Missing source', 'file_ref': 'absent', 'locator': 'page 1'}
    before = (tmp_path / '.sico/ai/pdk-data/index.json').read_bytes()
    with pytest.raises(PdkUnavailable, match='registered resource'):
        publish(project_directory(tmp_path, "ai/pdk-data", create=True), current.base, {'sources.json': sources})
    assert (tmp_path / '.sico/ai/pdk-data/index.json').read_bytes() == before


def test_document_ingestion_cannot_relabel_live_cell(tmp_path):
    current, _, _, _ = package(tmp_path)
    devices = current.document('device.json')
    devices['items']['cell0']['cell'] = 'different'
    with pytest.raises(PdkUnavailable, match='device identity'):
        publish(project_directory(tmp_path, "ai/pdk-data", create=True), current.base, {'device.json': devices})


def test_denial_with_reason_does_not_assert_missing_symbol(tmp_path):
    from types import SimpleNamespace
    from cadai.pdk_standard.freshness import check as check_freshness
    row = {'view': {'state': 'unknown', 'reason': 'No symbol'}}
    current = SimpleNamespace(document=lambda _: {'items': {'primitive': row}})
    bridge = SimpleNamespace(capture=lambda *_: pytest.fail('No symbol may be opened'))
    edits = [{'file': 'device.json', 'changes': [
        {'path': '/items/primitive/use/circuit', 'after': 'deny'},
        {'path': '/items/primitive/reason', 'after': 'User excludes this primitive'}]}]
    check_freshness(bridge, current, edits)
    edits[0]['changes'][0]['after'] = 'allow'
    with pytest.raises(PdkUnavailable, match='without an observed symbol'):
        check_freshness(bridge, current, edits)


def test_cache_discovery_ignores_unrelated_workspace_trees(tmp_path):
    from cadai.pdk_data import PdkData
    from cadai.pdk_normalize import context
    from pdk_fixture import Bridge
    data = PdkData(tmp_path, {})
    for suffix in ('pdk-cache', 'pdk-data', 'arbitrary-task-log'):
        project_directory(tmp_path, "ai/" + suffix, create=True)
    visited = []
    data._files = lambda root: visited.append(root.name) or []
    data.discover(context(Bridge().ctx), 'symbol')
    assert visited == ['pdk-cache', 'pdk-data']
