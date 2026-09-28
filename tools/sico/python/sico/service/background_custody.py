"""Keep an old-kernel watchdog waitable so recovery can resume its owned child."""

import os
import signal
import time
from pathlib import Path

from sicoprocess import enable_subreaper, reap_descendants, require_pidfds


def run(function, connection_file):
    try:
        require_pidfds()
    except ValueError:
        pass
    else:
        return function(connection_file)
    from .background_watchdog import process_alive, process_identity
    from .background_worker import _read
    from ..transport.framing import strict_json

    path = Path(connection_file)
    config = strict_json(_read(path))
    enable_subreaper()
    identity = process_identity(os.getpid())
    pid = os.fork()
    if pid == 0:
        try:
            status = function(connection_file, custodian=identity)
        except BaseException:
            import traceback
            traceback.print_exc()
            status = 1
        os._exit(status)
    requested = []
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, lambda *_: requested.append(True))
    try:
        while True:
            state = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if state is not None:
                return state.si_status if state.si_code == os.CLD_EXITED else 128 + state.si_status
            if requested or (path.parent / 'stop').exists() or not process_alive(config['supervisor']):
                # This unreaped direct child cannot have its PID reused. The
                # watchdog resumes its own normal evidence/cleanup protocol.
                os.kill(pid, signal.SIGCONT)
                if requested:
                    os.kill(pid, signal.SIGTERM)
            time.sleep(.02)
    finally:
        reap_descendants(.5)
