"""Bounded stdio transport for a site Python or tool subprocess."""

from __future__ import annotations

import subprocess
from typing import Optional, Sequence

from ..envelope import Envelope
from .base import Transport
from .stdio import StdioTransport


class SubprocessTransport(Transport):
    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd=None,
        env=None,
        identity="subprocess",
        close_timeout=2.0,
    ):
        self._identity = identity
        self._close_timeout = close_timeout
        self._process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._stdio = StdioTransport(
            self._process.stdout,
            self._process.stdin,
            identity=identity,
            close_streams=True,
        )

    def send(self, envelope: Envelope) -> None:
        self._stdio.send(envelope)

    def receive(self, deadline: Optional[float] = None) -> Envelope:
        return self._stdio.receive(deadline)

    def close(self, reason: str = "") -> None:
        self._stdio.close(reason)
        if self._process.poll() is not None:
            return
        try:
            self._process.wait(timeout=self._close_timeout)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=self._close_timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

    def identity(self) -> str:
        return self._identity

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


__all__ = ["SubprocessTransport"]
