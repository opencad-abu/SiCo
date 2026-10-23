"""Bounded Bash command observations from an independent control pipe."""

from __future__ import annotations

from dataclasses import dataclass
import shlex

from ..core.contracts import identifier
from ..core.terminal_commands import MAX_PATH_BYTES, text_value

MAX_CONTROL_BYTES = MAX_PATH_BYTES + 512


@dataclass(frozen=True)
class BashObservation:
    """Shell-reported PID/directory, not independently verified host identity."""

    kind: str
    command_id: str
    value: int
    cwd: str | None


class BashControl:
    """Decode control bytes only, never stdout or stderr.

    This separates shell observations from command output. It is not a security
    boundary against code deliberately tampering with its own shell integration.
    EOF without a matching finish is observation loss, not inferred success.
    """

    def __init__(self):
        self._buffer = bytearray()
        self._failed = False
        self._active = None
        self._last_status = 0

    @property
    def last_status(self):
        """Last verified sourced-command result, before integration overwrites it."""
        return self._last_status

    def expect(self, command_id):
        identifier(command_id)
        if self._failed or self._active is not None or self._buffer:
            raise ValueError("Bash control is occupied or unavailable")
        self._active = (command_id, "expected")

    def feed(self, data):
        if self._failed or not isinstance(data, bytes) or len(data) > 2 * MAX_CONTROL_BYTES:
            self._failed = True
            raise ValueError("Invalid Bash control input")
        self._buffer.extend(data)
        result = []
        try:
            while self._buffer.count(0) >= 4:
                fields = bytes(self._buffer).split(b"\0", 4)
                size = sum(len(field) + 1 for field in fields[:4])
                if size > MAX_CONTROL_BYTES:
                    raise ValueError("Bash control frame exceeds limit")
                result.append(self._observation(fields[:4]))
                del self._buffer[:size]
            if len(self._buffer) > MAX_CONTROL_BYTES:
                raise ValueError("Bash control frame exceeds limit")
            return result
        except (ValueError, UnicodeError):
            self._failed = True
            raise

    def _observation(self, fields):
        kind, command, number, cwd = [value.decode("utf-8") for value in fields]
        if self._active is None or command != self._active[0]:
            raise ValueError("Bash observation belongs to another command")
        if not number.isascii() or not number.isdigit() or len(number) > 10:
            raise ValueError("Invalid Bash observation values")
        # PWD is user state, not kernel identity. Unset/relative values cannot
        # invalidate a command whose directory is independently host-observed.
        text_value(cwd, limit=MAX_PATH_BYTES, empty=True)
        cwd = cwd if cwd.startswith("/") else None
        value = int(number)
        phase = self._active[1]
        if kind == "prepared" and phase == "expected" and 1 <= value <= 2**31 - 1:
            self._active = (command, "prepared")
        elif kind == "started" and phase in ("expected", "prepared") and 1 <= value <= 2**31 - 1:
            self._active = (command, "started")
        elif kind in ("finished", "rejected") and 0 <= value <= 255:
            if (kind == "finished") != (phase == "started") or kind == "rejected" and (value == 0 or phase != "expected"):
                raise ValueError("Invalid Bash observation order")
            if kind == "finished":
                self._last_status = value
            self._active = None
        else:
            raise ValueError("Invalid Bash observation order")
        return BashObservation(kind, command, value, cwd)

    def eof(self):
        self._failed = True
        if self._buffer or self._active is not None:
            raise ValueError("Bash control ended before command completion")

    def cancelled(self, command_id, status=130):
        """Accept an owner-confirmed interactive cancellation, not a finish frame."""
        identifier(command_id)
        if (self._failed or self._buffer or self._active != (command_id, "started")
                or type(status) is not int or not 0 <= status <= 255):
            raise ValueError("Bash cancellation has no active foreground command")
        self._last_status = status
        self._active = None


