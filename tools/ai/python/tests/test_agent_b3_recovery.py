"""Fault injection for independent deadlines and durable, non-replaying recovery."""
import json
import os
import signal
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from test_agent_b1_worker import runtime as runtime
from test_agent_b2_jobs import status, submit, wait_for

from sico.service.background_jobs import BackgroundJobs
from sico.service.background_worker import _write


def launch_manager(tmp_path, runtime, *, execution_timeout=1, pause=''):
    config = tmp_path / 'runtime.json'
    config.write_text(json.dumps(asdict(runtime), default=str))
    driver = '''
import json, sys, time, os
from pathlib import Path
from sico.service.background_jobs import BackgroundJobs
from sico.service.background_worker import BackgroundRuntime
from sico.core.contracts import BoundContext
root = Path(sys.argv[1])
runtime = BackgroundRuntime(**json.loads((root/'runtime.json').read_text()))
jobs = BackgroundJobs(root/'background', runtime, worker_options={
    'startup_timeout': 10, 'execution_timeout': float(sys.argv[2]), 'shutdown_timeout': .2})
target = dict(lib='Work',cell='top',view='schematic')
if sys.argv[3] == 'final':
    save = jobs._progress
    def progress(receipt):
        if receipt.get('finished_at'):
            (root/'paused').touch()
            while True: time.sleep(.1)
        save(receipt)
    jobs._progress = progress
row = jobs.submit_background('inspect_schematic', target, session_id='s',
    origin=BoundContext('front','g','window',{'cellview':target,'cwd':'/isolated'}))
(root/'accepted.part').write_text(json.dumps(row))
(root/'accepted.part').replace(root/'accepted.json')
while True: time.sleep(.1)
'''
    process = subprocess.Popen([sys.executable, '-c', driver, str(tmp_path),
                                str(execution_timeout), pause],
                               cwd=Path(__file__).resolve().parents[1])
    wait_for(lambda: (tmp_path / 'accepted.json').exists(), bool)
    row = json.loads((tmp_path / 'accepted.json').read_text())
    return process, row, tmp_path / 'background/workers' / row['job_id']


def finish(process):
    if process.poll() is None:
        os.kill(process.pid, signal.SIGCONT)
        process.kill()
    process.wait(timeout=3)


def read_receipt(directory):
    try:
        return json.loads((directory / 'receipt.json').read_text())
    except FileNotFoundError:
        return {}


def test_independent_watchdog_stops_worker_when_supervisor_is_frozen(tmp_path, runtime):
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    process, row, directory = launch_manager(tmp_path, runtime, execution_timeout=1)
    try:
        wait_for(lambda: (directory / 'dispatched').exists(), bool)
        os.kill(process.pid, signal.SIGSTOP)
        wait_for(lambda: (directory / 'watchdog.json').exists(), bool, timeout=4)
        report = json.loads((directory / 'watchdog.json').read_text())
        assert report['reason'] == 'execution_timeout'
        assert report['process_stopped'] and report['job_id'] == row['job_id']
    finally:
        finish(process)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = wait_for(lambda: status(reopened, row['job_id']),
                             lambda r: r['state'] != 'recovering')
        assert recovered['state'] == 'worker_timeout', recovered
        assert recovered['recovered'] and not recovered['automatic_resume_allowed']
        assert not reopened.busy
        assert not (directory / 'connection.json').exists()
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_crash_after_worker_completion_recovers_only_verified_result(tmp_path, runtime):
    process, row, directory = launch_manager(tmp_path, runtime, pause='final')
    try:
        wait_for(lambda: (tmp_path / 'paused').exists(), bool)
        assert read_receipt(directory)['state'] == 'completed'
    finally:
        finish(process)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = status(reopened, row['job_id'])
        assert recovered['state'] == 'completed' and recovered['recovered']
        assert reopened.read_background_result(row['job_id'], recovered['artifact'], session_id='s')
        stored = json.loads((reopened.records / (row['job_id'] + '.json')).read_text())
        assert stored['state'] == 'completed' and stored['recovered']
        assert not reopened.busy
    finally:
        reopened.close()
        assert reopened.wait(3)


