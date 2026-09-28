"""Independent Linux worker launcher, deadlines and retained cleanup evidence."""
import os
import signal
import subprocess
import time
from pathlib import Path

from sicoprocess import enable_subreaper, reap_descendants

PROTOCOL = 'cad_ai_background_watchdog.v1'


def process_identity(pid):
    fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, start=fields[19], group=int(fields[2]), state=fields[0])


def process_alive(identity):
    try:
        actual = process_identity(identity['pid'])
        return actual['start'] == identity['start'] and actual['state'] != 'Z'
    except (OSError, KeyError, ValueError, IndexError):
        return False


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def wake_guardian(identity):
    """Resume only the captured supervisor, so it can finish owning its descendants."""
    try:
        fd = os.pidfd_open(identity['pid'])
    except (ProcessLookupError, AttributeError):
        return
    except OSError as exc:
        if exc.errno == 38:  # Old kernel: retain reconciliation; no unsafe PID signal.
            return
        raise
    try:
        if process_alive(identity):
            signal.pidfd_send_signal(fd, signal.SIGCONT)
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)


def resume_owned_guardian(process):
    """Only the worker thread that creates/waits this Popen may call this.

    Unlike recovery from a persisted PID, this caller is the exclusive reaper
    of its direct child. Exit reserves that child's PID until our next wait.
    """
    if process.poll() is None:
        try:
            os.kill(process.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass


def stop_tree(process, timeout):
    """Confirm local worker descendants, including children outside its group."""
    process.poll()
    reap_descendants(timeout)
    process.wait(timeout=max(1, timeout))
    return True


def watch_worker(connection_file, *, custodian=None):
    from .background_environment import worker_environment
    from ..storage.journal import open_private
    from ..transport.framing import strict_json
    from .background_worker import _read, _write
    path = Path(connection_file)
    enable_subreaper()
    root = path.parent
    config = strict_json(_read(path))
    identity = {k: config[k] for k in ('job_id', 'worker_id', 'instance_id', 'generation')}
    supervisor = config['supervisor']
    guardian = process_identity(os.getpid())
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    _write(root / 'watcher.json', dict(protocol=PROTOCOL, **identity,
           boot_id=boot_id(), guardian=guardian, custodian=custodian or guardian, supervisor=supervisor, phase='armed',
           tree_owned=True))
    # No Virtuoso exists until the supervisor has durably observed this guardian.
    deadline = time.monotonic() + config['startup_timeout']
    while not (root / 'launch').exists():
        if (not process_alive(supervisor) or time.monotonic() >= deadline
                or stopping or (root / 'stop').exists()):
            return 0
        time.sleep(.02)
    process, reason, stopped = None, 'startup_failed', True
    try:
        if not process_alive(supervisor):
            return 0
        with os.fdopen(open_private(root / 'logs/stdout.log', os.O_CREAT | os.O_EXCL | os.O_WRONLY), 'wb') as out:
            with os.fdopen(open_private(root / 'logs/stderr.log', os.O_CREAT | os.O_EXCL | os.O_WRONLY), 'wb') as err:
                process = subprocess.Popen(config['command'], cwd=root, stdin=subprocess.DEVNULL,
                    stdout=out, stderr=err, start_new_session=True, umask=0o077,
                    env=worker_environment(root))
        launcher = process_identity(process.pid)
        _write(root / 'watcher.json', dict(protocol=PROTOCOL, **identity,
               boot_id=boot_id(), guardian=guardian, custodian=custodian or guardian, supervisor=supervisor,
               launcher=launcher, phase='started', started_at=time.time(), tree_owned=True))
        reason = 'startup_timeout'
        deadline = time.monotonic() + config['startup_timeout']
        execution = None
        execution_finished = False
        stop_deadline = None
        while True:
            # Inspect without waitpid: keep group identity reserved until cleanup.
            if not process_alive(launcher):
                reason = 'worker_exited'
                break
            if not process_alive(supervisor):
                reason = 'supervisor_lost'
                break
            now = time.monotonic()
            if stopping or (root / 'stop').exists():
                stop_deadline = stop_deadline or now + config['shutdown_timeout']
            if stop_deadline is not None and now >= stop_deadline:
                reason = 'stop_requested'
                break
            if execution is None and (root / 'execution.json').exists():
                execution = strict_json(_read(root / 'execution.json'))
                if any(execution.get(k) != v for k, v in identity.items()):
                    raise ValueError('Watchdog execution identity mismatch')
                deadline = execution['deadline_monotonic']
                reason = 'execution_timeout'
            if execution is not None and not execution_finished and (root / 'execution-finished').exists():
                execution_finished = True
                deadline = now + config['shutdown_timeout']
                reason = 'stop_requested'
            if now >= deadline:
                break
            time.sleep(.02)
    finally:
        if process is not None:
            stopped = stop_tree(process, min(.5, config['shutdown_timeout']))
        _write(root / 'watchdog.json', dict(protocol=PROTOCOL, **identity,
               boot_id=boot_id(), reason=reason, process_stopped=stopped,
               exit_code=process.returncode if process else None,
               finished_at=time.time(), automatic_resume_allowed=False))
    return 0 if stopped else 1
