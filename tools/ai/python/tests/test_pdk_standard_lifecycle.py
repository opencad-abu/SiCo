"""Lifecycle semantics: relocation reuse, old readers, interrupted commits, deletion."""

import shutil
from pathlib import Path

import pytest
from sicostate import project_directory

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_normalize import context, detail
from cadai.pdk_standard import update_store
from cadai.pdk_standard.draft_contract import OP
from cadai.pdk_standard.jsonio import atomic, mapping, read
from cadai.pdk_standard.source_check import observed
from cadai.pdk_standard.updates import Updates, apply_change, expected
from cadai.pdk_standard.workspace import select
from pdk_fixture import Bridge
from pdk_standard_fixture import package, replace, update


def device_refs(package, key):
    refs = set()
    row = package.document('device.json')['items'][key]
    for suffix in ('cdf.json', 'symbol.json', 'simulation.json'):
        name = row.get('dir', row['cell']) + '/' + suffix
        if package.available(name):
            refs.update(package.document(name)['depends_on'])
    return sorted(refs)


def test_relocated_workspace_data_directory_reuses_the_same_revision(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    source = project_directory(tmp_path, 'ai/pdk-data')
    moved_workspace = tmp_path / 'moved'
    moved_workspace.mkdir()
    target = project_directory(moved_workspace, 'ai/pdk-data', create=True)
    shutil.copytree(source, target, dirs_exist_ok=True)
    moved = select(moved_workspace, 'synthetic', {})
    assert moved.revision == current.revision
    assert moved.document('cell0/cdf.json') == current.document('cell0/cdf.json')
    assert moved.document('device.json') == current.document('device.json')


def test_relocated_source_root_keeps_fingerprints_and_passes_the_source_check(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    moved = tmp_path / 'moved-root'
    (moved / 'pdk').mkdir(parents=True)
    index_path = project_directory(tmp_path, 'ai/pdk-data') / 'index.json'
    index = read(index_path)
    index['packages'] = mapping(index_path.parent, index['packages'])
    index['packages']['synthetic']['roots'] = {'pdk': str(moved)}
    atomic(index_path, index)
    relocated = select(tmp_path, 'synthetic', {})
    assert relocated.revision == current.revision
    assert relocated.roots['pdk'] == str(moved)
    # A fresh capture at the new path must not look like a source change:
    # dependency fingerprints exclude the relocation-only path.
    bridge.ctx['libraries'][0]['resolved_path'] = str(moved / 'pdk')
    capture = bridge.capture('device', value['target'])
    live = detail(capture['data'], context(capture['context']))
    observed(relocated, 'cell0', live, device_refs(relocated, 'cell0'))


def test_old_reader_keeps_its_revision_after_a_new_switch(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    old = select(tmp_path, 'synthetic', {})
    old_root = old.base.root
    updates, preview = update(tmp_path, current, [replace('/parameters/w/meaning', 'Width')],
                              file='cell0/cdf.json')
    updates.apply({'update_ref': preview['update_ref']})
    latest = select(tmp_path, 'synthetic', {})
    assert latest.revision != old.revision
    # The old reader keeps its frozen view and its files stay immutable.
    assert old.revision == current.revision
    assert old.document('cell0/cdf.json') == current.document('cell0/cdf.json')
    assert old.document('device.json') == current.document('device.json')
    assert old_root.exists() and (old_root / 'package.json').exists()
    assert latest.document('cell0/cdf.json')['parameters']['w']['meaning'] == 'Width'


def test_interrupted_commit_recovers_from_its_retained_user_source(tmp_path, monkeypatch):
    current, bridge, value, _ = package(tmp_path)
    updates, preview = update(tmp_path, current, [replace('/parameters/w/meaning', 'Width')],
                              file='cell0/cdf.json')

    def interrupted(self, ref, result):
        raise OSError('worker died before writing the receipt')

    monkeypatch.setattr(update_store.Reviews, 'finish', interrupted)
    with pytest.raises(OSError):
        updates.apply({'update_ref': preview['update_ref']})
    monkeypatch.undo()
    committed = select(tmp_path, 'synthetic', {})
    assert committed.revision != current.revision
    assert committed.document('cell0/cdf.json')['parameters']['w']['meaning'] == 'Width'
    # A restarted worker reconstructs the receipt from the committed user source
    # instead of replaying the change or re-asking the host.
    fresh = Updates(tmp_path, {})
    fresh.confirmation = lambda _: pytest.fail('recovery must not re-ask the host')
    restored = fresh.apply({'update_ref': preview['update_ref']})
    assert restored['status'] == 'ok' and restored['recovered'] is True
    assert restored['revision'] == committed.revision


def test_deletion_semantics_are_defined_and_refused_by_the_consumer(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    assert set(OP['properties']['op']['enum']) == {'add', 'replace', 'remove'}
    record = {'expected': expected(current.document('cell0/cdf.json')), 'dependencies': {},
              'delete_file': True}
    with pytest.raises(PdkUnavailable, match='deletion is unsupported'):
        apply_change(current, 'cell0/cdf.json', record)