@pytest.mark.parametrize('damage', ['truncated', 'identity'])
def test_recovery_rejects_incomplete_or_mismatched_artifact(tmp_path, runtime, damage):
    process, row, directory = launch_manager(tmp_path, runtime, pause='final')
    try:
        wait_for(lambda: (tmp_path / 'paused').exists(), bool)
    finally:
        finish(process)
    if damage == 'truncated':
        (directory / 'artifacts/result.json').write_text('{')
    else:
        receipt = read_receipt(directory)
        receipt['worker_id'] = 'f' * 32
        _write(directory / 'receipt.json', receipt)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = status(reopened, row['job_id'])
        assert recovered['state'] == 'result_invalid'
        assert not recovered.get('artifact') and recovered['recovered']
        with pytest.raises(ValueError):
            reopened.read_background_result(row['job_id'], {}, session_id='s')
        assert not reopened.busy
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_dead_supervisor_retains_unknown_and_allows_new_independent_job(tmp_path, runtime):
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    process, row, directory = launch_manager(tmp_path, runtime, execution_timeout=10)
    try:
        wait_for(lambda: (directory / 'dispatched').exists(), bool)
    finally:
        finish(process)
    reopened = BackgroundJobs(tmp_path / 'background', runtime,
                               worker_options={'shutdown_timeout': .2})
    try:
        recovered = wait_for(lambda: status(reopened, row['job_id']),
                             lambda r: r['state'] != 'recovering')
        assert recovered['state'] == 'running_unknown' and recovered['recovered']
        assert recovered['recovery']['process_stopped']
        (runtime.virtuoso.parent / 'mode').write_text('ok')
        new = submit(reopened)
        assert new['job_id'] != row['job_id'] and new['worker_id'] != row['worker_id']
        wait_for(lambda: status(reopened, new['job_id']), lambda r: r['state'] == 'completed')
        assert status(reopened, row['job_id'])['state'] == 'running_unknown'
    finally:
        reopened.close()
        assert reopened.wait(5)


def test_dead_guardian_and_supervisor_use_exact_process_identity(tmp_path, runtime):
    from sico.service.background_watchdog import process_alive
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    process, row, directory = launch_manager(tmp_path, runtime, execution_timeout=10)
    try:
        wait_for(lambda: (directory / 'dispatched').exists(), bool)
        watcher = json.loads((directory / 'watcher.json').read_text())
        os.kill(watcher['guardian']['pid'], signal.SIGKILL)
    finally:
        finish(process)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = wait_for(lambda: status(reopened, row['job_id']),
                             lambda r: r['state'] != 'recovering')
        assert recovered['state'] == 'running_unknown', recovered
        assert recovered['recovery']['process_stopped']
        custody = recovered['recovery']['custody']
        assert custody['phase'] == 'finished' and custody['tree_owned']
        assert custody['process_stopped'] and custody['children_remaining'] == 0
        assert custody['reason'] == 'watcher_exited'
        assert not recovered['automatic_resume_allowed']
        # The surviving custodian reaped the reparented worker tree; no recovery
        # operation was replayed.
        assert not process_alive(watcher['guardian'])
        wait_for(lambda: not process_alive(watcher['launcher']), bool, timeout=3)
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_dead_guardian_with_unfinished_custody_remains_cleanup_failed(tmp_path, runtime):
    from sico.service.background_watchdog import process_alive
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    process, row, directory = launch_manager(tmp_path, runtime, execution_timeout=10)
    try:
        wait_for(lambda: (directory / 'dispatched').exists(), bool)
        watcher = json.loads((directory / 'watcher.json').read_text())
        os.kill(watcher['guardian']['pid'], signal.SIGKILL)
    finally:
        finish(process)
    wait_for(lambda: not process_alive(watcher['custodian']), bool, timeout=3)
    custody = json.loads((directory / 'custody.json').read_text())
    custody.update(phase='armed', process_stopped=False, children_remaining=None)
    _write(directory / 'custody.json', custody)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = wait_for(lambda: status(reopened, row['job_id']),
                             lambda r: r['state'] != 'recovering')
        assert recovered['state'] == 'cleanup_failed', recovered
        assert not recovered['recovery']['process_stopped']
        assert not recovered['automatic_resume_allowed']
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_pid_reuse_or_old_boot_never_signals_unrelated_process(monkeypatch):
    from sico.service import background_recovery as recovery
    from sico.service.background_watchdog import boot_id, process_identity
    def forbidden(*args):
        pytest.fail('Attempt to signal an unrelated process')
    monkeypatch.setattr(signal, 'pidfd_send_signal', forbidden, raising=False)
    current = process_identity(os.getpid())
    assert recovery.stop_abandoned(dict(boot_id='old-boot', launcher=current))
    recovery.stop_abandoned(dict(boot_id=boot_id(), launcher=dict(current, start='0')))


def test_two_readers_recover_once_and_preserve_durable_identity(tmp_path, runtime):
    from concurrent.futures import ThreadPoolExecutor
    process, row, directory = launch_manager(tmp_path, runtime, pause='final')
    try:
        wait_for(lambda: (tmp_path / 'paused').exists(), bool)
    finally:
        finish(process)
    readers = [BackgroundJobs(tmp_path / 'background', None) for _ in range(2)]
    try:
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(lambda r: status(r, row['job_id']), readers))
        assert all(r['state'] == 'completed' for r in rows)
        assert rows[0]['recovered_at'] == rows[1]['recovered_at']
        assert not any(r.busy for r in readers)
    finally:
        for reader in readers:
            reader.close()
            assert reader.wait(3)


