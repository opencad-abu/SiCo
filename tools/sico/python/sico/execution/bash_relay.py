"""Run one Bash inside the already managed sandbox; outer owner reaps its tree.

This relay owns command descriptors, not a second supervisor, session journal,
permission policy or model route. Only trusted composition may launch it.
"""

import base64
import fcntl
import os
import pty
import select
import subprocess
import termios
import time

from ..core.terminal_commands import CommandRequest, exact_fields
from ..terminal.bash_control import BashControl, MAX_CONTROL_BYTES
from ..terminal.command_pipes import CommandPipes
from .bash_pty import PtyCommandRunner
from . import relay_wire as wire


class BashRelay:
    def __init__(self, environment, mode="pipes"):
        if mode not in ("pipes", "pty"):
            raise ValueError("Unsupported Bash relay mode")
        self.mode = mode
        self.pty_master = None
        self.control_read, control_write = os.pipe()
        continue_read, self.continue_write = os.pipe()
        try:
            if mode == "pipes":
                self.shell = subprocess.Popen(
                    ['/bin/bash', '--noprofile', '--norc'],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, pass_fds=(control_write, continue_read),
                    bufsize=0, env=environment)
                self.pty_fd = None
            else:
                master, slave = pty.openpty()
                self.pty_master, self.pty_fd = master, slave
                shell_environment = dict(
                    environment, PS1="", PS2="", PROMPT_COMMAND="", HISTFILE="/dev/null",
                    HISTSIZE="0", HISTFILESIZE="0")

                def controlling_terminal():
                    os.setsid()
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

                try:
                    self.shell = subprocess.Popen(
                        ['/bin/bash', '--noprofile', '--norc', '--noediting', '-i'],
                        stdin=subprocess.PIPE, stdout=slave, stderr=slave,
                        pass_fds=(control_write, continue_read, slave), bufsize=0,
                        env=shell_environment, preexec_fn=controlling_terminal)
                finally:
                    os.close(slave)
                os.set_blocking(master, False)
                os.write(self.shell.stdin.fileno(), b"set -m\n")
        except BaseException:
            os.close(self.control_read)
            os.close(self.continue_write)
            if self.pty_master is not None:
                os.close(self.pty_master)
            raise
        finally:
            os.close(control_write)
            os.close(continue_read)
        self.control_fd, self.continue_fd = control_write, continue_read
        self.decoder = BashControl()
        self.namespace = os.readlink('/proc/self/ns/pid')
        os.set_blocking(self.control_read, False)

    def emit(self, kind, command=None, **values):
        wire.send(1, dict(kind=kind, command=command, **values))

    def run(self, request):
        command = request.ref.command_id
        self.decoder.expect(command)
        pipes = CommandPipes(request)
        try:
            data = memoryview(pipes.dispatch(self.control_fd, continue_fd=self.continue_fd,
                                            previous_status=self.decoder.last_status,
                                            pty_fd=self.pty_fd))
            while data:
                count = os.write(self.shell.stdin.fileno(), data)
                if count <= 0:
                    raise BrokenPipeError('Bash command input closed')
                data = data[count:]
            if self.mode == "pty":
                return PtyCommandRunner(
                    command, self.shell, self.namespace, self.control_read, self.decoder,
                    self.pty_master, pipes, self.emit, self.continue_write).run()
            return self._drain(command, pipes)
        finally:
            # Success drains before release. Failure exits the relay; the host
            # must confirm outer supervisor cleanup before another generation.
            pipes.close()

    def _observation(self, fact, pipes):
        pipes.observe_open()
        command = fact.command_id
        if fact.kind == 'prepared':
            self.emit('prepared', command, pid=fact.value, namespace=self.namespace)
            permit = wire.receive(0, timeout=5)
            if permit != dict(kind='continue', command=command):
                raise ValueError('Invalid Bash execution permit')
            os.write(self.continue_write, b'1')
        elif fact.kind == 'started':
            self.emit('started', command)
        elif fact.kind in ('finished', 'rejected'):
            pipes.release_writers()

    def _drain(self, command, pipes):
        closed, ended, lost, finished, running = set(), False, False, None, False
        scope_ended, loss_deadline = False, None
        while True:
            if not ended:
                try:
                    raw = os.read(self.control_read, 2 * MAX_CONTROL_BYTES)
                except BlockingIOError:
                    raw = None
                if raw == b'':
                    ended = lost = True
                for fact in self.decoder.feed(raw) if raw else ():
                    self._observation(fact, pipes)
                    running |= fact.kind == 'started'
                    if fact.kind in ('finished', 'rejected'):
                        finished = fact
                # Scope EOF can race a final control write on the other pipe.
                # Re-read control after observing EOF, and exhaust buffered
                # frames, before deciding the required finish was lost.
                if scope_ended and raw is None and finished is None:
                    lost = True
            try:
                scope_ended |= pipes.scope_ended(self.shell.pid)
            except (OSError, ValueError):
                # A missing observation is not a completed command. Still
                # consume available output before the outer owner cleans up.
                lost = True
            if lost and loss_deadline is None:
                loss_deadline = time.monotonic() + 1
                pipes.release_writers()
            # Bash can write output immediately after its started control write.
            # Observe that control fact before forwarding bytes on another pipe.
            if not running and not (lost or finished):
                select.select([self.control_read], [], [], .002)
                continue
            progress = False
            for stream in ('stdout', 'stderr'):
                if stream in closed:
                    continue
                data = pipes.read(stream)
                if data == b'':
                    closed.add(stream)
                elif data and running:
                    progress = True
                    self.emit('output', command, stream=stream,
                              data=base64.b64encode(data).decode('ascii'))
            # A lost boundary cannot wait indefinitely for background writers.
            # Drain immediately available bytes, then let the outer supervisor
            # confirm cleanup. Later bytes remain explicitly incomplete.
            if lost and (not progress or time.monotonic() >= loss_deadline):
                try:
                    self.decoder.eof()
                except ValueError:
                    pass
                # Expected boundary loss uses the same ordered stdout frames as
                # captured output; a traceback on stderr could overtake tails.
                self.emit('failed', command)
                return False
            if len(closed) == 2 and scope_ended and finished:
                if finished.kind == 'rejected':
                    self.emit('failed', command)
                    return False
                self.emit('complete', command, exit_code=finished.value)
                return True
            select.select([self.control_read] if not ended else [], [], [], .002)

    def close(self):
        self.shell.stdin.close()
        os.close(self.control_read)
        os.close(self.continue_write)
        if self.pty_master is not None:
            os.close(self.pty_master)
        self.shell.wait(timeout=1)


def main():
    initial = wire.receive(0, timeout=5)
    fields = ('kind', 'environment', 'mode') if 'mode' in initial else ('kind', 'environment')
    exact_fields(initial, fields)
    if initial['kind'] != 'initialize' or initial.get('mode', 'pipes') not in ('pipes', 'pty'):
        raise ValueError('Invalid relay initialization')
    relay = BashRelay(initial['environment'], initial.get('mode', 'pipes'))
    try:
        relay.emit('ready', pid=relay.shell.pid, namespace=relay.namespace)
        while True:
            message = wire.receive(0)
            exact_fields(message, ('kind', 'request'))
            if message['kind'] != 'execute':
                raise ValueError('Invalid relay operation')
            if not relay.run(CommandRequest.from_record(message['request'])):
                return 1
    except EOFError:
        return 0
    finally:
        relay.close()


if __name__ == '__main__':
    raise SystemExit(main())
