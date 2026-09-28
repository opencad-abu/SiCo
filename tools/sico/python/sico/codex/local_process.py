"""App-server pipe ownership and bounded shutdown of its local supervisor."""

import os
import subprocess
import sys
import threading
from ..installation import python_path
from sicoprocess import require_subreaper


class LocalProcess:
    def __init__(self, command, environment, cwd):
        require_subreaper()
        child_control, self._control = os.pipe()
        self._lock = threading.Lock()
        environment = dict(environment)
        environment["PYTHONPATH"] = python_path()
        try:
            self.child = subprocess.Popen(
                [sys.executable, "-s", "-c",
                 "import sys; from sicoprocess import run; "
                 "raise SystemExit(run(int(sys.argv[1]), sys.argv[2:]))",
                 str(child_control), *command],
                cwd=cwd, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, pass_fds=(child_control,),
                start_new_session=True)
        except BaseException:
            os.close(self._control)
            raise
        finally:
            os.close(child_control)

    def close(self):
        with self._lock:
            self.child.stdin.close()
            if self._control >= 0:
                os.close(self._control)
                self._control = -1
            try:
                self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Local descendants still require reconciliation") from None
            if self.child.returncode != 0:
                raise RuntimeError("Local descendant cleanup was not confirmed")