def test_supervisor_dies_before_launch_ack_never_starts_virtuoso(tmp_path, runtime):
    from sico.interpreter import agent_command
    from sico.service.background_watchdog import process_identity
    root = tmp_path / 'armed'
    root.mkdir(mode=0o700)
    (root / 'logs').mkdir(mode=0o700)
    parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    watcher = None
    try:
        config = dict(job_id='a'*32, worker_id='b'*32, instance_id='bg_test', generation='g',
                      supervisor=process_identity(parent.pid), startup_timeout=3, shutdown_timeout=.2,
                      command=[sys.executable, '-c', "from pathlib import Path; Path('forbidden').touch()"])
        _write(root / 'watch.json', config)
        watcher = subprocess.Popen(agent_command('background-watch', '--connection-file', str(root/'watch.json')))
        wait_for(lambda: (root/'watcher.json').exists(), bool)
        finish(parent)
        assert watcher.wait(timeout=3) == 0
        assert not (root/'forbidden').exists()
    finally:
        finish(parent)
        if watcher and watcher.poll() is None:
            watcher.kill()
            watcher.wait(3)


def test_recovery_never_adopts_artifact_without_completed_receipt(tmp_path, runtime):
    process, row, directory = launch_manager(tmp_path, runtime, pause='final')
    try:
        wait_for(lambda: (tmp_path / 'paused').exists(), bool)
    finally:
        finish(process)
    receipt = read_receipt(directory)
    receipt.update(state='running', ok=False)
    receipt.pop('artifact')
    receipt.pop('finished_at')
    _write(directory / 'receipt.json', receipt)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = status(reopened, row['job_id'])
        assert recovered['state'] == 'running_unknown'
        assert not recovered.get('artifact') and not reopened.busy
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_readonly_recovery_persistence_failure_never_returns_success(tmp_path, runtime, monkeypatch):
    process, row, directory = launch_manager(tmp_path, runtime, pause='final')
    try:
        wait_for(lambda: (tmp_path / 'paused').exists(), bool)
    finally:
        finish(process)
    from sico.service import background_recovery as recovery
    def fail(*args):
        raise OSError('recovery disk full')
    monkeypatch.setattr(recovery, '_write', fail)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        with pytest.raises(OSError, match='disk full'):
            status(reopened, row['job_id'])
        assert not reopened.busy
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_frozen_guardian_recovery_is_bounded(tmp_path, runtime):
    from sico.service.background_watchdog import process_alive
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    process, row, directory = launch_manager(tmp_path, runtime, execution_timeout=10)
    try:
        wait_for(lambda: (directory / 'dispatched').exists(), bool)
        watcher = json.loads((directory / 'watcher.json').read_text())
        os.kill(watcher['guardian']['pid'], signal.SIGSTOP)
    finally:
        finish(process)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = wait_for(lambda: status(reopened, row['job_id']),
                             lambda r: r['state'] != 'recovering', timeout=5)
        assert recovered['state'] == 'running_unknown', recovered
        assert recovered['recovery']['process_stopped']
        wait_for(lambda: not process_alive(watcher['guardian']), bool, timeout=3)
        assert not process_alive(watcher['launcher'])
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_queue_expiry_removes_job_without_spawning(tmp_path, runtime):
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    jobs = BackgroundJobs(tmp_path / 'background', runtime, queue_timeout=.15,
                          worker_options={'execution_timeout': 5, 'shutdown_timeout': .2})
    try:
        first = submit(jobs)
        wait_for(lambda: status(jobs, first['job_id']), lambda r: r.get('request_id'))
        queued = submit(jobs)
        done = wait_for(lambda: status(jobs, queued['job_id']), lambda r: r['state'] == 'queue_timeout')
        assert not done['operation_dispatched']
        assert not (jobs.worker_root / queued['job_id']).exists()
    finally:
        jobs.close()
        assert jobs.wait(5)


def test_b2_completed_receipt_remains_readable_without_new_watchdog_files(tmp_path, runtime):
    process, row, directory = launch_manager(tmp_path, runtime, pause='final')
    try:
        wait_for(lambda: (tmp_path / 'paused').exists(), bool)
    finally:
        finish(process)
    (directory / 'watcher.json').unlink()
    (directory / 'watchdog.json').unlink()
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        recovered = status(reopened, row['job_id'])
        assert recovered['state'] == 'completed'
        assert reopened.read_background_result(row['job_id'], recovered['artifact'], session_id='s')
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_live_supervisor_cleans_up_frozen_guardian(tmp_path, runtime):
    from concurrent.futures import ThreadPoolExecutor

    from test_agent_b1_worker import assert_reaped

    from sico.service.background_worker import BackgroundWorker
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    worker = BackgroundWorker(tmp_path / 'workers', runtime, execution_timeout=1, shutdown_timeout=.2)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(worker.run, 'inspect_schematic', dict(lib='Work', cell='top'))
        wait_for(lambda: (worker.directory / 'dispatched').exists(), bool)
        os.kill(worker.guardian.pid, signal.SIGSTOP)
        result = future.result(8)
    assert result['state'] == 'worker_timeout', result
    assert result['cleanup']['guardian_reaped']
    assert_reaped(result)
