"""Reconcile retained worker evidence without resubmitting any SKILL operation."""
import fcntl
from sicolock import lock as state_lock
import os
import shutil
import signal
import time
from pathlib import Path

from ..storage.journal import open_private, sync_directory
from ..transport.framing import strict_json
from .background_watchdog import boot_id, process_alive, process_identity, wake_guardian
from .background_worker import IDENTITY, PROTOCOL, _read, _write, read_background_artifact


def stop_abandoned(watcher):
    """Fallback only for the exact live leader; pidfds cannot target a reused PID."""
    if watcher.get('boot_id') != boot_id():
        return True
    leader = watcher.get('launcher')
    if not leader:
        return True
    if not process_alive(leader):
        # No matching leader: never signal a new process/group at the old PID.
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    p = process_identity(int(entry.name))
                    if p['group'] == leader['pid'] and p['state'] != 'Z':
                        return False
                except (OSError, ValueError, IndexError):
                    continue
        return True
    if process_identity(leader['pid'])['group'] != leader['pid']:
        return False
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        return False
    handles = []
    try:
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                p = process_identity(int(entry.name))
                if p['group'] != leader['pid'] or p['state'] == 'Z':
                    continue
                fd = os.pidfd_open(p['pid'])
                if not process_alive(p):
                    os.close(fd)
                    continue
                handles.append((fd, p))
            except (OSError, ValueError, IndexError):
                continue
        # Recheck the original leader after capturing handles, before any signal.
        if not process_alive(leader) or not any(p['pid'] == leader['pid'] for _, p in handles):
            return False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for fd, _ in handles:
                try:
                    signal.pidfd_send_signal(fd, sig)
                except ProcessLookupError:
                    pass
            time.sleep(.1)
        return all(not process_alive(p) for _, p in handles)
    finally:
        for fd, _ in handles:
            os.close(fd)


def stop_guardian(identity):
    if not process_alive(identity):
        return True
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        return False
    fd = os.pidfd_open(identity['pid'])
    try:
        if not process_alive(identity):
            return True
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        time.sleep(.05)
        return not process_alive(identity)
    finally:
        os.close(fd)


def remove_temporary(directory, job):
    context = job.get('context_name')
    if context and (Path(context).name != context or not context.endswith('.cxt')):
        raise ValueError('Invalid retained context name')
    for name in ('connection.json', 'watch.json', 'bootstrap.il', 'launch', 'stop', context):
        if name:
            (directory / name).unlink(missing_ok=True)
    scratch = directory / 'scratch'
    if scratch.is_symlink():
        raise ValueError('Invalid scratch directory')
    if scratch.exists():
        shutil.rmtree(scratch)
    sync_directory(directory)


