"""Standard consumers share one read; legacy and unsupported data are never blessed."""

import json

import pytest
from pdk_publication_fixture import acceptance_evidence, ready_candidate
from sicostate import project_directory

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_normalize import context
from cadai.pdk_standard import catalog
from cadai.pdk_standard.publication import publish as publish_ready
from cadai.pdk_standard.query import Queries
from cadai.pdk_standard.workspace import select


class _Data:
    """The two attributes the preparation flow exposes to the standard adapter."""

    def __init__(self, workspace, environment=None):
        self.workspace = workspace
        self.environment = environment or {}


def test_legacy_directory_is_reported_unavailable_not_read(tmp_path):
    root = project_directory(tmp_path, 'ai/pdk-data', create=True)
    (root / 'index.json').write_text(json.dumps({
        'format': 'cad.pdk.directory.v1',
        'items': [{'library': 'synthetic', 'cells': ['cell0']}]}))
    with pytest.raises(PdkUnavailable) as error:
        select(tmp_path, 'synthetic', {})
    assert error.value.code == 'pdk_standard_unavailable'
    assert catalog.prepared(_Data(tmp_path), 'synthetic', {}) is None


def test_unsupported_standard_versions_stay_unreadable(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    root = project_directory(tmp_path, 'ai/pdk-data')
    index_path = root / 'index.json'
    original = index_path.read_text()
    index = json.loads(original)
    index['schema_version'] = '2.0.0'
    index_path.write_text(json.dumps(index))
    with pytest.raises(PdkUnavailable) as error:
        select(tmp_path, 'synthetic', {})
    assert error.value.code == 'unsupported_pdk_standard'
    index_path.write_text(original)
    publish_ready(tmp_path, 'synthetic', current.revision, {},
                  acceptance=acceptance_evidence(tmp_path))
    index = json.loads(index_path.read_text())
    manifest_path = root / index['packages']['synthetic']['path'] / 'package.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['schema_version'] = '0.9.0'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(PdkUnavailable) as error:
        select(tmp_path, 'synthetic', {})
    assert error.value.code == 'unsupported_pdk_standard'


def test_unpublished_data_stays_out_of_the_legacy_preparation_flow(tmp_path):
    current, bridge, _ = ready_candidate(tmp_path)
    assert catalog.prepared(_Data(tmp_path), 'synthetic', context(bridge.ctx)) is None
    publish_ready(tmp_path, 'synthetic', current.revision, {},
                  acceptance=acceptance_evidence(tmp_path))
    prepared = catalog.prepared(_Data(tmp_path), 'synthetic', context(bridge.ctx))
    assert prepared is not None and prepared['source'] == 'published_standard'
    assert prepared['digest'] == select(tmp_path, 'synthetic', {}).revision
    cells = {item['identity']['target']['cell'] for item in prepared['capture']['data']['items']}
    assert cells == {'cell0'}


def test_standard_entries_report_one_effective_revision(tmp_path):
    current, bridge, _ = ready_candidate(tmp_path)
    publish_ready(tmp_path, 'synthetic', current.revision, {},
                  acceptance=acceptance_evidence(tmp_path))
    package = select(tmp_path, 'synthetic', {})
    prepared = catalog.prepared(_Data(tmp_path), 'synthetic', context(bridge.ctx))
    response = Queries().read(package, {'library': 'synthetic', 'section': 'devices'})
    assert prepared['digest'] == response['revision'] == package.revision
    assert [item['id'] for item in response['items']] == ['cell0']
