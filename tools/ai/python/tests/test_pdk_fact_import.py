"""Objective reuse is source checked, repeatable and independent of usage policy."""

from copy import deepcopy

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_session import PdkSession
from cadai.pdk_standard.jsonio import LIMIT, encode
from cadai.pdk_standard.workspace import select
from pdk_fixture import raw
from pdk_standard_fixture import package, replace, update
from test_pdk_standard_ingest import enrich


def fixture(tmp_path, count=1):
    donor, _, _, _ = package(tmp_path / 'donor', count)
    enriched = enrich(tmp_path / 'donor', donor)
    current, bridge, _, _ = package(tmp_path / 'target', count)
    (tmp_path / 'target/manual.txt').write_bytes((tmp_path / 'donor/manual.txt').read_bytes())
    updates, preview = update(tmp_path / 'target', current, [replace('/items/cell0/use/circuit', 'deny')])
    updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path / 'target', 'synthetic', {})
    args = {'library': 'synthetic', 'revision': current.revision,
            'source_index': str(tmp_path / 'donor/.sico/ai/pdk-data/index.json'),
            'source_revision': enriched.revision}
    session = PdkSession(bridge=bridge, workspace=tmp_path / 'target',
                         environment={'SICO_PDK_WORKFLOW': 'generation'})
    return current, bridge, session, args


def test_import_preserves_denials_and_existing_overlay(tmp_path):
    current, bridge, session, args = fixture(tmp_path, 160)
    devices = current.document('device.json')
    params = current.document('cell0/cdf.json')['parameters']
    result = session.call('import_pdk_facts', args)
    assert result['imported_fields'] == 2 and result['live_captures'] == 1
    effective = select(tmp_path / 'target', 'synthetic', {})
    assert effective.document('device.json') == devices
    after = effective.document('cell0/cdf.json')['parameters']
    assert after['w']['meaning'] == 'Total channel width'
    for key, row in params.items():
        assert (row['write'], row['requirement']) == (after[key]['write'], after[key]['requirement'])
    assert max(p.stat().st_size for p in effective.overlay.rglob('*.json')) <= LIMIT
    count = len(bridge.calls)
    again = session.call('import_pdk_facts', {**args, 'revision': effective.revision})
    assert again['imported_fields'] == 0 and again['revision'] == effective.revision
    assert len(bridge.calls) == count


@pytest.mark.parametrize('fault', ['bytes', 'cdf', 'dirty', 'source_revision', 'revision', 'concurrent', 'index_write'])
def test_failures_never_change_effective_index(tmp_path, monkeypatch, fault):
    current, bridge, session, args = fixture(tmp_path)
    index = tmp_path / 'target/.sico/ai/pdk-data/index.json'
    before = index.read_bytes()
    if fault == 'bytes':
        (tmp_path / 'target/manual.txt').write_text('different PDK release')
    elif fault == 'cdf':
        bridge.data['cdf']['parameters']['items'][0]['defValue'] = raw('99')
    elif fault == 'dirty':
        bridge.data['database']['master_state']['modified'] = True
    elif fault in {'source_revision', 'revision'}:
        args[fault] = 'stale'
    elif fault == 'concurrent':
        from cadai.pdk_standard import fact_import
        real, calls = fact_import.select, []
        def changed(*args):
            result = real(*args)
            calls.append(1)
            if len(calls) > 1:
                result.revision = 'concurrent'
            return result
        monkeypatch.setattr(fact_import, 'select', changed)
    else:
        from cadai.pdk_standard import overlay
        monkeypatch.setattr(overlay, 'atomic', lambda *_: (_ for _ in ()).throw(OSError('write failed')))
    with pytest.raises((PdkUnavailable, OSError)):
        session.call('import_pdk_facts', args)
    assert index.read_bytes() == before


def test_design_mode_cannot_import(tmp_path):
    _, bridge, _, args = fixture(tmp_path)
    session = PdkSession(bridge=bridge, workspace=tmp_path / 'target', environment={})
    with pytest.raises(PdkUnavailable, match='Normal design'):
        session.call('import_pdk_facts', args)