def recover_record(records, worker_root, row):
    """Caller has checked the owner lease. Serialize multiple recovery readers."""
    path = records / (row['job_id'] + '.json')
    fd = open_private(records / (row['job_id'] + '.recovery.lock'), os.O_CREAT | os.O_RDWR)
    try:
        state_lock(fd, fcntl.LOCK_EX)
        row = strict_json(_read(path))
        if row.get('recovered') and row['state'] not in {'recovering', 'cleanup_failed'}:
            return row
        original = row.get('state')
        result = dict(row, recovered=True, recovered_at=time.time(), automatic_resume_allowed=False)
        directory = worker_root / row['job_id']
        try:
            if directory.is_symlink():
                raise ValueError('Invalid worker directory')
            receipt = strict_json(_read(directory / 'receipt.json'))
            if receipt.get('protocol') != PROTOCOL or any(receipt.get(k) != row.get(k)
                    for k in ('job_id', 'worker_id')):
                raise ValueError('Worker receipt identity mismatch')
            job = strict_json(_read(directory / 'job.json'))
            if job.get('protocol') != PROTOCOL or any(receipt.get(k) != job.get(k) for k in IDENTITY):
                raise ValueError('Worker job identity mismatch')
            for k in IDENTITY + ('request_id',):
                if row.get(k) is not None and row[k] != receipt.get(k):
                    raise ValueError('Manager receipt identity mismatch')
            watcher = None
            if (directory / 'watcher.json').exists():
                watcher = strict_json(_read(directory / 'watcher.json'))
                if any(watcher.get(k) != job[k] for k in ('job_id', 'worker_id', 'instance_id', 'generation')):
                    raise ValueError('Watchdog identity mismatch')
                if watcher['boot_id'] == boot_id() and process_alive(watcher['guardian']):
                    _write(directory / 'stop', b'1')
                    if watcher.get('tree_owned'):
                        wake_guardian(watcher['guardian'])
                    since = row.get('recovery_started_at', time.time())
                    if time.time() - since < 2:
                        result.update(state='recovering', reason='waiting_for_watchdog',
                                      recovery_started_at=since, recovery=dict(process_stopped=False))
                        _write(path, result)
                        return result
                    if watcher.get('tree_owned'):
                        result.update(state='cleanup_failed', reason='local_tree_unconfirmed',
                                      recovery=dict(process_stopped=False))
                        _write(path, result)
                        return result
                    if not stop_abandoned(watcher) or not stop_guardian(watcher['guardian']):
                        result.update(state='cleanup_failed', reason='orphan_process_unconfirmed',
                                      recovery=dict(process_stopped=False))
                        _write(path, result)
                        return result
            report = None
            if (directory / 'watchdog.json').exists():
                report = strict_json(_read(directory / 'watchdog.json'))
                if any(report.get(k) != job[k] for k in ('job_id', 'worker_id', 'instance_id', 'generation')):
                    raise ValueError('Watchdog report identity mismatch')
            if watcher and watcher.get('tree_owned') and not report:
                stop_abandoned(watcher)  # Contain known members; this cannot prove the whole tree.
            stopped = (bool(report and report.get('process_stopped'))
                       if watcher and watcher.get('tree_owned') else
                       stop_abandoned(watcher) if watcher else (
                not receipt.get('pid') or bool(receipt.get('finished_at') and receipt.get('cleanup')
                                              and all(receipt['cleanup'].values()))))
            if not stopped:
                result.update(state='cleanup_failed', reason='orphan_process_unconfirmed',
                              recovery=dict(process_stopped=False))
            else:
                remove_temporary(directory, job)
                result.update({k: v for k, v in receipt.items() if k != 'protocol'})
                result['recovery'] = dict(process_stopped=True, temporary_removed=True,
                                          prior_state=original, watchdog=report)
                if receipt['state'] == 'completed':
                    if not receipt.get('cleanup') or not all(receipt['cleanup'].values()):
                        raise ValueError('Worker cleanup not confirmed')
                    read_background_artifact(directory)
                    result.update(state='completed', reason='verified_worker_receipt')
                elif report and report['reason'] == 'execution_timeout':
                    result.update(state='worker_timeout', reason='execution_timeout', ok=False)
                elif receipt.get('finished_at'):
                    result.update(reason=receipt.get('reason', 'retained_worker_receipt'))
                else:
                    dispatched = bool(receipt.get('request_id'))
                    result.update(state='running_unknown' if dispatched else 'interrupted',
                                  reason='supervisor_lost', ok=False)
                result['finished_at'] = receipt.get('finished_at') or time.time()
        except FileNotFoundError:
            state = 'running_unknown' if row.get('request_id') else 'interrupted'
            if original == 'completed':
                state = 'result_invalid'
            result.update(state=state, reason='owner_service_unavailable', ok=False)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(state='result_invalid', reason='recovery_evidence_invalid',
                          error_type=type(exc).__name__, ok=False)
        if result['state'] != 'completed':
            result.pop('artifact', None)
        _write(path, result)
        return result
    finally:
        os.close(fd)
