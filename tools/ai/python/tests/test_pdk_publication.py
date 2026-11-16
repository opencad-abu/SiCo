"""Generation acceptance and read-only design across sessions/workspaces."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from sicostate import project_directory
from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard import gate
from cadai.pdk_standard.baseline_choice import resolve
from cadai.pdk_standard.catalog import selection_status
from cadai.pdk_standard.jsonio import LIMIT
from cadai.pdk_standard.lifecycle import status
from cadai.pdk_standard.publication import publish
from cadai.pdk_standard.query import Queries, cdf_missing
from cadai.pdk_standard.readiness import accept
from cadai.pdk_standard.service import StandardData
from cadai.pdk_standard.workspace import select
from pdk_publication_fixture import acceptance_evidence, ready_candidate
from pdk_standard_fixture import replace, update


def test_unpublished_allowed_devices_are_not_design_candidates(tmp_path):
    current, _, device = ready_candidate(tmp_path)
    result = Queries().read(current, {})
    assert result['items'] == [] and result['code'] == 'pdk_acceptance_required'
    assert not result['design_ready'] and result['next_action'] == 'accept_pdk_data'
    service = StandardData(tmp_path, {})
    with pytest.raises(PdkUnavailable) as error:
        gate.obtain(service, device)
    assert error.value.code == 'pdk_generation_incomplete'
    assert service.updates.previews == {}
    captured = status(tmp_path, 'synthetic', {})
    assert captured['status'] == 'capture_ready'
    assert captured['acceptance_required']
    assert captured['next_action'] == 'accept_pdk_data'


def test_generation_publication_reuses_confirmations_in_another_workspace(tmp_path):
    generation, consumer = tmp_path / 'generation', tmp_path / 'consumer'
    current, bridge, device = ready_candidate(generation, auxiliary=160)
    result = publish(generation, 'synthetic', current.revision, {},
                     acceptance=acceptance_evidence(generation))
    environment = {'SICO_PDK_DATA': str(project_directory(generation, 'ai/pdk-data'))}
    consumer.mkdir()
    before = {p: p.read_bytes() for p in generation.rglob('*.json')}
    for _ in range(2):
        service = StandardData(consumer, environment)
        data = service.call('get_pdk_data', {'library': 'synthetic'})
        assert data['design_ready'] and len(data['items']) == 1
        rule = gate.obtain(service, device)
        assert gate.values(rule, {'w': '1u'})['w'] == 1e-6
        assert rule['simulation']['setup']['switch_views'] == ['spectre', 'schematic']
        assert not service.updates.previews
    assert result['design_ready'] and result['status'] == 'published'
    assert all(p.read_bytes() == original for p, original in before.items())
    assert all(p.stat().st_size <= LIMIT for p in generation.rglob('*.json'))
    selected = select(consumer, 'synthetic', environment)
    assert selected.document('cell0/cdf.json')['parameters']['aux159']['write'] == 'unknown'
    assert publish(generation, 'synthetic', result['revision'], {})['revision'] == result['revision']


def test_publication_digest_includes_sharded_manifest_dependencies(tmp_path):
    from cadai.pdk_standard.jsonio import read
    from cadai.pdk_standard.publication import require
    current, _, _ = ready_candidate(tmp_path, auxiliary=160, dependency_count=80)
    result = publish(tmp_path, 'synthetic', current.revision, {},
                     acceptance=acceptance_evidence(tmp_path))
    published = select(tmp_path, 'synthetic', {})
    physical = read(published.base.root / 'package.json')
    assert '$parts' in physical['dependencies']
    assert len(published.manifest['dependencies']) == 83
    assert any(name.startswith('package.dependencies.') for name in published.manifest['files'])
    require(published)
    assert result['design_ready']


@pytest.mark.parametrize('name,path', [
    ('cell0/symbol.json', 'bbox'), ('cell0/symbol.json', 'grid'),
    ('cell0/cdf.json', 'execution'), ('cell0/simulation.json', 'interfaces'),
    ('cell0/simulation.json', 'setup')])
def test_incomplete_publication_does_not_change_index(tmp_path, monkeypatch, name, path):
    current, _, _ = ready_candidate(tmp_path)
    original = current.document
    value = deepcopy(original(name))
    value[path] = ({'mode': 'unknown'} if path == 'execution' else {} if path in {'interfaces', 'setup'}
                   else {'state': 'unknown', 'reason': 'Missing'})
    value['evidence'] = {p: v for p, v in value.get('evidence', {}).items()
                         if not p.startswith('/' + path + '/')}
    current.document = lambda n: deepcopy(value) if n == name else original(n)
    import cadai.pdk_standard.workspace as workspace
    monkeypatch.setattr(workspace, 'select', lambda *_: current)
    index = project_directory(tmp_path, 'ai/pdk-data') / 'index.json'
    before = index.read_bytes()
    with pytest.raises(PdkUnavailable) as error:
        publish(tmp_path, 'synthetic', current.revision, {})
    assert error.value.code == 'pdk_generation_incomplete'
    assert index.read_bytes() == before


def test_missing_model_corner_and_resource_dependency_block_publication(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    original = current.document
    simulation = original('cell0/simulation.json')
    simulation['interfaces']['spectre'].update(model_source='external', model_refs=['default'])
    model = {'source': 'capture', 'depends_on': [], 'configs': {'default': {
        'simulator': 'spectre', 'language': 'spectre', 'includes': [{'file_ref': 'deck'}]}}, 'corners': {}}
    files = original('file.json')
    files['items']['deck'] = {'kind': 'model', 'root': 'pdk', 'path': 'model.scs', 'purpose': 'Model'}
    documents = {'cell0/simulation.json': simulation, 'model.json': model, 'file.json': files}
    current.document = lambda n: deepcopy(documents[n]) if n in documents else original(n)
    available = current.available
    current.available = lambda n: n in documents or available(n)
    with pytest.raises(PdkUnavailable, match='model_corner_missing'):
        accept(current)
    model['corners']['tt'] = {'name': 'typical', 'configs': ['default']}
    with pytest.raises(PdkUnavailable, match='model_dependency_missing'):
        accept(current)


def test_declared_interface_preserves_transitive_dependencies(tmp_path):
    current, _, _ = ready_candidate(tmp_path, auxiliary=3)
    cdf = current.document('cell0/cdf.json')
    assert list(cdf_missing(cdf)) == []
    cdf['parameters']['w']['depends_on'] = ['aux0']
    cdf['parameters']['aux0']['depends_on'] = ['aux1']
    gaps = list(cdf_missing(cdf))
    assert ('/parameters/aux1/write', 'confirmation_required') in gaps
    assert not any('/aux2/' in path for path, _ in gaps)
    cdf['parameters']['aux2']['write'] = 'allow'
    assert any('/aux2/' in path for path, _ in cdf_missing(cdf))


def test_implicit_netlist_terminal_publishes_with_a_named_connection(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    original = current.document
    simulation = original('cell0/simulation.json')
    interface = simulation['interfaces']['spectre']
    interface['term_order'].append('B')
    interface['implicit_terminals'] = {'B': '0'}
    current.document = lambda n: deepcopy(simulation) if n == 'cell0/simulation.json' else original(n)
    accept(current)
    interface['implicit_terminals'] = {'B': ''}
    with pytest.raises(PdkUnavailable, match='netlist_implicit_terminal_invalid'):
        accept(current)


def test_tool_synchronized_parameter_mapping_needs_the_registered_capability(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    original = current.document
    simulation = original('cell0/simulation.json')
    interface = simulation['interfaces']['spectre']
    interface['parameters']['mr'] = {'derived': 'tool-synchronized instance parameter (iPar("m"))',
                                     'implementation_ref': 'builtin:ipar-sync:v1'}
    current.document = lambda n: deepcopy(simulation) if n == 'cell0/simulation.json' else original(n)
    accept(current)
    interface['parameters']['mr']['implementation_ref'] = 'unregistered:v1'
    with pytest.raises(PdkUnavailable, match='unsupported_derived_netlist_parameter'):
        accept(current)


def test_registered_simulator_options_pass_and_unknown_stay_unsupported(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    original = current.document
    simulation = original('cell0/simulation.json')
    current.document = lambda n: deepcopy(simulation) if n == 'cell0/simulation.json' else original(n)
    simulation['setup']['options'] = {'scale': 0.9}
    accept(current)
    simulation['setup']['options'] = {'scale': 0}
    with pytest.raises(PdkUnavailable, match='unsupported_simulator_options'):
        accept(current)
    simulation['setup']['options'] = {'mystery': 1}
    with pytest.raises(PdkUnavailable, match='unsupported_simulator_options'):
        accept(current)


def test_republication_requires_current_revision_and_invalidates_existing_binding(tmp_path):
    current, _, device = ready_candidate(tmp_path)
    first = publish(tmp_path, 'synthetic', current.revision, {},
                    acceptance=acceptance_evidence(tmp_path))
    service = StandardData(tmp_path, {})
    rule = gate.obtain(service, device)
    current = select(tmp_path, 'synthetic', {})
    updates, preview = update(tmp_path, current, [replace('/items/cell0/use/circuit', 'deny')])
    updates.apply({'update_ref': preview['update_ref']})
    with pytest.raises(PdkUnavailable, match='rules changed'):
        gate.current(service, {'standard_rule': rule, 'master': {'target': device['target']}})
    assert not status(tmp_path, 'synthetic', {})['design_ready']
    with pytest.raises(PdkUnavailable, match='revision changed'):
        publish(tmp_path, 'synthetic', first['revision'], {})


def test_changed_live_source_is_maintenance_evidence_not_an_update_review(tmp_path):
    current, _, device = ready_candidate(tmp_path)
    publish(tmp_path, 'synthetic', current.revision, {},
            acceptance=acceptance_evidence(tmp_path))
    device['parameters']['items'][0]['raw']['defValue']['value'] = 'changed'
    service = StandardData(tmp_path, {})
    with pytest.raises(PdkUnavailable) as error:
        gate.obtain(service, device)
    assert error.value.code == 'pdk_source_changed'
    assert not service.updates.previews


def test_atomic_switch_failure_preserves_previous_reader(tmp_path, monkeypatch):
    current, _, _ = ready_candidate(tmp_path)
    import cadai.pdk_standard.publication as publication
    original = publication.atomic
    def broken(path, value):
        if path.name == 'index.json':
            raise OSError('simulated index switch failure')
        return original(path, value)
    monkeypatch.setattr(publication, 'atomic', broken)
    with pytest.raises(OSError, match='simulated'):
        publish(tmp_path, 'synthetic', current.revision, {},
                acceptance=acceptance_evidence(tmp_path))
    assert select(tmp_path, 'synthetic', {}).revision == current.revision


def test_published_catalog_skips_collection_in_fresh_session(tmp_path):
    from cadai.pdk_session import PdkSession
    shared = tmp_path / 'shared'
    current, bridge, _ = ready_candidate(shared)
    publish(shared, 'synthetic', current.revision, {},
            acceptance=acceptance_evidence(shared))
    consumer = tmp_path / 'consumer'
    consumer.mkdir()
    session = PdkSession(bridge=bridge, workspace=consumer,
                         environment={'SICO_PDK_DATA': str(project_directory(shared, 'ai/pdk-data'))})
    bridge.calls.clear()
    ready = session.call('prepare_pdk_data', {'library': 'synthetic'})
    assert ready['design_ready'] and not session.preparation.jobs
    assert bridge.calls == [
        ('libraries', {}),
        ('skill', 'aiPdkInitializeLibrary("synthetic")'),
    ]
    search = session.call('search_pdk_devices', {'library': 'synthetic'})
    assert len(search['items']) == 1
    detail = session.call('get_pdk_device', {'snapshot_ref': search['context']['snapshot_ref'],
                          'device_ref': search['items'][0]['device_ref']})
    assert detail['device']['target']['cell'] == 'cell0'
    assert all(op in {'libraries', 'device', 'skill'} for op, _ in bridge.calls)
    assert [value for op, value in bridge.calls if op == 'skill'] == [
        'aiPdkInitializeLibrary("synthetic")']
    assert not session.preparation.jobs


def test_local_publication_supersedes_only_exact_shared_base(tmp_path):
    shared, local = tmp_path / 'shared', tmp_path / 'local'
    current, _, _ = ready_candidate(shared)
    # Publish shared first; local generation retains a workspace overlay after a review.
    first = publish(shared, 'synthetic', current.revision, {},
                    acceptance=acceptance_evidence(shared))
    local.mkdir()
    environment = {'SICO_PDK_DATA': str(project_directory(shared, 'ai/pdk-data'))}
    from cadai.pdk_standard.updates import Updates
    updater = Updates(local, environment)
    updater.confirmation = lambda _: {'decision': 'confirm', 'actor': 'test', 'evidence_ref': 'audit:test'}
    preview = updater.prepare({'library': 'synthetic', 'revision': first['revision'],
        'changes': [{'file': 'device.json', 'ops': [replace('/items/cell0/reason', 'Reviewed locally')]}]})
    updater.apply({'update_ref': preview['update_ref']})
    edited = select(local, 'synthetic', environment)
    result = publish(local, 'synthetic', edited.revision, environment,
                     acceptance=acceptance_evidence(local, environment=environment))
    assert select(local, 'synthetic', environment).revision == result['revision']
    assert select(shared, 'synthetic', {}).revision == first['revision']


def test_conflicting_baselines_require_choice_and_keep_workspace(tmp_path):
    shared, local = tmp_path / 'shared', tmp_path / 'local'
    current, _, _ = ready_candidate(shared)
    shared_result = publish(shared, 'synthetic', current.revision, {},
                            acceptance=acceptance_evidence(shared))
    current, _, _ = ready_candidate(local)
    local_result = publish(local, 'synthetic', current.revision, {},
                           acceptance=acceptance_evidence(local))
    environment = {'SICO_PDK_DATA': str(project_directory(shared, 'ai/pdk-data'))}
    with pytest.raises(PdkUnavailable) as error:
        select(local, 'synthetic', environment)
    assert error.value.code == 'pdk_baseline_sync_required'
    message = str(error.value)
    assert str(project_directory(shared, 'ai/pdk-data')) in message
    assert str(project_directory(local, 'ai/pdk-data')) in message
    choice = selection_status(local, 'synthetic', environment)
    assert choice['status'] == 'decision_required'
    assert choice['question']['id'] == 'pdk_baseline_sync'
    service = StandardData(local, environment)
    kept = service.call('resolve_pdk_baseline', {
        'library': 'synthetic', 'baseline_ref': choice['baseline_ref'],
        'decision': 'keep_workspace', 'choice_confirmed': True,
    })
    assert kept['status'] == 'kept_workspace'
    assert kept['revision'] == local_result['revision']
    assert select(local, 'synthetic', environment).revision == local_result['revision']


def test_sync_shared_baseline_archives_workspace_baseline(tmp_path):
    shared, local = tmp_path / 'shared', tmp_path / 'local'
    current, _, _ = ready_candidate(shared)
    shared_result = publish(shared, 'synthetic', current.revision, {},
                            acceptance=acceptance_evidence(shared))
    current, _, _ = ready_candidate(local)
    publish(local, 'synthetic', current.revision, {},
            acceptance=acceptance_evidence(local))
    environment = {'SICO_PDK_DATA': str(project_directory(shared, 'ai/pdk-data'))}
    choice = selection_status(local, 'synthetic', environment)
    result = resolve(local, 'synthetic', environment, {
        'library': 'synthetic', 'baseline_ref': choice['baseline_ref'],
        'decision': 'sync_shared', 'choice_confirmed': True,
    })
    assert result['status'] == 'synced_shared'
    assert result['revision'] == shared_result['revision']
    assert Path(result['archived']).is_dir()
    assert select(local, 'synthetic', environment).revision == shared_result['revision']
    index = json.loads((project_directory(local, 'ai/pdk-data') / 'index.json').read_text())
    assert index['packages']['synthetic']['path'].startswith('synthetic.' + shared_result['revision'][:12])
