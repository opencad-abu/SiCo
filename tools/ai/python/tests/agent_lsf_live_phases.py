"""One real backend-owner process per acceptance phase; production queue owns all work."""

import time

from sico.core.contracts import BoundContext
from sico.service.background_config import captured_backend
from sico.service.background_jobs import BackgroundJobs
from sico.service.background_retained import pending
from sico.service.project_service import ProjectBusy, ProjectServiceLease
from sico.storage.project_files import read_record, write_record

SESSION = 'as06-live'


def status_summary(row):
    """Finite evidence without design contents, environment or full command output."""
    return {key: row[key] for key in (
        'job_id', 'worker_id', 'service_id', 'state', 'scheduler', 'scheduler_verified',
        'cancel_requested', 'cancel_outcome', 'operation_dispatched', 'automatic_resume_allowed',
        'artifact', 'reason') if key in row}


def _until(manager, job_id, site, predicate):
    deadline = time.monotonic() + site.wait_seconds
    while True:
        row = manager.get_background_status(job_id, session_id=SESSION)
        if predicate(row):
            return row
        if time.monotonic() >= deadline:
            raise TimeoutError('Original job remains unconfirmed')
        time.sleep(min(.2, site.execution['lsf']['poll_seconds']))


def finished_before_action(manager, job_id, site, row):
    if row['state'] == 'completed' and row.get('scheduler', {}).get('state') == 'DONE':
        result = manager.read_background_result(job_id, row.get('artifact'), session_id=SESSION)
        if result['inspection']['cellview'] != site.target:
            raise AssertionError('Completed result belongs to another target')
        return dict(outcome='not_exercised', reason='verified_completion_before_action',
                    row=status_summary(row))
    return dict(outcome='needs_reconcile', reason='execution_not_verified', row=status_summary(row))


def submit(manager, job_id, site, root):
    origin = BoundContext('as06-site', job_id, 'saved-oa',
                          dict(source='site_saved_oa', cwd=str(root), cellview=site.target))
    row = manager.submit_background('inspect_schematic', site.target, session_id=SESSION,
        origin=origin, operation_id=job_id, environment=site.environment)
    row = _until(manager, job_id, site, lambda r: bool(r.get('scheduler', {}).get('job_id'))
                 or r['state'] in {'interrupted', 'background_unavailable', 'queue_timeout'})
    active = row.get('scheduler', {}).get('state') in {
        'PEND', 'RUN', 'WAIT', 'PSUSP', 'USUSP', 'SSUSP'}
    if active:
        return dict(outcome='observed', row=status_summary(row))
    return finished_before_action(manager, job_id, site, row)


def observe(manager, job_id, site, root):
    prior = read_record(root / 'submit.json')['row']['scheduler']
    row = _until(manager, job_id, site, lambda r: r.get('scheduler', {}).get('state')
                 in {'DONE', 'EXIT'})
    scheduler = row['scheduler']
    if any(scheduler[key] != prior[key] for key in ('job_id', 'name', 'cluster', 'owner')):
        raise AssertionError('Restart associated a different scheduler job')
    result = manager.read_background_result(job_id, row.get('artifact'), session_id=SESSION)
    if result['inspection']['cellview'] != site.target or row.get('automatic_resume_allowed'):
        raise AssertionError('Result target or replay policy changed')
    return dict(outcome='passed', row=status_summary(row), pending=pending(row),
                target=result['inspection']['cellview'])


def cancel(manager, job_id, site, root):
    row = manager.get_background_status(job_id, session_id=SESSION)
    if row.get('scheduler', {}).get('state') in {'DONE', 'EXIT'}:
        return finished_before_action(manager, job_id, site, row)
    requested = manager.cancel_background(job_id, session_id=SESSION)
    row = _until(manager, job_id, site, lambda r: r.get('scheduler', {}).get('state')
                 in {'DONE', 'EXIT'})
    prior = read_record(root / 'submit.json')['row']['scheduler']
    if any(row['scheduler'][key] != prior[key] for key in ('job_id', 'name', 'cluster', 'owner')):
        raise AssertionError('Cancellation observed another scheduler job')
    exercised = (requested.get('cancel_outcome') == 'requested'
                 and row['scheduler']['state'] == 'EXIT')
    if not exercised:
        return dict(finished_before_action(manager, job_id, site, row),
                    request=status_summary(requested))
    return dict(outcome='passed',
                row=status_summary(row), request=status_summary(requested),
                pending=pending(row), external_outcome='unconfirmed')


def run_phase(site, root, phase):
    report = dict(phase=phase, outcome='needs_reconcile')
    manager = None
    try:
        admission = read_record(root / 'run.json')
        backend = captured_backend(root / 'background', site.execution)
        manager = BackgroundJobs(root / 'background', None,
            backends={'lsf': backend}, execution_backend='lsf')
        report.update({'submit': submit, 'observe': observe, 'cancel': cancel}[phase](
            manager, admission['job_id'], site, root))
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    finally:
        if manager is not None:
            manager.close()  # Detach only. Never cancel in failure cleanup.
            if not manager.wait(3):
                report.update(outcome='needs_reconcile', owner_close='unconfirmed')
        write_record(root / (phase + '.json'), report)


def lock_contender(project, report):
    try:
        with ProjectServiceLease.acquire(project):
            row = dict(outcome='unexpected_acquisition')
    except ProjectBusy:
        row = dict(outcome='blocked')
    except Exception as exc:
        row = dict(outcome='unconfirmed', error_type=type(exc).__name__)
    write_record(report, row)
