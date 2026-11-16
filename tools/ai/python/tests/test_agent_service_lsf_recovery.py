"""Common admission plus unknown submit, cancellation and restart association."""

import json
import time

import pytest
from agent_lsf_fixtures import Scheduler, backend, config, request
from agent_lsf_fixtures import runtime as runtime  # noqa: F401
from test_agent_b2_jobs import origin, status, submit, wait_for

from sico.service.background_jobs import BackgroundJobs
from sico.transport.framing import ProtocolError


def manager(tmp_path, runtime, scheduler):
    adapter, _ = backend(tmp_path, runtime, scheduler)
    return BackgroundJobs(adapter.root, None, backends={'lsf': adapter}, execution_backend='lsf')


def test_lost_submit_ack_queries_original_name_once_and_retains_busy(tmp_path, runtime):
    scheduler = Scheduler(config(tmp_path), lost=True)
    jobs = manager(tmp_path, runtime, scheduler)
    try:
        row = submit(jobs)
        assert row['execution_backend'] == 'lsf'
        assert scheduler.submitted.wait(3)
        wait_for(lambda: status(jobs, row['job_id']), lambda x: x.get('scheduler'))
        observed = status(jobs, row['job_id'])
        assert observed['scheduler']['job_id'] == '42'
        assert observed['state'] == 'running' and jobs.busy
        assert len(scheduler.submissions) == 1
        assert not observed['automatic_resume_allowed']
    finally:
        jobs.close()
        assert jobs.wait(3)
    assert not scheduler.cancellations


def test_restart_associates_using_captured_config_without_submit_or_local_kill(tmp_path, runtime,
                                                                           monkeypatch):
    scheduler = Scheduler(config(tmp_path), lost=True)
    jobs = manager(tmp_path, runtime, scheduler)
    row = submit(jobs)
    assert scheduler.submitted.wait(3)
    jobs.close()
    assert jobs.wait(3)
    from sico.service import lsf_evidence

    monkeypatch.setattr(lsf_evidence, 'LsfScheduler', lambda c: scheduler)
    reopened = BackgroundJobs(jobs.root, None)
    try:
        assert reopened.busy  # Retained scan blocks idle before any UI query.
        observed = status(reopened, row['job_id'])
        assert observed['scheduler']['cluster'] == 'eda-test'
        assert observed['control_available'] and len(scheduler.submissions) == 1
        cancelled = reopened.cancel_background(row['job_id'], session_id='s')
        assert cancelled['cancel_outcome'] == 'requested'
        assert cancelled['state'] == 'running' and reopened.busy
        assert len(scheduler.cancellations) == 1
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_done_without_artifact_and_temporarily_missing_query_remain_unknown(tmp_path, runtime):
    scheduler = Scheduler(config(tmp_path), state='DONE')
    jobs = manager(tmp_path, runtime, scheduler)
    try:
        row = submit(jobs)
        wait_for(lambda: status(jobs, row['job_id']), lambda r: r['state'] == 'running_unknown')
        assert jobs.busy
        scheduler.fail_query = True
        current = status(jobs, row['job_id'])
        assert current['state'] == 'running_unknown' and not current.get('artifact')
        assert len(scheduler.submissions) == 1
    finally:
        jobs.close()
        assert jobs.wait(3)


def test_stable_admission_id_deduplicates_and_conflicting_input_refuses(tmp_path, runtime):
    scheduler = Scheduler(config(tmp_path))
    jobs = manager(tmp_path, runtime, scheduler)
    r = request()
    try:
        a = jobs.submit_background(r.method, dict(r.params), session_id='s', origin=origin(),
                                   operation_id=r.job_id)
        b = jobs.submit_background(r.method, dict(r.params), session_id='s', origin=origin(),
                                   operation_id=r.job_id)
        assert a['job_id'] == b['job_id']
        with pytest.raises(ProtocolError):
            jobs.submit_background(r.method, dict(r.params, max_items=2), session_id='s',
                                   origin=origin(), operation_id=r.job_id)
        assert scheduler.submitted.wait(3)
        assert len(scheduler.submissions) == 1
    finally:
        jobs.close()
        assert jobs.wait(3)


def test_corrupt_backend_discriminator_is_protocol_damage(tmp_path):
    root = tmp_path / 'background'
    jobs = BackgroundJobs(root, None)
    try:
        path = jobs.records / ('a' * 32 + '.json')
        path.write_text(json.dumps(dict(protocol='cad_ai_background_jobs.v1', job_id='a' * 32,
            owner_session_id='s', execution_backend='shell', state='running',
            accepted_at=time.time())))
        path.chmod(0o600)
        with pytest.raises(ProtocolError):
            status(jobs, 'a' * 32)
    finally:
        jobs.close()
        assert jobs.wait(3)


def test_persisted_intent_precedes_bsub_and_failure_never_submits(tmp_path, runtime, monkeypatch):
    from sico.service import lsf_job

    scheduler = Scheduler(config(tmp_path))
    adapter, _ = backend(tmp_path, runtime, scheduler)
    def failed(*args):
        raise OSError('intent fsync failed')
    monkeypatch.setattr(lsf_job, 'save', failed)
    with pytest.raises(OSError):
        adapter.create(request(), lambda row: None).run()
    assert not scheduler.submissions


def test_remote_running_job_holds_queue_slot_and_queued_cancel_never_submits(tmp_path, runtime):
    scheduler = Scheduler(config(tmp_path))
    jobs = manager(tmp_path, runtime, scheduler)
    try:
        first, second = submit(jobs), submit(jobs)
        assert scheduler.submitted.wait(3)
        assert len(scheduler.submissions) == 1
        assert jobs.cancel_background(second['job_id'], session_id='s')['state'] == 'cancelled'
        jobs.close()
        assert jobs.wait(3)
        assert len(scheduler.submissions) == 1 and not scheduler.cancellations
        assert status(jobs, first['job_id'])['state'] == 'running'
    finally:
        jobs.close()
        jobs.wait(3)


def test_cancel_ack_does_not_release_running_remote_capacity(tmp_path, runtime):
    scheduler = Scheduler(config(tmp_path))
    jobs = manager(tmp_path, runtime, scheduler)
    try:
        admitted = submit(jobs)
        assert scheduler.submitted.wait(3)
        jobs.cancel_background(admitted['job_id'], session_id='s')
        wait_for(lambda: scheduler.cancellations, bool)
        observed = status(jobs, admitted['job_id'])
        assert observed['state'] in {'running', 'cancelling'}
        assert not observed.get('scheduler_verified') and jobs.busy
        assert len(scheduler.cancellations) == 1
    finally:
        jobs.close()
        assert jobs.wait(3)


def test_new_explicit_cancel_can_retry_after_prior_unconfirmed_request(tmp_path, runtime):
    scheduler = Scheduler(config(tmp_path))
    jobs = manager(tmp_path, runtime, scheduler)
    try:
        row = submit(jobs)
        assert scheduler.submitted.wait(3)
        jobs.cancel_background(row['job_id'], session_id='s')
        wait_for(lambda: len(scheduler.cancellations), lambda n: n == 1)
        jobs.cancel_background(row['job_id'], session_id='s')
        wait_for(lambda: len(scheduler.cancellations), lambda n: n == 2)
        assert jobs.busy
    finally:
        jobs.close()
        assert jobs.wait(3)