def dispatch_script(command_id, source_path, *, output_path, error_path, scope_path, control_fd, cwd=None,
                    continue_fd=None, previous_status=0):
    """Source sealed command bytes at top level in the existing Bash.

    Per-command output descriptors also collect late background output. The
    integration descriptor is closed during user code and its child execs.
    stdin is explicitly /dev/null until interactive input is implemented.
    """
    identifier(command_id)
    if type(previous_status) is not int or not 0 <= previous_status <= 255:
        raise ValueError("Invalid previous Bash status")
    if type(control_fd) is not int or control_fd < 3:
        raise ValueError("Invalid Bash integration descriptor")
    if continue_fd is not None and (type(continue_fd) is not int or continue_fd < 3 or continue_fd == control_fd):
        raise ValueError("Invalid Bash continuation descriptor")
    for path in (source_path, output_path, error_path, scope_path):
        text_value(path, limit=MAX_PATH_BYTES)
        if not path.startswith("/"):
            raise ValueError("Bash integration paths must be absolute")
    if cwd is not None:
        text_value(cwd, limit=MAX_PATH_BYTES)
        if not cwd.startswith("/"):
            raise ValueError("Bash directory must be absolute")

    def report(kind, value):
        # The format is constant. Identity values are arguments, not shell code.
        return ("\\builtin printf '%s\\0%s\\0%s\\0%s\\0' "
                f"{kind} {shlex.quote(command_id)} {value} \"${{PWD-}}\" >&{control_fd}")

    started = report("started", '"$BASHPID"')
    prepared = report("prepared", '"$BASHPID"')
    finished = report("finished", '"$?"')
    rejected = report("rejected", '"$?"')
    closed = f" {continue_fd}<&-" if continue_fd is not None else ""
    scope_fd = max(control_fd, continue_fd or 2) + 1
    source = (f"\\builtin source {shlex.quote(source_path)} </dev/null "
              f"{control_fd}>&- {scope_fd}>&-{closed}")
    # The condition leaves its result in $? at entry to either branch. The user
    # source is a branch body, never a condition: errexit and ERR remain active.
    # No wrapper function, positional arguments or shell variables are inserted.
    restore = f"(\\builtin exit {previous_status})"
    execute = f"{started}; if {restore}; then {source}; else {source}; fi; {finished}"
    if continue_fd is not None:
        # The host observes kernel cwd while Bash is blocked here. A subshell
        # reads the one-byte permit without overwriting the user's REPLY/IFS.
        execute = (f"{prepared}; "
                   f"(\\builtin read -r -n1 -u {continue_fd}) || \\builtin exit 125; {execute}")
    if cwd is not None:
        execute = (f"if \\builtin cd -- {shlex.quote(cwd)}; then {execute}; "
                   f"else {rejected}; fi")
    # Bash's redirection stack holds this writer for the outer block, including
    # while source closes it for user code. EOF observes scope end even when
    # noexec/DEBUG/disabled builtins skip the final report. It proves no result.
    return (f"{{ {execute}; }} >{shlex.quote(output_path)} 2>{shlex.quote(error_path)} "
            f"{scope_fd}>{shlex.quote(scope_path)}\n").encode()


def dispatch_pty_script(command_id, source_path, *, scope_path, sentinel_path, control_fd,
                        pty_fd, cwd=None, continue_fd=None, previous_status=0):
    """Source one command with stdin on the persistent PTY and output inherited.

    The outer block holds a separate sentinel writer that is inherited by every
    command child. Sentinel EOF therefore means all command output writers have
    released the persistent terminal, without closing the shell's PTY.
    """
    identifier(command_id)
    if type(previous_status) is not int or not 0 <= previous_status <= 255:
        raise ValueError("Invalid previous Bash status")
    if type(control_fd) is not int or control_fd < 3:
        raise ValueError("Invalid Bash integration descriptor")
    if type(pty_fd) is not int or pty_fd < 3 or pty_fd == control_fd:
        raise ValueError("Invalid Bash PTY descriptor")
    if continue_fd is not None and (type(continue_fd) is not int or continue_fd < 3
                                    or continue_fd in (control_fd, pty_fd)):
        raise ValueError("Invalid Bash continuation descriptor")
    for path in (source_path, scope_path, sentinel_path):
        text_value(path, limit=MAX_PATH_BYTES)
        if not path.startswith("/"):
            raise ValueError("Bash integration paths must be absolute")
    if cwd is not None:
        text_value(cwd, limit=MAX_PATH_BYTES)
        if not cwd.startswith("/"):
            raise ValueError("Bash directory must be absolute")

    def report(kind, value):
        return ("\\builtin printf '%s\\0%s\\0%s\\0%s\\0' "
                f"{kind} {shlex.quote(command_id)} {value} \"${{PWD-}}\" >&{control_fd}")

    started = report("started", '"$BASHPID"')
    prepared = report("prepared", '"$BASHPID"')
    finished = report("finished", '"$?"')
    rejected = report("rejected", '"$?"')
    closed = f" {continue_fd}<&-" if continue_fd is not None else ""
    scope_fd = max(control_fd, continue_fd or 2, pty_fd) + 1
    sentinel_fd = scope_fd + 1
    source = (f"\\builtin source {shlex.quote(source_path)} <&{pty_fd} "
              f"{control_fd}>&- {scope_fd}>&-{closed}")
    restore = f"(\\builtin exit {previous_status})"
    execute = f"{started}; if {restore}; then {source}; else {source}; fi; {finished}"
    if continue_fd is not None:
        execute = (f"{prepared}; "
                   f"(\\builtin read -r -n1 -u {continue_fd}) || \\builtin exit 125; {execute}")
    if cwd is not None:
        execute = (f"if \\builtin cd -- {shlex.quote(cwd)}; then {execute}; "
                   f"else {rejected}; fi")
    return (f"{{ {execute}; }} {scope_fd}>{shlex.quote(scope_path)} "
            f"{sentinel_fd}>{shlex.quote(sentinel_path)}\n").encode()
