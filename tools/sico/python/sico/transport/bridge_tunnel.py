"""Own an SSH forward to one remote loopback bridge, using a private Unix socket."""

from concurrent.futures import CancelledError
import os
import pwd
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time

from ..codex.local_process import LocalProcess


def node_name(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", value) is None
    ):
        raise ValueError("Invalid bridge execution node")
    return value


def node_metadata(environment=None):
    env = os.environ if environment is None else environment
    job = env.get("LSB_JOBID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", job):
        job = ""
    return dict(node=node_name(socket.gethostname()), job=job)


class BridgeTunnel:
    def __init__(self, node, port, *, deadline, cancelled=None):
        self.process = None
        self.directory = Path(tempfile.mkdtemp(prefix="sico-bridge-", dir="/tmp"))
        self.path = self.directory / "socket"
        try:
            command = [
                "ssh",
                "-N",
                "-T",
                "-l",
                pwd.getpwuid(os.getuid()).pw_name,
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "ServerAliveInterval=5",
                "-o",
                "ServerAliveCountMax=2",
                "-L",
                f"{self.path}:127.0.0.1:{port}",
                node_name(node),
            ]
            self.process = LocalProcess(command, dict(os.environ), self.directory)
            while not self.path.exists():
                if cancelled and cancelled():
                    raise CancelledError()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Remote bridge SSH connection timed out")
                if self.process.child.poll() is not None:
                    raise OSError(
                        "Remote bridge SSH unavailable; check host trust and login"
                    )
                time.sleep(0.025)
        except BaseException:
            self.close()
            raise

    def connect(self, timeout):
        connection = socket.socket(socket.AF_UNIX)
        try:
            connection.settimeout(timeout)
            connection.connect(str(self.path))
            return connection
        except BaseException:
            connection.close()
            raise

    def close(self):
        if self.process is not None:
            self.process.close()
            self.process.child.stdout.close()
            self.process = None
        if self.directory.exists():
            shutil.rmtree(self.directory)
