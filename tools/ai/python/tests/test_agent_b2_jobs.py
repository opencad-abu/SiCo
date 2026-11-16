"""Asynchronous job ownership, cancellation and verified result boundaries."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sico.core.contracts import BoundContext
from test_agent_b1_worker import runtime

TARGET = {'lib': 'Work', 'cell': 'top', 'view': 'schematic'}


def origin(generation='g'):
    return BoundContext('front', generation, 'window', {'cellview': TARGET, 'cwd': '/isolated'})


def wait_for(operation, condition, timeout=8):
    deadline = time.monotonic() + timeout
    while True:
        result = operation()
        if condition(result):
            return result
        assert time.monotonic() < deadline, result
        time.sleep(.01)


@pytest.fixture
def jobs(tmp_path, runtime):
    from sico.service.background_jobs import BackgroundJobs
    service = BackgroundJobs(tmp_path / 'background', runtime, concurrency=1,
                             worker_options={'startup_timeout': 2, 'execution_timeout': 10,
                                             'shutdown_timeout': .2})
    yield service
    service.close()
    assert service.wait(6)


def submit(jobs, session='s', **params):
    return jobs.submit_background('inspect_schematic', dict(TARGET, **params),
                                  session_id=session, origin=origin())


def status(jobs, job_id, session='s'):
    return jobs.get_background_status(job_id, session_id=session)


def test_submission_returns_before_execution_and_queue_cancel_never_spawns(jobs, runtime):
    from cadai import process_monitor

    (runtime.virtuoso.parent / 'mode').write_text('delay')
    started = time.monotonic()
    first = submit(jobs)
    assert first['state'] == 'accepted' and time.monotonic() - started < .5
    running = wait_for(lambda: status(jobs, first['job_id']), lambda r: r['state'] == 'running')
    scope = str(jobs.root.parent / 'sessions' / 's')
    observed = process_monitor.snapshot(scope)
    process = next(row for row in observed if row['pid'] == running['pid'])
    assert process['name'] == 'Virtuoso · 后台检查' and process['state'] == 'running'
    assert {Path(path).name for path in process['logs']} == {'stdout.log', 'stderr.log', 'CDS.log'}
    assert not process_monitor.snapshot(str(jobs.root.parent / 'sessions' / 'other'))
    second = submit(jobs, session='other')
    queued = status(jobs, second['job_id'], 'other')
    assert queued['state'] == 'queued'
    assert jobs.cancel_background(second['job_id'], session_id='other')['state'] == 'cancelled'
    assert not (jobs.worker_root / second['job_id']).exists()
    done = wait_for(lambda: status(jobs, first['job_id']), lambda r: r['state'] == 'completed')
    assert done['origin'] == origin().record() and done['owner_session_id'] == 's'
    assert done['request_id'] and done['worker_id'] and done['finished_at']
    result = jobs.read_background_result(first['job_id'], done['artifact'], session_id='s')
    assert result['inspection']['cellview'] == TARGET
    assert not result['automatic_resume_allowed']
    exited = next(row for row in process_monitor.snapshot(scope) if row['id'] == process['id'])
    assert exited['state'] == 'exited' and exited['finished_at']


def test_cancel_running_reaps_process_and_preserves_unknown(jobs, runtime):
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    accepted = submit(jobs)
    job_id = accepted['job_id']
    wait_for(lambda: (jobs.worker_root / job_id / 'dispatched').exists(), bool)
    reply = jobs.cancel_background(job_id, session_id='s')
    assert reply['state'] == 'running_unknown'
    done = wait_for(lambda: status(jobs, job_id), lambda r: r.get('finished_at'))
    assert done['state'] == 'running_unknown' and done['cleanup']['process_reaped']
    assert not done.get('artifact')
    events = [json.loads(line)['record'] for line in
              (jobs.worker_root / job_id / 'router/requests.jsonl').read_text().splitlines()]
    request = [row for row in events if row['request_id'] == done['request_id']]
    assert request[-1]['state'] == 'timed_out_unknown'
    with pytest.raises(ValueError):
        jobs.read_background_result(job_id, {}, session_id='s')


def test_wrong_owner_and_artifact_reference_are_rejected(jobs):
    accepted = submit(jobs)
    key = accepted['job_id']
    for call in (jobs.get_background_status, jobs.cancel_background):
        with pytest.raises(ValueError, match='owner'):
            call(key, session_id='wrong')
    done = wait_for(lambda: status(jobs, key), lambda r: r['state'] == 'completed')
    for owner, ref in [('wrong', done['artifact']), ('s', dict(done['artifact'], sha256='0' * 64))]:
        with pytest.raises(ValueError):
            jobs.read_background_result(key, ref, session_id=owner)
    assert jobs.cancel_background(key, session_id='s')['state'] == 'completed'


def test_shutdown_cancels_queued_and_running_without_replay(jobs, runtime):
    (runtime.virtuoso.parent / 'mode').write_text('hang')
    a = submit(jobs)
    wait_for(lambda: status(jobs, a['job_id']), lambda r: r.get('pid'))
    b = submit(jobs)
    jobs.close()
    assert jobs.wait(5)
    assert status(jobs, b['job_id'])['state'] == 'cancelled'
    assert status(jobs, a['job_id'])['state'] == 'cancelled'
    with pytest.raises(ValueError):
        submit(jobs)


def test_archived_results_are_read_only_and_never_rescheduled(jobs, tmp_path):
    from sico.service.background_jobs import BackgroundJobs
    accepted = submit(jobs)
    done = wait_for(lambda: status(jobs, accepted['job_id']), lambda r: r['state'] == 'completed')
    jobs.close()
    assert jobs.wait(3)
    reopened = BackgroundJobs(jobs.root, None)
    try:
        assert reopened.get_background_status(done['job_id'], session_id='s')['state'] == 'completed'
        assert reopened.read_background_result(done['job_id'], done['artifact'], session_id='s')
        assert reopened.list_background(session_id='other') == []
        assert not reopened.busy
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_unavailable_configuration_rejects_before_acceptance(tmp_path):
    from sico.service.background_jobs import BackgroundJobs
    service = BackgroundJobs(tmp_path / 'background', None)
    try:
        with pytest.raises(ValueError, match='background_unavailable'):
            submit(service)
    finally:
        service.close()
        assert service.wait(3)


def test_foreground_target_and_generation_are_captured_once(jobs):
    accepted = submit(jobs)
    with pytest.raises(ValueError):
        jobs.submit_background('inspect_schematic', dict(TARGET, cell='different'),
                               session_id='s', origin=origin('next'))
    assert status(jobs, accepted['job_id'])['origin']['generation'] == 'g'
    with pytest.raises(ValueError):
        jobs.get_background_status('../escape', session_id='s')


def test_other_desktop_reads_live_state_without_claiming_or_cancelling(jobs, runtime):
    (runtime.virtuoso.parent / 'mode').write_text('hang')
    accepted = submit(jobs)
    key = accepted['job_id']
    wait_for(lambda: status(jobs, key), lambda r: r.get('pid'))
    script = '''
import json, sys
from sico.service.background_jobs import BackgroundJobs
m = BackgroundJobs(sys.argv[1], None)
try:
    row = m.get_background_status(sys.argv[2], session_id='s')
    assert row['state'] == 'starting', row
    assert row['owner_service_active'] and not row['control_available']
    try:
        m.cancel_background(sys.argv[2], session_id='s')
    except ValueError as exc:
        assert 'desktop' in str(exc)
    else:
        raise AssertionError('Foreign job cancellation accepted')
    assert not m.busy
finally:
    m.close()
    assert m.wait(3)
'''
    result = subprocess.run([sys.executable, '-c', script, str(jobs.root), key],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert status(jobs, key)['state'] == 'starting'


def test_persistence_failure_rejects_acceptance_and_does_not_spawn(jobs, monkeypatch):
    import sico.service.background_jobs as module
    def fail(*args):
        raise OSError('full disk')
    monkeypatch.setattr(module, '_write', fail)
    with pytest.raises(OSError, match='full disk'):
        submit(jobs)
    assert not jobs.busy and not list(jobs.worker_root.iterdir())


def test_progress_persistence_failure_reaps_worker_and_stops_accepting(jobs, runtime, monkeypatch):
    import sico.service.background_jobs as module
    (runtime.virtuoso.parent / 'mode').write_text('delay')
    key = submit(jobs)['job_id']
    wait_for(lambda: (jobs.worker_root / key / 'dispatched').exists(), bool)
    def fail(*args):
        raise OSError('full disk')
    monkeypatch.setattr(module, '_write', fail)
    wait_for(lambda: jobs.busy, lambda busy: not busy)
    row = status(jobs, key)
    assert row['state'] == 'interrupted'
    assert row['reason'] == 'persistence_failed'
    assert not Path('/proc', str(row['pid'])).exists()
    with pytest.raises(ValueError, match='persistence'):
        submit(jobs)


def test_bounded_queue_rejects_without_another_worker(jobs, runtime):
    jobs.queue_limit = 1
    (runtime.virtuoso.parent / 'mode').write_text('hang')
    first = submit(jobs)
    wait_for(lambda: status(jobs, first['job_id']), lambda r: r.get('pid'))
    second = submit(jobs)
    with pytest.raises(ValueError, match='queue is full'):
        submit(jobs)
    assert len(jobs.list_background(session_id='s')) == 2
    assert not (jobs.worker_root / second['job_id']).exists()


def test_facade_loads_explicit_private_configuration_off_caller_thread(tmp_path, runtime, monkeypatch):
    import threading
    from dataclasses import asdict
    import sico.service.background_service as module
    config = tmp_path / 'runtime.json'
    record = dict(protocol='cad_ai_background_runtime.v1', **asdict(runtime))
    config.write_text(json.dumps(record, default=str))
    config.chmod(0o600)
    from sico.service import background_config

    original = background_config.load_settings
    threads = []
    def load(path):
        threads.append(threading.get_ident())
        return original(path)
    monkeypatch.setattr(background_config, 'load_settings', load)
    facade = module.BackgroundService(tmp_path / 'background', config_path=str(config),
                                       worker_options={'shutdown_timeout': .2})
    try:
        row = facade.request('submit_background', 'inspect_schematic', TARGET,
                             session_id='s', origin=origin()).result(2)
        assert row['state'] == 'accepted' and threads != [threading.get_ident()]
        wait_for(lambda: facade.request('get_background_status', row['job_id'],
                                         session_id='s').result(2),
                 lambda r: r['state'] == 'completed')
    finally:
        facade.close()
        assert facade.wait(6)
    with pytest.raises(ValueError):
        facade.request('list_background', session_id='s')
    config.chmod(0o644)
    with pytest.raises(ValueError):
        original(config)


def test_busy_is_nonblocking_while_record_write_is_slow(jobs, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import sico.service.background_jobs as module
    entered, release = threading.Event(), threading.Event()
    write = module._write
    def slow(*args):
        entered.set()
        assert release.wait(3)
        return write(*args)
    monkeypatch.setattr(module, '_write', slow)
    with ThreadPoolExecutor(2) as pool:
        submission = pool.submit(submit, jobs)
        assert entered.wait(2)
        try:
            pool.submit(lambda: jobs.busy).result(.2)
        finally:
            release.set()
        submission.result(2)


def test_facade_shutdown_succeeds_with_full_api_queue(tmp_path, runtime, monkeypatch):
    import threading
    import sico.service.background_service as module
    entered, release = threading.Event(), threading.Event()
    from sico.service import background_config

    load = background_config.load_settings
    def slow(path):
        entered.set()
        assert release.wait(3)
        return load(path)
    monkeypatch.setattr(background_config, 'load_settings', slow)
    service = module.BackgroundService(tmp_path / 'background', config_path='')
    try:
        service.request('list_background', session_id='s')
        assert entered.wait(2)
        # Startup now occupies the worker; the first request is already queued.
        for _ in range(63):
            service.request('list_background', session_id='s')
        service.close()
        with pytest.raises(ValueError):
            service.request('list_background', session_id='s')
    finally:
        release.set()
        service.close()
        assert service.wait(5)


def test_orphaned_records_are_never_redispatched(tmp_path):
    from sico.service.background_jobs import BackgroundJobs, PROTOCOL
    from sico.service.background_worker import _write
    jobs = BackgroundJobs(tmp_path / 'background', None)
    key = 'a' * 32
    _write(jobs.records / (key + '.json'), dict(protocol=PROTOCOL, job_id=key,
           owner_session_id='s', service_id='b' * 32, state='running', accepted_at=time.time()))
    try:
        row = status(jobs, key)
        assert row['state'] == 'interrupted' and not row['owner_service_active']
        assert not row['control_available'] and not row['automatic_resume_allowed']
        assert jobs.cancel_background(key, session_id='s') == row
        assert not jobs.busy and not list(jobs.worker_root.iterdir())
    finally:
        jobs.close()
        assert jobs.wait(3)
