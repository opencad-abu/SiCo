"""Drain and control one command on Bash's persistent terminal PTY.

This is a command runner inside the existing Bash relay, not another process
owner or session supervisor. The relay still owns the one Bash and delegates
outer-process cleanup to the existing managed supervisor.
"""

import base64
import os
import select
import signal
import time

from ..core.terminal_input import TerminalInput
from . import relay_wire as wire


class PtyCommandRunner:
    """Observe one PTY command boundary, attributed bytes, input and cancellation."""

    def __init__(self, command, shell, namespace, control_read, decoder, master, pipes,
                 emit, continue_write):
        self._command = command
        self._shell = shell
        self._namespace = namespace
        self._control_read = control_read
        self._decoder = decoder
        self._master = master
        self._pipes = pipes
        self._emit = emit
        self._continue_write = continue_write
        self._incoming = wire.decoder()
        self._input_sequence = 0
        self._running = False
        self._finished = None
        self._scope_ended = False
        self._sentinel_eof = False
        self._pending_output = bytearray()
        self._cancel_requested = False
        self._cancel_since = None
        self._cancel_signal_sent = False
        self._cancel_deadline = None
        self._lost = False
        self._loss_deadline = None
        self._control_ended = False
        self._control_quiet = False
        self._scope_quiet_since = None

    def run(self):
        try:
            try:
                while True:
                    ready = set(select.select(
                        [self._control_read, self._master, self._pipes.sentinel_fd, 0],
                        [], [], .002)[0])
                    if self._control_read in ready:
                        self._read_control()
                    if self._master in ready:
                        self._read_output()
                    if self._pipes.sentinel_fd in ready:
                        self._read_sentinel()
                    if 0 in ready:
                        self._read_messages()
                    self._flush_pending()
                    self._observe_scope()
                    if (self._scope_ended and self._control_quiet
                            and self._finished is None and not self._lost):
                        if self._scope_quiet_since is None:
                            self._scope_quiet_since = time.monotonic()
                        elif time.monotonic() - self._scope_quiet_since >= .05:
                            self._lost = True
                    else:
                        self._scope_quiet_since = None
                    if self._finished is not None:
                        self._pipes.release_writers()
                    if self._cancel_requested:
                        self._advance_cancel()
                    if self._normal_complete():
                        return True
                    if self._lost:
                        self._advance_loss()
                        if (self._loss_deadline is not None
                                and time.monotonic() >= self._loss_deadline):
                            self._fail()
                            return False
                    elif (self._scope_ended and self._control_ended
                          and self._finished is None):
                        self._lost = True
                    time.sleep(.001)
            except _Complete:
                return True
        finally:
            self._pipes.close()

    def _read_control(self):
        try:
            raw = os.read(self._control_read, 65536)
        except BlockingIOError:
            self._control_quiet = True
            return
        self._control_quiet = False
        self._scope_quiet_since = None
        if raw == b"":
            self._control_ended = self._lost = True
            return
        for fact in self._decoder.feed(raw):
            self._pipes.observe_open()
            if fact.kind == "prepared":
                self._emit("prepared", self._command, pid=fact.value,
                           namespace=self._namespace)
                permit = wire.receive(0, timeout=5)
                if permit != dict(kind="continue", command=self._command):
                    raise ValueError("Invalid Bash execution permit")
                os.write(self._continue_write, b"1")
            elif fact.kind == "started":
                self._running = True
                self._emit("started", self._command)
            elif fact.kind in ("finished", "rejected"):
                self._finished = fact
            else:
                raise ValueError("Invalid Bash PTY control observation")

    def _read_output(self):
        try:
            data = os.read(self._master, 65536)
        except BlockingIOError:
            return
        except OSError:
            self._lost = True
            return
        if data == b"":
            self._lost = True
            return
        if not self._running:
            self._pending_output.extend(data)
            if len(self._pending_output) > 65536:
                raise ValueError("PTY output arrived before command start")
            return
        self._emit_output(data)

    def _read_sentinel(self):
        try:
            data = os.read(self._pipes.sentinel_fd, 1)
        except BlockingIOError:
            return
        if data:
            raise ValueError("Unexpected command PTY sentinel bytes")
        self._sentinel_eof = True

    def _read_messages(self):
        try:
            raw = os.read(0, 65536)
        except BlockingIOError:
            return
        if raw == b"":
            raise EOFError("Relay input closed during a PTY command")
        for message in self._incoming.feed(raw):
            self._message(message)

    def _message(self, message):
        kind = message.get("kind")
        if kind == "input":
            if set(message) != {"kind", "input"}:
                raise ValueError("Invalid relay input frame")
            value = TerminalInput.from_record(message["input"])
            if (value.ref.command_id != self._command or not self._running
                    or self._finished is not None or self._cancel_signal_sent
                    or value.sequence != self._input_sequence + 1):
                raise ValueError("Relay input does not belong to the active command")
            self._write_input(value.data)
            self._input_sequence = value.sequence
        elif kind == "cancel":
            if set(message) != {"kind", "command"} or message["command"] != self._command:
                raise ValueError("Relay cancellation belongs to another command")
            if not self._cancel_requested:
                self._cancel_requested = True
                self._cancel_since = time.monotonic()
        else:
            raise ValueError("Invalid relay operation during a PTY command")

    def _write_input(self, data):
        view = memoryview(data)
        deadline = time.monotonic() + 2
        while view:
            if not select.select([], [self._master], [], max(0, deadline - time.monotonic()))[1]:
                raise TimeoutError("PTY input could not be delivered")
            try:
                count = os.write(self._master, view)
            except BlockingIOError:
                continue
            if count <= 0:
                raise BrokenPipeError("PTY input closed")
            view = view[count:]

    def _advance_cancel(self):
        if self._finished is not None:
            return
        if self._lost:
            return
        now = time.monotonic()
        if not self._running:
            if now - self._cancel_since >= 5:
                raise TimeoutError("Foreground command never started for cancellation")
            return
        if not self._cancel_signal_sent:
            try:
                foreground = os.tcgetpgrp(self._master)
                shell_group = os.getpgid(self._shell.pid)
            except (OSError, ProcessLookupError):
                self._lost = True
                return
            if foreground != shell_group:
                try:
                    os.killpg(foreground, signal.SIGINT)
                except ProcessLookupError:
                    # The foreground group may have exited between observation
                    # and signal. The control/sentinel boundary still decides.
                    pass
                self._cancel_signal_sent = True
                self._cancel_deadline = now + 5
            elif now - self._cancel_since >= .5:
                raise ValueError("Foreground builtin cannot be cancelled without stopping the shell")
            return
        try:
            idle = os.tcgetpgrp(self._master) == os.getpgid(self._shell.pid)
        except (OSError, ProcessLookupError):
            self._lost = True
            return
        if self._sentinel_eof and self._scope_ended and idle and self._shell.poll() is None:
            self._decoder.cancelled(self._command, 130)
            self._drain_output()
            self._emit("complete", self._command, exit_code=130, cancelled=True)
            raise _Complete
        if time.monotonic() >= self._cancel_deadline:
            raise TimeoutError("Foreground cancellation did not reach a usable shell")

    def _observe_scope(self):
        try:
            self._scope_ended |= self._pipes.scope_ended(self._shell.pid)
        except (OSError, ValueError):
            self._lost = True

    def _normal_complete(self):
        if (self._lost or self._finished is None or not self._scope_ended
                or not self._sentinel_eof):
            return False
        if self._finished.kind == "rejected":
            self._emit("failed", self._command)
            return True
        self._drain_output()
        self._emit("complete", self._command, exit_code=self._finished.value,
                   cancelled=self._cancel_signal_sent)
        return True

    def _drain_output(self):
        while True:
            try:
                data = os.read(self._master, 65536)
            except BlockingIOError:
                return
            except OSError:
                return
            if not data:
                return
            self._emit_output(data)

    def _flush_pending(self):
        if self._running and self._pending_output:
            self._emit_output(bytes(self._pending_output))
            self._pending_output.clear()

    def _emit_output(self, data):
        self._emit("output", self._command, stream="pty",
                   data=base64.b64encode(data).decode("ascii"))

    def _advance_loss(self):
        if self._loss_deadline is None:
            self._loss_deadline = time.monotonic() + 1
            self._pipes.release_writers()
        self._flush_pending()
        self._drain_output()

    def _fail(self):
        try:
            self._decoder.eof()
        except ValueError:
            pass
        self._emit("failed", self._command)


class _Complete(Exception):
    """Internal control flow after a synthesized PTY cancellation completion."""
