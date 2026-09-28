"""Explicit command wrapper for the shared input-method session."""

import os
import signal
import subprocess
import sys

from sicopaths import installation

from .session import input_session, lsf_session


def main(arguments=None):
    args = sys.argv[1:] if arguments is None else arguments
    if len(args) < 2 or args[0] != "--":
        print("usage: sico-ai-pinyin -- COMMAND [ARG ...]", file=sys.stderr)
        return 2
    command = args[1:]
    if not lsf_session(os.environ):
        os.execvp(command[0], command)
    stopped = []
    previous = {}
    child = None
    control_write = None

    def stop(number, _frame):
        stopped.append(number)
        nonlocal control_write
        if control_write is not None:
            os.close(control_write)
            control_write = None

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[number] = signal.signal(number, stop)
    try:
        with input_session():
            if stopped:
                return 128 + stopped[0]
            control = os.environ.get("SICO_AI_CONTROL_FD", "")
            descriptors = (int(control),) if control.isdigit() else ()
            read_fd, control_write = os.pipe()
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from sicoprocess import run; "
                    "status=run(int(sys.argv[2]), sys.argv[4:], "
                    "forward_status=True, pass_fds=tuple(map(int, filter(None, "
                    "sys.argv[3].split(','))))); "
                    "raise SystemExit(status if status >= 0 else 128 - status)")
            try:
                child = subprocess.Popen([sys.executable, "-s", "-c", code,
                    str(installation().path("tools/common/python")), str(read_fd),
                    ",".join(map(str, descriptors)), *command],
                    pass_fds=(read_fd, *descriptors), start_new_session=True)
            finally:
                os.close(read_fd)
            if stopped and control_write is not None:
                os.close(control_write)
                control_write = None
            status = child.wait()
            return 128 + stopped[0] if stopped else status if status >= 0 else 128 - status
    finally:
        if control_write is not None:
            os.close(control_write)
        if child is not None and child.poll() is None:
            child.wait(timeout=5)
        for number, handler in previous.items():
            signal.signal(number, handler)
