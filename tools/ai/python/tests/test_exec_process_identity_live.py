"""Host kernel verification of sandbox hints, without exposing a signal handle."""

import os
import sys

import pytest

from exec_live_fixtures import managed_configuration, managed_process, output, until


def test_managed_shell_identity_is_host_visible_and_directory_is_not_shell_pwd(managed_process):
    with managed_process() as (process, work):
        process.write(b"printf '%s\\n' \"$$\"; readlink /proc/self/ns/pid\n")
        rows = until(process, lambda rows: output(rows).count(b"\n") == 2)
        pid, namespace = output(rows).decode().splitlines()
        observed = process.observe_process(int(pid), namespace, "/bin/bash")
        assert observed.pid != int(pid)
        assert observed.cwd == str(work)
        assert observed.start > 0
        process.write(b"cd next; PWD=/forged; printf ready\n")
        until(process, lambda rows: output(rows) == b"ready")
        changed = process.observe_process(int(pid), namespace, "/bin/bash")
        assert (changed.pid, changed.start) == (observed.pid, observed.start)
        assert changed.cwd == str(work / "next")
        with pytest.raises(ValueError):
            process.observe_process(os.getpid(), os.readlink('/proc/self/ns/pid'), sys.executable)
        with pytest.raises(ValueError):
            process.observe_process(int(pid), namespace, sys.executable)
        process.close()
        with pytest.raises(RuntimeError):
            process.observe_process(int(pid), namespace, "/bin/bash")
