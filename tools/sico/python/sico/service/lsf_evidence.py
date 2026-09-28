"""Captured scheduler identity and site configuration for one original submission."""

import hashlib
import re
from contextlib import contextmanager
from dataclasses import asdict

from ..storage.history import owned_directory
from ..storage.project_files import read_record, write_record
from ..transport.framing import ProtocolError
from .lsf_config import lsf_settings
from .lsf_scheduler import LsfScheduler


def path(root, job_id):
    return root / 'scheduled' / job_id / 'scheduler.json'


def save(root, row):
    write_record(path(root, row['job_id']), row)


def initial(request, config, manifest):
    return dict(job_id=request.job_id, worker_id=request.worker_id, phase='submitting',
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        config=asdict(config), scheduler=dict(cluster=config.cluster, owner=config.owner,
        name='sico-' + request.job_id, job_id=None, state='UNKWN'))


def load(root, row):
    try:
        return _load(root, row)
    except ProtocolError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ProtocolError('Invalid retained scheduler evidence') from exc


def _load(root, row):
    location = path(root, row['job_id'])
    for directory in (location.parent.parent, location.parent):
        owned_directory(directory)
    record = read_record(location)
    if (not isinstance(record, dict) or set(record) != {
            'job_id', 'worker_id', 'phase', 'config', 'scheduler', 'manifest_sha256'}
            or any(record.get(k) != row[k] for k in ('job_id', 'worker_id'))
            or record['phase'] not in {'submitting', 'associated', 'cancel_requested'}):
        raise ProtocolError('LSF evidence belongs to another admission')
    from .background_worker import _read

    raw = _read(location.with_name('manifest.json'), limit=16384)
    if hashlib.sha256(raw).hexdigest() != record['manifest_sha256']:
        raise ProtocolError('Captured node input manifest changed')
    config = lsf_settings(record['config'])
    expected = record['scheduler']
    if (not isinstance(expected, dict) or set(expected) != {
            'cluster', 'owner', 'name', 'job_id', 'state'}
            or expected['cluster'] != config.cluster or expected['owner'] != config.owner
            or expected['name'] != 'sico-' + row['job_id']
            or (expected['job_id'] is not None and (not isinstance(expected['job_id'], str)
                or not re.fullmatch(r'[1-9][0-9]{0,19}', expected['job_id'])))
            or expected['state'] not in {'PEND', 'RUN', 'PSUSP', 'USUSP', 'SSUSP', 'WAIT',
                                         'DONE', 'EXIT', 'UNKWN', 'ZOMBI'}):
        raise ProtocolError('Captured LSF identity changed')
    return record, config


def scheduler(config, supplied=None):
    if supplied is not None:
        if supplied.config != config:
            raise ProtocolError('Scheduler settings changed during observation')
        return supplied
    return LsfScheduler(config)


@contextmanager
def transaction(root, job_id):
    """Serialize read/observe/write across reopened readers, without a second state store."""
    import fcntl
    from sicolock import lock as state_lock
    import os

    from ..storage.journal import open_private

    directory = path(root, job_id).parent
    owned_directory(directory)
    fd = open_private(directory / 'scheduler.lock', os.O_CREAT | os.O_RDWR)
    try:
        state_lock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
