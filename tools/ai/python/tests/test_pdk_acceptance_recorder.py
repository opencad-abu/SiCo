"""Real-run recording boundaries with deterministic operation/source fault injection."""

from copy import deepcopy
import json

import pytest
from pdk_device_acceptance import recording, compile_evidence
from pdk_publication_fixture import ready_candidate
from pdk_standard_fixture import replace, update

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.publication import publish
from cadai.pdk_standard.receipts import OPERATIONS, load
from cadai.pdk_standard.workspace import select


def recorder(workspace, directory, validator=lambda _: None):
    return recording(directory, ['cell0'], source_validator=validator,
                     select_package=lambda: select(workspace, 'synthetic', {}))


def all_passed(report):
    report['devices']['cell0'] = {op: {'ok': True} for op in OPERATIONS}


def test_snapshot_is_durable_before_operations_and_completed_evidence_can_publish(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    directory = tmp_path / 'run'
    seen = []
    with recorder(tmp_path, directory, lambda p: seen.append(p.revision)) as (package, report):
        saved = json.loads((directory / 'report.json').read_text())
        assert saved['snapshot']['subject']['revision'] == package.revision
        assert not (directory / 'evidence.json').exists()
        all_passed(report)
    assert seen == [current.revision, current.revision]
    path = directory / 'evidence.json'
    compiled = tmp_path / 'compiled.json'
    compile_evidence([path], compiled)
    assert load(compiled)['runs'] == load(path)['runs']
    assert publish(tmp_path, 'synthetic', current.revision, {},
                   acceptance=str(compiled))['design_ready']
    with pytest.raises(FileExistsError):
        with recorder(tmp_path, directory):
            pytest.fail('An existing run must never be overwritten')


@pytest.mark.parametrize('fault', ['missing', 'false', 'truthy'])
def test_missing_or_failed_operation_is_not_dropped_during_compilation(tmp_path, fault):
    current, _, _ = ready_candidate(tmp_path)
    directory = tmp_path / 'run'
    with recorder(tmp_path, directory) as (_, report):
        all_passed(report)
        report['devices']['cell0']['netlist'] = {
            'missing': {}, 'false': {'ok': False}, 'truthy': {'ok': 'yes'}}[fault]
    compiled = tmp_path / 'compiled.json'
    compile_evidence([directory / 'evidence.json'], compiled)
    assert load(compiled)['runs'][0]['results']['cell0']['netlist'] == 'failed'
    with pytest.raises(PdkUnavailable, match='failed operations'):
        publish(tmp_path, 'synthetic', current.revision, {}, acceptance=str(compiled))


@pytest.mark.parametrize('fault', ['crash', 'changed'])
def test_interrupted_or_changed_run_retains_snapshot_without_completed_evidence(tmp_path, fault):
    current, _, _ = ready_candidate(tmp_path)
    directory = tmp_path / 'run'
    with pytest.raises((RuntimeError, PdkUnavailable)):
        with recorder(tmp_path, directory) as (_, report):
            snapshot = deepcopy(report['snapshot'])
            all_passed(report)
            if fault == 'crash':
                raise RuntimeError('injected runner crash')
            updates, preview = update(tmp_path, current,
                [replace('/parameters/w/domain/max', 3e-6)], 'cell0/cdf.json')
            updates.apply({'update_ref': preview['update_ref']})
    saved = json.loads((directory / 'report.json').read_text())
    assert saved['snapshot'] == snapshot and saved['error']
    assert not (directory / 'evidence.json').exists()


def test_compiler_cannot_upgrade_a_legacy_raw_report(tmp_path):
    path = tmp_path / 'old-report.json'
    path.write_text(json.dumps({'complete': ['cell0'], 'revision': None}))
    output = tmp_path / 'evidence.json'
    with pytest.raises(PdkUnavailable, match='v2'):
        compile_evidence([path], output)
    assert not output.exists()
