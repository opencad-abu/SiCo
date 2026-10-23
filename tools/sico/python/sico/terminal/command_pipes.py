"""Ephemeral sealed input and per-command kernel descriptors; no process owner."""

import fcntl
import os
import shlex

from ..core.terminal_commands import CommandRequest
from .bash_control import dispatch_pty_script, dispatch_script


class CommandPipes:
    """Close after confirmed output drain or confirmed process-tree cleanup.

    Writable filesystem space is unnecessary: Bash opens read-only sealed command
    input and output pipes via this relay's /proc descriptors. The relay must be
    in the same sandbox/PID namespace as its shell. After uncertain delivery,
    the owner must stop that shell before releasing descriptors: a queued /proc
    path must never resolve to a reused descriptor. This object launches nothing.
    """

    def __init__(self, request):
        if (not isinstance(request, CommandRequest) or request.mode not in ("pipes", "pty")
                or request.shell not in (None, "/bin/bash")):
            raise ValueError("Command descriptors require /bin/bash")
        self._request = request
        self._mode = request.mode
        self._fds = set()
        self._writers_released = False
        self._dispatched = False
        self._readers = {}
        try:
            self._source = self._own(os.memfd_create("sico-command", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING))
            text = request.text if request.text is not None else shlex.join(request.argv)
            data = memoryview(text.encode("utf-8"))
            while data:
                count = os.write(self._source, data)
                if count <= 0:
                    raise OSError("Command input could not be written")
                data = data[count:]
            fcntl.fcntl(self._source, fcntl.F_ADD_SEALS,
                        fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            self._sinks = {}
            if self._mode == "pipes":
                for stream in ("stdout", "stderr"):
                    reader, writer = os.pipe()
                    self._readers[stream] = self._own(reader)
                    self._sinks[stream] = self._own(writer)
                    os.set_blocking(reader, False)
            reader, writer = os.pipe()
            self._scope_reader, self._scope_writer = self._own(reader), self._own(writer)
            os.set_blocking(reader, False)
            if self._mode == "pty":
                reader, writer = os.pipe()
                self._sentinel_reader, self._sentinel_writer = self._own(reader), self._own(writer)
                os.set_blocking(reader, False)
        except BaseException:
            self.close()
            raise

    def _own(self, descriptor):
        self._fds.add(descriptor)
        return descriptor

    def _close_fd(self, descriptor):
        if descriptor in self._fds:
            os.close(descriptor)
            self._fds.remove(descriptor)

    @property
    def sentinel_fd(self):
        if self._mode != "pty":
            raise ValueError("Command descriptors do not have a PTY sentinel")
        return self._sentinel_reader

    def dispatch(self, control_fd, *, continue_fd=None, previous_status=0, pty_fd=None):
        if self._dispatched or self._writers_released or self._source not in self._fds:
            raise ValueError("Command descriptors are closed")
        path = lambda fd: f"/proc/{os.getpid()}/fd/{fd}"
        if self._mode == "pty":
            if type(pty_fd) is not int:
                raise ValueError("PTY commands require a shell PTY descriptor")
            script = dispatch_pty_script(
                self._request.ref.command_id, path(self._source),
                scope_path=path(self._scope_writer),
                sentinel_path=path(self._sentinel_writer), control_fd=control_fd,
                pty_fd=pty_fd, cwd=self._request.cwd, continue_fd=continue_fd,
                previous_status=previous_status)
        else:
            script = dispatch_script(
                self._request.ref.command_id, path(self._source),
                output_path=path(self._sinks["stdout"]),
                error_path=path(self._sinks["stderr"]),
                scope_path=path(self._scope_writer), control_fd=control_fd,
                cwd=self._request.cwd, continue_fd=continue_fd,
                previous_status=previous_status)
        self._dispatched = True
        return script

    def observe_open(self):
        """After a control observation proves Bash opened the outer block."""
        self._close_fd(self._scope_writer)
        if self._mode == "pty":
            self._close_fd(self._sentinel_writer)

    def sentinel_ended(self):
        """PTY-only: all command output writers have released the terminal."""
        if self._mode != "pty":
            raise ValueError("Command descriptors do not have a PTY sentinel")
        descriptor = self._sentinel_reader
        if descriptor not in self._fds:
            raise ValueError("Command PTY sentinel is closed")
        try:
            data = os.read(descriptor, 1)
        except BlockingIOError:
            return False
        if data:
            raise ValueError("Unexpected command PTY sentinel bytes")
        return True

    def scope_ended(self, shell_pid=None):
        """Observe outer-block release, never a result or process retirement.

        Bash can copy saved redirections into a background subshell. EOF alone
        then waits for that descendant. The relay may supply its still-owned,
        unreaped main shell PID to check that shell's descriptor table instead.
        Unstable scans are conservatively still open; no signal is sent here.
        """
        if self._scope_reader not in self._fds:
            raise ValueError("Command scope descriptor is closed")
        try:
            data = os.read(self._scope_reader, 1)
        except BlockingIOError:
            if shell_pid is None or self._scope_writer in self._fds:
                return False
            return self._scope_released_by(shell_pid)
        if data:
            raise ValueError("Unexpected command scope bytes")
        return True

    def _scope_released_by(self, shell_pid):
        if type(shell_pid) is not int or not 1 <= shell_pid <= 2**31 - 1:
            raise ValueError("Invalid owned Bash PID")
        expected = os.readlink(f"/proc/self/fd/{self._scope_reader}")
        root = f"/proc/{shell_pid}/fd"

        def snapshot():
            names = os.listdir(root)
            if len(names) > 4096:
                raise ValueError("Bash descriptor observation exceeds budget")
            return sorted((name, os.readlink(f"{root}/{name}")) for name in names)

        try:
            before = snapshot()
            if any(target == expected for _, target in before):
                return False
            return before == snapshot()
        except (FileNotFoundError, ProcessLookupError):
            # The caller retains ownership. Disappearance is never retirement;
            # control EOF or the existing owner resolves actual process loss.
            return False

    def release_writers(self):
        """After the shell's finish/reject observation, allow eventual pipe EOF."""
        self._writers_released = True
        self.observe_open()
        self._close_fd(self._source)
        for descriptor in self._sinks.values():
            self._close_fd(descriptor)
        if self._mode == "pty":
            self._close_fd(self._sentinel_writer)

    def read(self, stream):
        """None means no bytes yet; b'' is that stream's actual EOF."""
        if stream not in self._readers:
            raise ValueError("Unknown command output stream")
        descriptor = self._readers[stream]
        if descriptor not in self._fds:
            raise ValueError("Command output descriptor is closed")
        try:
            return os.read(descriptor, 65536)
        except BlockingIOError:
            return None

    def close(self):
        for descriptor in tuple(self._fds):
            self._close_fd(descriptor)
