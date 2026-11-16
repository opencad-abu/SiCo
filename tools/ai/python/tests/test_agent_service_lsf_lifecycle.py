"""Cancellation before submission, retained capacity and local CLI descendants."""

import json
import os
import sys
import threading
from pathlib import Path

import pytest
from agent_lsf_fixtures import Scheduler, backend, config, request
from agent_lsf_fixtures import runtime as runtime  # noqa: F401
from test_agent_b2_jobs import status, submit, wait_for
from test_agent_service_lsf_recovery import manager

from sico.service.background_jobs import BackgroundJobs
from sico.service.lsf_bundle import prepare_bundle, run_node
from sico.lsf.commands import command_environment, run_command
from sico.service.lsf_evidence import initial, save
from sico.transport.framing import ProtocolError


@pytest.mark.parametrize('action', ['cancel', 'close'])
def test_cancel_or_detach_during_preparation_never_submits(tmp_path, runtime, monkeypatch, action):
    from sico.service import lsf_job

    entered, release = threading.Event(), threading.Event()
    original = lsf_job.prepare_bundle
    def paused(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(lsf_job, 'prepare_bundle', paused)
    scheduler = Scheduler(config(tmp_path))
    jobs = manager(tmp_path, runtime, scheduler)
    try:
        row = submit(jobs)
        assert entered.wait(3)
        if action == 'cancel':
            jobs.cancel_background(row['job_id'], session_id='s')
        else:
            jobs.close()
        release.set()
        done = wait_for(lambda: status(jobs, row['job_id']), lambda r: r['state'] == 'cancelled')
        assert done['operation_dispatched'] is False
        wait_for(lambda: jobs.busy, lambda value: not value)
        assert not scheduler.submissions and not scheduler.cancellations
    finally:
        release.set()
        jobs.close()
        assert jobs.wait(3)


def test_reconciled_old_job_wakes_new_admission_without_replay(tmp_path, runtime):
    scheduler = Scheduler(config(tmp_path), node=True)
    jobs = manager(tmp_path, runtime, scheduler)
    old = submit(jobs)
    assert scheduler.submitted.wait(3)
    jobs.close()
    assert jobs.wait(3)
    try:
        assert scheduler.child.wait(10) == 0
        adapter, _ = backend(tmp_path, runtime, scheduler)
        reopened = BackgroundJobs(jobs.root, None, backends={'lsf': adapter},
                                  execution_backend='lsf')
        try:
            assert reopened.busy
            new = submit(reopened)
            assert status(reopened, new['job_id'])['state'] == 'queued'
            assert len(scheduler.submissions) == 1
            assert status(reopened, old['job_id'])['state'] == 'completed'
            wait_for(lambda: len(scheduler.submissions), lambda n: n == 2)
            assert scheduler.submissions[0][0] != scheduler.submissions[1][0]
        finally:
            reopened.close()
            assert reopened.wait(3)
            assert scheduler.child.wait(10) == 0
    finally:
        scheduler.close()


def test_node_checks_persisted_manifest_before_start(tmp_path, runtime):
    r, c = request(), config(tmp_path)
    root = tmp_path / 'background'
    manifest = prepare_bundle(root, r, runtime, {}, {}, c)
    save(root, initial(r, c, manifest))
    row = json.loads(manifest.read_text())
    row['params']['max_items'] = 1
    manifest.write_text(json.dumps(row))
    with pytest.raises(ProtocolError, match='manifest changed'):
        run_node(manifest)
    assert not (manifest.parent / 'node.started.json').exists()
    assert not (manifest.parent / 'workers').exists()


@pytest.mark.parametrize('timeout', [False, True])
def test_scheduler_cli_reclaims_detached_descendants(tmp_path, timeout):
    marker = tmp_path / 'descendant.pid'
    code = ('import os,time; '
            'child=os.fork(); '
            'os.setsid() if child == 0 else None; '
            'open(' + repr(str(marker)) + ',"w").write(str(os.getpid())) if child == 0 else None; '
            'time.sleep(30) if child == 0 else time.sleep(' + ('30' if timeout else '.1') + ')')
    try:
        if timeout:
            with pytest.raises(TimeoutError):
                run_command([sys.executable, '-c', code],
                            environment=command_environment(), timeout=.5)
        else:
            result, _ = run_command([sys.executable, '-c', code],
                                    environment=command_environment(), timeout=3)
            assert result == 0
        pid = int(marker.read_text())
        assert not Path('/proc/' + str(pid)).exists()
    finally:
        # Test failure cleanup is restricted to the exact PID created by this fixture.
        if marker.exists() and Path('/proc/' + marker.read_text()).exists():
            os.kill(int(marker.read_text()), 9)


def test_scheduler_cli_preserves_nonzero_status():
    code, output = run_command([sys.executable, '-c', 'print("rejected");raise SystemExit(7)'],
                               environment=command_environment(), timeout=3)
    assert code == 7 and output.strip() == 'rejected'
