"""Bounded evidence for a worker tree that outlives its watchdog."""

import json
import os
import signal

import pytest
from sico.service.background_evidence import custody_proof
from sico.service.background_worker import BackgroundWorker, _write
from test_agent_b1_worker import assert_reaped
from test_agent_b1_worker import runtime as runtime


def make_worker(tmp_path, settings, **options):
    root = tmp_path / 'jobs'
    return BackgroundWorker(root, settings, execution_timeout=1, shutdown_timeout=.2, **options)


@pytest.mark.parametrize('killed', ['watcher', 'custodian'])
def test_watchdog_loss_reaps_whole_tree_and_next_job_is_independent(tmp_path, runtime, killed):
    holder = {}

    def inject(receipt):
        if receipt['state'] != 'running' or receipt.get('request_id'):
            return
        worker = holder['worker']
        watcher = json.loads((worker.directory / 'watcher.json').read_text())
        os.kill(receipt['pid'], signal.SIGSTOP)
        victim = worker.guardian.pid if killed == 'custodian' else watcher['guardian']['pid']
        os.kill(victim, signal.SIGKILL)

    worker = make_worker(tmp_path, runtime, progress=inject)
    holder['worker'] = worker
    result = worker.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    assert result['state'] == 'worker_timeout', result
    assert_reaped(result)
    custody = json.loads((worker.directory / 'custody.json').read_text())
    if killed == 'watcher':
        assert custody['phase'] == 'finished'
        assert custody['tree_owned'] and custody['process_stopped']
        assert custody['children_remaining'] == 0
        assert custody['reason'] == 'watcher_exited'
    else:
        assert json.loads((worker.directory / 'watchdog.json').read_text())['process_stopped']

    following = BackgroundWorker(tmp_path / 'jobs', runtime, execution_timeout=10,
                                 shutdown_timeout=.5)
    completed = following.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    assert completed['state'] == 'completed', completed
    assert_reaped(completed)
    for key in ('job_id', 'worker_id', 'generation', 'bridge_id', 'router_id'):
        assert completed[key] != result[key]


def test_custody_proof_requires_finished_tree_and_current_identity(tmp_path, runtime):
    worker = BackgroundWorker(tmp_path / 'jobs', runtime, execution_timeout=10)
    result = worker.run('inspect_schematic', {'lib': 'Work', 'cell': 'top'})
    assert result['state'] == 'completed', result
    path = worker.directory / 'custody.json'
    original = json.loads(path.read_text())
    proof = custody_proof(worker.directory, worker.receipt,
                          worker.guardian_identity, worker.watcher_identity)
    assert proof['phase'] == 'finished'
    changes = [('phase', 'armed'), ('process_stopped', False),
               ('children_remaining', 1), ('job_id', 'f' * 32), ('boot_id', 'old-boot'),
               ('custodian', dict(original['custodian'], start='0')),
               ('watcher', dict(original['watcher'], start='0'))]
    for key, value in changes:
        damaged = dict(original, **{key: value})
        _write(path, damaged)
        assert custody_proof(worker.directory, worker.receipt,
                             worker.guardian_identity, worker.watcher_identity) is None, key
    _write(path, original)
    assert custody_proof(worker.directory, worker.receipt,
                         worker.guardian_identity, worker.watcher_identity)['process_stopped']
