"""Atomic reviewed publication, bounded patches, source identity and overlay persistence."""

from sicostate import project_directory

from copy import deepcopy
import json

import pytest

from cadai.pdk_errors import PdkUnavailable, PdkArgumentError
from cadai.pdk_standard.jsonio import LIMIT, encode, read, sha
from cadai.pdk_standard.package import publish
from cadai.pdk_standard.query import Queries
from cadai.pdk_standard.tools import arguments
from cadai.pdk_standard.workspace import select
from cadai.pdk_standard.freshness import check
from pdk_standard_fixture import package, update, replace
from pdk_fixture import raw


def test_collection_is_pending_all_cells_retained_and_small(tmp_path):
    effective, _, _, result = package(tmp_path, 156)
    assert result['status'] == 'incomplete' and result['design_ready'] is False
    assert not Queries().read(effective, {'section': 'devices'})['items']
    query = Queries()
    args = {'section': 'cdf', 'device': 'cell0', 'include_unconfirmed': True, 'page_size': 10}
    rows = []
    while True:
        page = query.read(effective, args)
        assert len(encode(page)) <= LIMIT
        rows.extend(r['id'] for r in page['items'] if r.get('section') == 'parameters')
        if page['complete']:
            break
        args['cursor'] = page['next_cursor']
    assert len(rows) == len(set(rows)) == 156
    assert all(p.stat().st_size <= LIMIT for p in effective.base.root.rglob('*.json'))
    assert query.read(effective, {'section': 'missing'})['status'] == 'incomplete'


def test_no_model_assertion_can_commit_and_question_shows_exact_diff(tmp_path):
    effective, _, _, _ = package(tmp_path)
    updates, preview = update(tmp_path, effective, [replace('/items/cell0/use/circuit', 'allow')], host=False)
    assert preview['changes'][0]['changes'] == [
        {'path': '/items/cell0/use/circuit', 'before': 'unknown', 'after': 'allow'}]
    assert preview['update_ref'] in preview['selection_question']['question']
    assert effective.revision in preview['selection_question']['question']
    assert updates.apply({'update_ref': preview['update_ref']})['confirmation_required']
    assert select(tmp_path, 'synthetic', {}).revision == effective.revision
    with pytest.raises(PdkArgumentError):
        arguments('apply_pdk_data_update', {'update_ref': preview['update_ref'], 'confirmed': True})