def test_collection_query_includes_denied_cells_but_only_editable_parameter_details(tmp_path):
    current, _, session, _ = fixture(tmp_path, 160)
    updates, preview = update(tmp_path / 'target', current, [replace('/parameters/w/write', 'allow'),
        replace('/parameters/w/requirement', 'explicit')], 'cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    args = {'library': 'synthetic', 'section': 'collection', 'device': 'cell0', 'page_size': 1}
    found = []
    while True:
        page = session.call('get_pdk_data', args)
        assert len(encode(page)) <= LIMIT
        found.extend(page['items'])
        if page['complete']:
            break
        args['cursor'] = page['next_cursor']
    assert len(found) < 160
    assert any(row['path'] == '/parameters/w/domain' for row in found)
    assert not any(row['path'].startswith('/parameters/p') for row in found)
    assert len(select(tmp_path / 'target', 'synthetic', {}).document('cell0/cdf.json')['parameters']) == 160
    assert not any('/use/' in row['path'] or row['path'].endswith('/write') for row in found)
    assert len({(row['file'], row['path']) for row in found}) == len(found)
    assert session.call('get_pdk_data', {'library': 'synthetic', 'section': 'missing'})['total'] == 0


def test_known_conflict_and_user_fact_are_not_silently_imported(tmp_path):
    current, _, session, args = fixture(tmp_path)
    updates, preview = update(tmp_path / 'target', current,
        [replace('/parameters/w/meaning', 'A different confirmed meaning')], 'cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path / 'target', 'synthetic', {})
    with pytest.raises(PdkUnavailable, match='Known fact conflicts'):
        session.call('import_pdk_facts', {**args, 'revision': current.revision})
    result = session.call('import_pdk_facts', {**args, 'revision': current.revision, 'preserve_conflicts': True})
    assert result['preserved_conflicts'] == 1 and result['status'] == 'incomplete'
    after = select(tmp_path / 'target', 'synthetic', {})
    assert after.document('cell0/cdf.json')['parameters']['w']['meaning'] == 'A different confirmed meaning'
    assert after.document('file.json')['items']['manual']['dependency'] == 'manual_bytes'


def test_readme_classification_is_collected_without_model_permission():
    from cadai.pdk_standard.project import files
    result = files({'deck_files': ['/pdk/models/device_readme.txt', '/pdk/models/device.lib']}, '/pdk/lib')
    assert {r['path']: r['kind'] for r in result.values()} == {
        'models/device_readme.txt': 'document', 'models/device.lib': 'model'}


def test_legacy_interface_digest_preserves_raw_issues(tmp_path):
    from cadai.pdk_normalize import context, detail
    from cadai.pdk_standard.jsonio import fingerprint
    from cadai.pdk_standard.source_check import observed
    current, bridge, _, _ = package(tmp_path)
    target = {'library': 'synthetic', 'cell': 'cell0', 'view': 'symbol'}
    capture = bridge.capture('device', target)
    current.manifest['dependencies']['legacy_interface'] = {
        'kind': 'interface', 'target': target, 'method': 'sico-cdf-siminfo-json-v1',
        'fingerprint': fingerprint(capture['data']['cdf']['simulators'])}
    value = detail(capture['data'], context(capture['context']))
    observed(current, 'cell0', value, ['legacy_interface'])
    capture['data']['cdf']['simulators']['issues'].append('raw issue')
    value = detail(capture['data'], context(capture['context']))
    with pytest.raises(PdkUnavailable, match='Source changed'):
        observed(current, 'cell0', value, ['legacy_interface'])


def test_large_added_dependency_inventory_has_one_authority(tmp_path):
    from cadai.pdk_standard.overlay import publish
    from cadai.pdk_standard.jsonio import fingerprint
    current, _, _, _ = package(tmp_path)
    cdf = current.document('cell0/cdf.json')
    deps = {'dependency_' + str(i): {'kind': 'cdf', 'method': 'synthetic',
            'target': {'library': 'synthetic', 'cell': 'cell0', 'view': 'symbol'},
            'fingerprint': fingerprint(i)} for i in range(250)}
    cdf['depends_on'].extend(deps)
    publish(tmp_path / '.sico/ai/pdk-data', current, {'cell0/cdf.json': cdf}, dependencies=deps)
    result = select(tmp_path, 'synthetic', {})
    assert set(result.document('cell0/cdf.json')['depends_on']) == set(cdf['depends_on'])
    assert set(result.dependency_updates) == set(deps)
    assert set(result.changes['cell0/cdf.json']['dependencies']) == {'cell0_cdf'}
    assert max(p.stat().st_size for p in result.overlay.rglob('*.json')) <= LIMIT


def test_tool_is_exposed_through_mcp_and_host(tmp_path):
    from sico.adapters.circuit import NAMES
    from cadai.mcp_pdk_handler import dispatch_pdk
    assert 'import_pdk_facts' in NAMES
    _, _, session, args = fixture(tmp_path)
    ok, result = dispatch_pdk('import_pdk_facts', args, session=session, bindings=None, client=None)
    assert ok and result['imported_fields'] > 0
