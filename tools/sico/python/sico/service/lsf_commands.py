"""Bounded scheduler subprocess transport with finite non-model environment."""

import os
import selectors
import subprocess
import sys
import time
from ..installation import python_path


def command_environment(environment=None):
    source = os.environ if environment is None else environment
    names = {"PATH", "HOME", "USER", "LOGNAME", "LSF_ENVDIR", "LSF_SERVERDIR",
             "LSF_LIBDIR", "LSF_BINDIR", "LSF_CONFDIR"}
    result = {k: v for k, v in source.items() if k in names}
    result.update(LC_ALL="C", LANG="C")
    return result


def run_command(argv, *, environment, timeout):
    child_control, control = os.pipe()
    environment = dict(environment, PYTHONPATH=python_path())
    try:
        child = subprocess.Popen([sys.executable, '-s', '-c',
            'import sys; from sicoprocess import run; '
            'raise SystemExit(run(int(sys.argv[1]), sys.argv[2:], forward_status=True))',
            str(child_control), *argv],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, pass_fds=(child_control,), start_new_session=True)
    except BaseException:
        os.close(control)
        raise
    finally:
        os.close(child_control)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("LSF command result unconfirmed")
                if not selector.select(min(.1, remaining)):
                    continue
                data = os.read(child.stdout.fileno(), 65536)
                if not data:
                    selector.unregister(child.stdout)
                    break
                output.extend(data)
                if len(output) > 1024 * 1024:
                    raise ValueError("LSF output exceeds budget")
        code = child.wait(timeout=max(.001, deadline - time.monotonic()))
        return code, output.decode("utf-8", errors="strict")
    finally:
        # EOF requests the shared supervisor to reclaim only this local CLI tree.
        # Remote scheduler jobs remain owned by LSF and require explicit bkill.
        os.close(control)
        child.stdout.close()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            raise TimeoutError('LSF command descendants still require reconciliation') from None
