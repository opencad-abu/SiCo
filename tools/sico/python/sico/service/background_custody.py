"""Surviving subreaper for one background worker process tree.

The supervisor starts this custodian as its direct child. The custodian forks
the watchdog and remains the nearest ancestor subreaper until the watchdog and
every reparented descendant are gone. Its durable report is cleanup evidence
only when it proves that no owned children remain.
"""

import os
import signal
import time
import traceback
from pathlib import Path

from sicoprocess import enable_subreaper, reap_descendants_bounded
from .background_evidence import CUSTODY_PROTOCOL, WATCH_IDENTITY, owned_record, watchdog_record


def _identity(pid):
    from .background_watchdog import process_identity

    try:
        return process_identity(pid)
    except (OSError, ValueError, IndexError):
        return None


def _report(root, config, custodian, watcher):
    from .background_watchdog import boot_id

    identity = {k: config[k] for k in WATCH_IDENTITY}
    return dict(protocol=CUSTODY_PROTOCOL, **identity, boot_id=boot_id(),
                custodian=custodian, watcher=watcher, supervisor=config['supervisor'],
                phase='armed', tree_owned=True, process_stopped=False,
                children_remaining=None, reason=None, automatic_resume_allowed=False,
                started_at=time.time())


def _publish(root, report):
    from .background_worker import _write

    _write(root / 'custody.json', report)


def own(path, config, custodian, watcher_pid):
    """Own the watchdog and every descendant that outlives it."""
    from .background_watchdog import process_alive

    root = path.parent
    watcher = _identity(watcher_pid)
    report = _report(root, config, custodian, watcher)
    _publish(root, report)
    requested = []
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, lambda *_: requested.append(True))
    reason = None
    deadline = None
    try:
        while True:
            state = os.waitid(os.P_PID, watcher_pid,
                              os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if state is not None:
                break
            now = time.monotonic()
            if not process_alive(config['supervisor']):
                reason = reason or 'supervisor_lost'
            if (root / 'stop').exists():
                reason = reason or 'stop_requested'
            if requested:
                reason = reason or 'custodian_signal'
            if reason:
                try:
                    os.kill(watcher_pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
                grace = max(.5, config['shutdown_timeout'] + .5)
                deadline = deadline or now + grace
                if now >= deadline:
                    try:
                        os.kill(watcher_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    time.sleep(.02)
            time.sleep(.02)
        state = os.waitid(os.P_PID, watcher_pid, os.WEXITED)
        status = state.si_status if state.si_code == os.CLD_EXITED else 128 + state.si_status
        identity = {k: report[k] for k in WATCH_IDENTITY}
        if reason is None and process_alive(config['supervisor']):
            watchdog = watchdog_record(root, identity)
            execution = owned_record(root, 'execution.json', identity)
            execution_deadline = None if watchdog and watchdog.get('process_stopped') else (
                execution.get('deadline_monotonic') if execution else None)
            if isinstance(execution_deadline, (int, float)):
                while time.monotonic() < execution_deadline:
                    if ((root / 'stop').exists()
                            or not process_alive(config['supervisor'])
                            or watchdog_record(root, identity)):
                        break
                    time.sleep(.02)
        cleanup = reap_descendants_bounded(
            grace=config['shutdown_timeout'],
            deadline=time.monotonic() + max(.5, config['shutdown_timeout'] + .5))
        report.update(phase='finished', reason=reason or 'watcher_exited',
                      process_stopped=cleanup['stopped'],
                      children_remaining=cleanup['remaining'],
                      reaped=cleanup['reaped'], watcher=_identity(watcher_pid) or watcher,
                      launcher=(owned_record(root, 'watcher.json', identity) or {}).get('launcher'),
                      exit_code=status, finished_at=time.time())
        _publish(root, report)
        return status
    except BaseException:
        traceback.print_exc()
        try:
            os.kill(watcher_pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        cleanup = reap_descendants_bounded(
            grace=config['shutdown_timeout'],
            deadline=time.monotonic() + max(.5, config['shutdown_timeout'] + .5))
        report.update(phase='finished', reason='custodian_error',
                      process_stopped=cleanup['stopped'],
                      children_remaining=cleanup['remaining'],
                      reaped=cleanup['reaped'], finished_at=time.time())
        _publish(root, report)
        return 1


def run(function, connection_file):
    from .background_worker import _read
    from .background_watchdog import process_identity
    from ..transport.framing import strict_json

    path = Path(connection_file)
    config = strict_json(_read(path))
    enable_subreaper()
    custodian = process_identity(os.getpid())
    pid = os.fork()
    if pid == 0:
        try:
            status = function(connection_file, custodian=custodian)
        except BaseException:
            traceback.print_exc()
            status = 1
        os._exit(status)
    return own(path, config, custodian, pid)
