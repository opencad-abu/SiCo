"""B2 asynchronous API against compiled IC231 and isolated synthetic OA only."""
import time

from test_agent_b0_background import TARGET, design, fingerprints
from test_agent_b1_ic231 import compiled
from test_agent_b1_worker import assert_reaped
from test_agent_b2_jobs import wait_for

from sico.core.contracts import BoundContext
from sico.service.background_jobs import BackgroundJobs
from sico.service.background_service import BackgroundService


def origin():
    return BoundContext('b2-front', 'front-generation', 'front-target',
                        {'cellview': {k: TARGET[k] for k in ('lib', 'cell', 'view')}})


def test_native_async_accept_queue_cancel_and_verified_result(design, compiled, tmp_path):
    before = fingerprints(design)
    service = BackgroundService(tmp_path / 'background', runtime=compiled)
    try:
        started = time.monotonic()
        first = service.request('submit_background', 'inspect_schematic', TARGET,
                                 session_id='native-b2', origin=origin()).result(2)
        assert first['state'] == 'accepted' and time.monotonic() - started < 2
        get = lambda: service.request('get_background_status', first['job_id'],
                                      session_id='native-b2').result(2)
        active = wait_for(get, lambda row: row.get('pid'), timeout=20)
        assert active['state'] == 'starting'
        queued = service.request('submit_background', 'inspect_schematic', TARGET,
                                  session_id='native-b2', origin=origin()).result(2)
        cancelled = service.request('cancel_background', queued['job_id'],
                                     session_id='native-b2').result(2)
        assert cancelled['state'] == 'cancelled'
        assert not (tmp_path / 'background/workers' / queued['job_id']).exists()
        done = wait_for(get, lambda row: row.get('finished_at'), timeout=35)
        assert done['state'] == 'completed', done
        assert_reaped(done)
        verified = service.request('read_background_result', first['job_id'], done['artifact'],
                                    session_id='native-b2').result(2)
        assert verified['inspection']['instances'][0]['terminals'][0]['net'] == 'SAVED'
        assert verified['origin'] == origin().record()
        assert not verified['automatic_resume_allowed']
        assert verified['instance_id'] != origin().instance_id
        assert verified['generation'] != origin().generation
        assert verified['generated_at'] >= first['accepted_at']
        assert fingerprints(design) == before
    finally:
        service.close()
        assert service.wait(20)
    reopened = BackgroundJobs(tmp_path / 'background', None)
    try:
        assert reopened.read_background_result(done['job_id'], done['artifact'],
                                               session_id='native-b2') == verified
        assert not reopened.busy
    finally:
        reopened.close()
        assert reopened.wait(3)


def test_native_startup_cancel_reaps_without_inspection(design, compiled, tmp_path):
    before = fingerprints(design)
    jobs = BackgroundJobs(tmp_path / 'background', compiled,
                          worker_options={'shutdown_timeout': 1})
    try:
        accepted = jobs.submit_background('inspect_schematic', TARGET,
                                           session_id='native-cancel', origin=origin())
        get = lambda: jobs.get_background_status(accepted['job_id'], session_id='native-cancel')
        wait_for(get, lambda row: row.get('pid'), timeout=20)
        jobs.cancel_background(accepted['job_id'], session_id='native-cancel')
        done = wait_for(get, lambda row: row.get('finished_at'), timeout=15)
        assert done['state'] == 'cancelled', done
        assert not done['operation_dispatched'] and not done.get('artifact')
        assert_reaped(done)
        assert fingerprints(design) == before
    finally:
        jobs.close()
        assert jobs.wait(10)
