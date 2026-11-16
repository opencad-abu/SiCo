"""Local supervised Bash fixture; not a production backend or sandbox qualifier."""

from contextlib import contextmanager
import os
import time

import pytest

from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.local_process import LocalProcess
from sico.terminal.bash_control import BashControl
from sico.terminal.command_pipes import CommandPipes


def command(text="pwd", name="first", **fields):
    return CommandRequest(CommandRef("session", "terminal", name), "request-" + name,
                          fields.pop("cwd", None), "environment", shell="/bin/bash", text=text, **fields)


class BashFixture:
    def __init__(self, cwd):
        # The retained fd is the control pipe; shell stdout itself is /dev/null.
        self.owner = LocalProcess(["/bin/bash", "-c",
            "exec 9>&1; exec >/dev/null; exec /bin/bash --noprofile --norc"],
            dict(PATH="/usr/bin:/bin", HOME=str(cwd)), cwd)
        self.child = self.owner.child
        os.set_blocking(self.child.stdout.fileno(), False)
        self.control = BashControl()
        self.shell_pid = None
        self.pipes = []

    def submit(self, request):
        self.control.expect(request.ref.command_id)
        pipes = CommandPipes(request)
        self.pipes.append(pipes)
        self.child.stdin.write(pipes.dispatch(9, previous_status=self.control.last_status))
        return pipes

    def run(self, request, *, on_start=None, on_output=None):
        pipes = self.submit(request)
        observations = []
        streams = dict(stdout=bytearray(), stderr=bytearray())
        closed = set()
        control_ended, failure = False, None
        scope_ended = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                raw = None if control_ended else os.read(self.child.stdout.fileno(), 65536)
            except BlockingIOError:
                raw = None
            if raw == b"":
                control_ended = True
                try:
                    self.control.eof()
                except ValueError as exc:
                    failure = exc
                pipes.release_writers()
            for observation in self.control.feed(raw) if raw else ():
                pipes.observe_open()
                observations.append(observation)
                if observation.kind == 'started':
                    self.shell_pid = observation.value
                if observation.kind == "started" and on_start:
                    on_start(observation)
                elif observation.kind in ("finished", "rejected"):
                    pipes.release_writers()
            if (scope_ended and raw is None and failure is None
                    and (not observations or observations[-1].kind not in ('finished', 'rejected'))):
                try:
                    self.control.eof()
                except ValueError as exc:
                    failure = exc
                pipes.release_writers()
            try:
                scope_ended |= pipes.scope_ended(self.shell_pid)
            except (OSError, ValueError):
                try:
                    self.control.eof()
                except ValueError as exc:
                    failure = exc
                pipes.release_writers()
                failure = failure or ValueError('Bash observation lost before command completion')
            progress = False
            for stream in streams:
                if not observations or stream in closed:
                    continue
                data = pipes.read(stream)
                if data == b"":
                    closed.add(stream)
                elif data:
                    progress = True
                    streams[stream].extend(data)
                    if on_output:
                        on_output(stream, data)
            if failure is not None and not progress:
                raise failure
            if scope_ended and len(closed) == 2 and observations and observations[-1].kind in ("finished", "rejected"):
                pipes.close()
                return observations, {key: bytes(value) for key, value in streams.items()}
            time.sleep(.002)
        raise TimeoutError("Local Bash fixture did not reach a drained boundary")

    def close(self):
        self.owner.close(require_confirmed=True)
        self.child.stdout.close()
        for pipes in self.pipes:
            pipes.close()


@pytest.fixture
def bash_session(tmp_path):
    @contextmanager
    def opened():
        fixture = BashFixture(tmp_path)
        try:
            yield fixture
        finally:
            fixture.close()
    return opened
