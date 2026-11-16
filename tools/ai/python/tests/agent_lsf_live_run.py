"""Private durable acceptance evidence and bounded submit-host child ownership."""

import multiprocessing
import os
import platform
import tempfile
import uuid
from pathlib import Path

from agent_lsf_live_phases import lock_contender, run_phase

from sico.service.project_service import ProjectServiceLease
from sico.storage.journal import open_private
from sico.storage.project_files import read_record, write_record


def create_run(site, scenario):
    if scenario not in {'detach_reassociate', 'cancel', 'nas_lock'}:
        raise ValueError('Unknown live acceptance scenario')
    root = Path(tempfile.mkdtemp(prefix='as06-' + scenario + '-', dir=str(site.shared_tests)))
    lsf = site.execution['lsf']
    write_record(root / 'run.json', dict(contract='cadai.as06.evidence.v1',
        scenario=scenario, job_id=uuid.uuid4().hex, submit_host=platform.node(),
        python=platform.python_version(), target=site.target,
        cluster=lsf['cluster'], owner=lsf['owner'], queue=lsf['queue'],
        slots=lsf['slots'], wall_minutes=lsf['wall_minutes'], resource=lsf['resource'],
        context_sha256=site.execution['runtime']['context_sha256'],
        worker_version=site.execution['runtime']['version'], outcome='prepared',
        excluded=['gui_detach', 'project_service_restart', 'cross_node_nas_lock',
                  'service_allocation_termination']))
    return root


def _child(target, args, timeout):
    child = multiprocessing.get_context('spawn').Process(target=target, args=args)
    child.start()
    child.join(timeout)
    if child.is_alive():
        # Only this submit-host acceptance owner. Remote work remains in LSF.
        child.terminate()
        child.join(4)
        if child.is_alive():
            child.kill()
            child.join(4)
        return False
    return child.exitcode == 0


def phase(site, root, action):
    if action not in {'submit', 'observe', 'cancel'}:
        raise ValueError('Unknown live acceptance phase')
    report = root / (action + '.json')
    try:
        fd = open_private(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        raise ValueError('Inspect original evidence; do not repeat a live phase') from None
    else:
        os.close(fd)  # An interrupted claim remains evidence, never permission to retry.
    write_record(report, dict(phase=action, outcome='needs_reconcile', reason='owner_started'))
    stopped = _child(run_phase, (site, root, action), site.wait_seconds + 8)
    row = read_record(report)
    if not stopped:
        row = dict(row, outcome='needs_reconcile', owner_exit='unconfirmed')
        write_record(report, row)
    return row


def verify_lock(site, root):
    project = root / 'lock-project'
    project.mkdir(mode=0o700)
    report = root / 'nas-lock.json'
    alias = ((site.alias_tests / root.name / project.name)
             if site.alias_tests is not None else project)
    with ProjectServiceLease.acquire(project) as lease:
        expected = lease.project_id
        stopped = _child(lock_contender, (alias, report), 10)
        row = read_record(report) if report.exists() else dict(outcome='unconfirmed')
        if not stopped or row['outcome'] != 'blocked':
            raise AssertionError('Shared lock exclusion is unconfirmed')
    with ProjectServiceLease.acquire(alias) as lease:
        if lease.project_id != expected:
            raise AssertionError('Alias changed project identity')
    row.update(outcome='passed', alias_exercised=site.alias_tests is not None,
               cross_node_verified=False, project_id=expected)
    write_record(report, row)
    return row