def test_overlay_is_cumulative_and_persistent_with_large_cdf(tmp_path):
    effective, _, _, _ = package(tmp_path, 156)
    original = {p: p.read_bytes() for p in effective.base.root.rglob('*.json')}
    updates, preview = update(tmp_path, effective, [replace('/items/cell0/use/circuit', 'allow')])
    first = updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    assert current.revision == first['revision']
    assert Queries().read(current, {'include_unconfirmed': True})['items'][0]['id'] == 'cell0'
    updates, preview = update(tmp_path, current, [replace('/parameters/w/write', 'deny'),
                            replace('/parameters/w/requirement', 'not_applicable')], 'cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    assert current.document('device.json')['items']['cell0']['use']['circuit'] == 'allow'
    assert current.document('cell0/cdf.json')['parameters']['w']['write'] == 'deny'
    assert len(current.document('cell0/cdf.json')['parameters']) == 156
    assert len(current.changes) == 3
    assert current.base.revision == effective.revision
    assert all(p.read_bytes() == contents for p, contents in original.items())
    assert all(p.stat().st_size <= LIMIT for p in current.overlay.rglob('*.json'))
    # Same baseline may be visible through both environment and workspace.
    duplicate = select(tmp_path, 'synthetic', {'SICO_PDK_DATA': str(project_directory(tmp_path, "ai/pdk-data", create=True))})
    assert duplicate.revision == current.revision


def test_cancel_stale_cursor_and_compare_and_swap(tmp_path):
    effective, _, _, _ = package(tmp_path, 30)
    query = Queries()
    page = query.read(effective, {'section': 'cdf', 'device': 'cell0', 'include_unconfirmed': True})
    first, a = update(tmp_path, effective, [replace('/items/cell0/use/circuit', 'allow')])
    second, b = update(tmp_path, effective, [replace('/items/cell0/use/testbench', 'allow')])
    first.apply({'update_ref': a['update_ref']})
    with pytest.raises(PdkUnavailable, match='Concurrent'):
        second.apply({'update_ref': b['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    with pytest.raises(PdkUnavailable, match='revision changed'):
        query.read(current, {'section': 'cdf', 'device': 'cell0', 'include_unconfirmed': True,
                             'cursor': page['next_cursor']})
    cancel, c = update(tmp_path, current, [replace('/items/cell0/use/testbench', 'allow')])
    cancel.confirmation = lambda _: {'decision': 'cancel'}
    assert cancel.apply({'update_ref': c['update_ref']})['status'] == 'cancelled'
    assert select(tmp_path, 'synthetic', {}).revision == current.revision


def test_tampered_shard_or_confirmation_fails(tmp_path):
    effective, _, _, _ = package(tmp_path, 156)
    updates, preview = update(tmp_path, effective, [replace('/items/cell0/use/circuit', 'allow')])
    scope = updates.previews[preview['update_ref']]['scope']
    scope.write_text('{}')
    with pytest.raises(PdkUnavailable, match='evidence changed'):
        updates.apply({'update_ref': preview['update_ref']})
    cdf = read(effective.base.root / 'cell0/cdf.json')
    shard = effective.base.root / cdf['parameters']['$parts'][0]['path']
    shard.write_text('{}')
    with pytest.raises(PdkUnavailable, match='digest mismatch'):
        effective.document('cell0/cdf.json')


def test_source_changed_after_review_prevents_commit(tmp_path):
    effective, bridge, _, _ = package(tmp_path)
    updates, preview = update(tmp_path, effective, [replace('/items/cell0/use/circuit', 'allow')])
    updates.source_validator = lambda package, diff: check(bridge, package, diff)
    check(bridge, effective, preview['changes'])
    bridge.data['cdf']['parameters']['items'][0]['callback'] = raw('changed callback')
    with pytest.raises(PdkUnavailable, match='Source changed'):
        updates.apply({'update_ref': preview['update_ref']})
    assert select(tmp_path, 'synthetic', {}).revision == effective.revision


def test_scalar_patches_schema_and_protected_identity(tmp_path):
    effective, _, _, _ = package(tmp_path)
    args = {'library': 'synthetic', 'revision': effective.revision,
            'changes': [{'file': 'device.json', 'ops': [replace('/items/cell0/use/circuit', 'allow')]}]}
    assert arguments('prepare_pdk_data_update', args) == args
    with pytest.raises(PdkUnavailable, match='identity'):
        update(tmp_path, effective, [replace('/items/cell0/cell', 'fictional')])
    with pytest.raises(PdkUnavailable, match='evidence'):
        update(tmp_path, effective, [replace('/source', 'my_assertion')])
    with pytest.raises(PdkUnavailable, match='Recommended|allowed'):
        update(tmp_path, effective, [{'op': 'add', 'path': '/items/cell0/recommended',
            'value': [{'use': 'circuit', 'for': 'test', 'priority': 0, 'reason': 'test'}]}])


def test_multi_file_failure_leaves_index_and_baseline_unchanged(tmp_path, monkeypatch):
    import cadai.pdk_standard.overlay as module
    effective, _, _, _ = package(tmp_path)
    index = tmp_path / '.sico/ai/pdk-data/index.json'
    before = index.read_bytes()
    updates, preview = update(tmp_path, effective, [replace('/items/cell0/use/circuit', 'allow')])
    original = module.atomic
    def broken(path, value):
        if path.name == 'index.json':
            raise OSError('simulated index switch failure')
        return original(path, value)
    monkeypatch.setattr(module, 'atomic', broken)
    with pytest.raises(OSError, match='simulated'):
        updates.apply({'update_ref': preview['update_ref']})
    assert index.read_bytes() == before
    assert select(tmp_path, 'synthetic', {}).revision == effective.revision


def test_shared_baseline_receives_workspace_overlay_only(tmp_path):
    shared = tmp_path / 'shared'
    local = tmp_path / 'local'
    local.mkdir()
    effective, _, _, _ = package(shared)
    environment = {'SICO_PDK_DATA': str(project_directory(shared, "ai/pdk-data", create=True))}
    from cadai.pdk_standard.updates import Updates
    updates = Updates(local, environment)
    updates.confirmation = lambda _: {'decision': 'confirm', 'actor': 'owner', 'evidence_ref': 'answer:1'}
    before = (shared / '.sico/ai/pdk-data/index.json').read_bytes()
    preview = updates.prepare({'library': 'synthetic', 'revision': effective.revision,
        'changes': [{'file': 'device.json', 'ops': [replace('/items/cell0/use/circuit', 'allow')]}]})
    updates.apply({'update_ref': preview['update_ref']})
    result = select(local, 'synthetic', environment)
    assert result.document('device.json')['items']['cell0']['use']['circuit'] == 'allow'
    assert (shared / '.sico/ai/pdk-data/index.json').read_bytes() == before
    assert result.base.root == effective.base.root
    assert result.overlay.is_relative_to(local)


def test_removing_optional_rule_does_not_claim_unchanged_sibling_provenance(tmp_path):
    effective, _, _, _ = package(tmp_path)
    u, preview = update(tmp_path, effective, [{'op': 'add', 'path': '/items/cell0/note', 'value': 'Test note'}])
    u.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    u, preview = update(tmp_path, current, [{'op': 'remove', 'path': '/items/cell0/note'}])
    u.apply({'update_ref': preview['update_ref']})
    current = select(tmp_path, 'synthetic', {})
    from cadai.pdk_standard.provenance import source_for
    assert source_for(current.document('device.json'), '/items/cell0/use/circuit') == 'capture'


def test_nonfinite_duplicate_json_and_path_escape_rejected(tmp_path):
    from cadai.pdk_standard.jsonio import relative
    for content in ('{"v":1e999}', '{"v":1,"v":2}', '\ufeff{"v":1}'):
        path = tmp_path / 'invalid.json'
        path.write_text(content)
        with pytest.raises(PdkUnavailable):
            read(path)
    for name in ('../outside', '/absolute', 'a//b', './a', ''):
        with pytest.raises(PdkUnavailable):
            relative(tmp_path, name)


def test_simulation_setup_is_reviewable_through_the_update_flow(tmp_path):
    from pdk_publication_fixture import ready_candidate

    current, _, _ = ready_candidate(tmp_path)
    setup = {'switch_views': ['spectre', 'schematic'], 'stop_views': ['spectre'],
             'options': {'scale': 0.9}}
    updates, preview = update(tmp_path, current, [
        replace('/setup', setup)], file='cell0/simulation.json')
    updates.apply({'update_ref': preview['update_ref']})
    result = select(tmp_path, 'synthetic', {})
    assert result.document('cell0/simulation.json')['setup'] == setup


def test_category_registry_grows_additively_and_cannot_lose_entries(tmp_path):
    current, bridge, observed, _ = package(tmp_path)
    updates, preview = update(
        tmp_path, current,
        [{"op": "add", "path": "/items/Passives", "value": {"name": "Passives"}}],
        file="category.json")
    updates.apply({"update_ref": preview["update_ref"]})
    current = select(tmp_path, "synthetic", {})
    updates, preview = update(
        tmp_path, current,
        [{"op": "replace", "path": "/items/cell0/categories", "value": ["Passives"]}],
        file="device.json")
    updates.apply({"update_ref": preview["update_ref"]})
    final = select(tmp_path, "synthetic", {})
    assert final.document("category.json")["items"]["Passives"] == {"name": "Passives"}
    assert final.document("device.json")["items"]["cell0"]["categories"] == ["Passives"]
    with pytest.raises(PdkUnavailable, match="cannot remove observed categories"):
        update(tmp_path, final,
               [{"op": "remove", "path": "/items/Passives"}],
               file="category.json")
    updates, preview = update(tmp_path, final,
                              [{"op": "replace", "path": "/items/cell0/categories",
                                "value": ["Missing"]}],
                              file="device.json")
    with pytest.raises(PdkUnavailable, match="Unknown category"):
        updates.apply({"update_ref": preview["update_ref"]})
