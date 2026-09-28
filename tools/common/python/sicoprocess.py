"""Linux supervisor for one local process tree, including reparented children.

This owns local OS descendants only. Scheduler jobs require AS-06 reconciliation.
No credentials or environment snapshots are serialized by this supervisor.
"""

import ctypes
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


def _children(pid):
    try:
        children = set()
        for task in Path(f"/proc/{pid}/task").iterdir():
            try:
                children.update(int(value) for value in (task / "children").read_text().split())
            except (FileNotFoundError, ProcessLookupError):
                pass
        return list(children)
    except (FileNotFoundError, ProcessLookupError):
        return []


def _identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19], fields[0]
    except (FileNotFoundError, ProcessLookupError):
        return None


def _tree():
    pending = _children(os.getpid())
    result = {}
    while pending:
        pid = pending.pop()
        if pid in result:
            continue
        identity = _identity(pid)
        if identity is not None:
            result[pid] = identity
            pending.extend(_children(pid))
    return result


def _signal_children(number):
    """Only this single-threaded parent's unreaped children are signal targets.

    A direct child cannot be reparented while we live. If it exits between the
    snapshot and kill, its zombie reserves the PID until our subsequent waitpid.
    Never signal a /proc-discovered grandchild: its parent could reap/reuse it.
    """
    if len(list(Path(f"/proc/{os.getpid()}/task").iterdir())) != 1:
        raise RuntimeError("Descendant cleanup requires an isolated single-threaded owner")
    for pid in _children(os.getpid()):
        try:
            os.kill(pid, number)
        except ProcessLookupError:
            pass


def _reap():
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def require_pidfds():
    """Check the interpreter and kernel before spawning an owned process tree."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise ValueError(
            "SiCo requires Python with os.pidfd_open and signal.pidfd_send_signal; "
            "select a compatible SICO_PYTHON and restart the project service")
    fd = None
    try:
        fd = os.pidfd_open(os.getpid())
        signal.pidfd_send_signal(fd, 0)
    except OSError as exc:
        raise ValueError(
            f"SiCo requires Linux pidfd support (errno {exc.errno}); "
            "use a compatible host and restart the project service") from None
    finally:
        if fd is not None:
            os.close(fd)


def require_subreaper():
    """Probe Linux ownership support without changing the caller's process role."""
    libc = ctypes.CDLL(None, use_errno=True)
    value = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(value), 0, 0, 0) != 0:  # PR_GET_CHILD_SUBREAPER
        raise ValueError("SiCo requires Linux child subreaper support")
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise ValueError("SiCo ownership requires exclusive child reaping (default SIGCHLD)")


def enable_subreaper():
    # Subreaping is established before spawn; double forks and setsid cannot
    # escape ownership by reparenting to the host's init process.
    require_subreaper()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Cannot own local descendants")


def reap_descendants(grace=.5):
    deadline = time.monotonic() + grace
    while True:
        _reap()
        if not _children(os.getpid()):
            return
        _signal_children(signal.SIGTERM if time.monotonic() < deadline else signal.SIGKILL)
        # Retain the supervisor for uninterruptible kernel I/O; never report
        # resources released while descendants remain. Callers wait with a budget.
        time.sleep(.02)


def run(control, command, *, forward_status=False, pass_fds=()):
    enable_subreaper()
    os.set_inheritable(control, False)
    stopped = []
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, lambda *_args: stopped.append(True))
    child = subprocess.Popen(command, start_new_session=True, pass_fds=pass_fds)
    try:
        while child.poll() is None and not stopped:
            ready, _, _ = select.select([control], [], [], .05)
            if ready:
                os.read(control, 1)  # Explicit stop or owner EOF, including SIGKILL.
                break
        status = child.returncode
        reap_descendants()
        return (status if status is not None else 1) if forward_status else 0
    finally:
        os.close(control)


if __name__ == "__main__":
    forward_status = sys.argv[2] == '--status'
    raise SystemExit(run(int(sys.argv[1]), sys.argv[3:] if forward_status else sys.argv[2:],
                         forward_status=forward_status))
