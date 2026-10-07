"""Qt-independent launch, lifecycle, and control protocol helpers."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CONTROL_FD_ENV = "SICO_AI_CONTROL_FD"
MAXIMUM_CONTROL_BYTES = 4096
USAGE = "sico-ai-terminal --working-directory DIR -- PROGRAM [ARG ...]"


@dataclass(frozen=True)
class LaunchOptions:
    workspace: Path
    program: Path
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class ControlResult:
    commands: tuple[str, ...]
    shutdown: bool = False
    oversized: bool = False


def parse_launch_options(
    arguments: Sequence[str], *, cwd: Path | None = None
) -> LaunchOptions:
    """Parse the terminal launcher's ``--working-directory ... --`` contract."""
    values = list(arguments)
    try:
        separator = values.index("--")
    except ValueError as exc:
        raise ValueError(USAGE) from exc
    if separator + 1 >= len(values):
        raise ValueError(USAGE)

    workspace_value: str | None = None
    index = 0
    while index < separator:
        if values[index] != "--working-directory" or index + 1 >= separator:
            raise ValueError(USAGE)
        workspace_value = values[index + 1]
        index += 2

    base = Path.cwd() if cwd is None else cwd
    workspace = Path(workspace_value) if workspace_value else base
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(USAGE)

    program = Path(values[separator + 1]).expanduser()
    if not program.is_absolute():
        if "/" in values[separator + 1]:
            program = (base / program).resolve()
        else:
            resolved = shutil.which(str(program))
            program = Path(resolved) if resolved is not None else program
    else:
        program = program.resolve()
    if not program.is_file() or not os.access(program, os.X_OK):
        raise ValueError(USAGE)

    return LaunchOptions(workspace, program, tuple(values[separator + 2 :]))


def parse_control_fd(
    environment: MutableMapping[str, str] | None = None,
) -> int | None:
    """Securely adopt the readable pipe descriptor supplied by the controller."""
    target = os.environ if environment is None else environment
    from sicoenv import value

    legacy = "CAD_AI_CONTROL_FD"  # Retire after the integrated migration release.
    try:
        raw = value(target, CONTROL_FD_ENV, (legacy,))
    finally:
        target.pop(CONTROL_FD_ENV, None)
        target.pop(legacy, None)
    if raw is None:
        return None
    if not raw or len(raw) > 10 or not raw.isascii() or not raw.isdigit():
        return None
    descriptor = int(raw)
    if descriptor <= 2:
        return None

    try:
        status = os.fstat(descriptor)
        flags = os.O_NONBLOCK
        if not stat.S_ISFIFO(status.st_mode):
            os.close(descriptor)
            return None
        try:
            import fcntl

            descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
            status_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            if status_flags & os.O_ACCMODE == os.O_WRONLY:
                os.close(descriptor)
                return None
            fcntl.fcntl(descriptor, fcntl.F_SETFD, descriptor_flags | fcntl.FD_CLOEXEC)
            fcntl.fcntl(descriptor, fcntl.F_SETFL, status_flags | flags)
        except (ImportError, AttributeError):
            os.set_blocking(descriptor, False)
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None
    return descriptor


class ControlDecoder:
    """Incrementally decode the line-oriented ``control.v2`` channel."""

    def __init__(self, maximum_bytes: int = MAXIMUM_CONTROL_BYTES) -> None:
        if maximum_bytes <= 0:
            raise ValueError("maximum control command size must be positive")
        self.maximum_bytes = maximum_bytes
        self._pending = bytearray()
        self._closed = False

    def feed(self, data: bytes) -> ControlResult:
        if self._closed:
            return ControlResult((), shutdown=True)
        if not isinstance(data, bytes):
            raise TypeError("control data must be bytes")
        self._pending.extend(data)
        commands: list[str] = []
        while True:
            newline = self._pending.find(b"\n")
            if newline < 0:
                break
            if newline > self.maximum_bytes:
                self._closed = True
                return ControlResult(tuple(commands), shutdown=True, oversized=True)
            raw = bytes(self._pending[:newline])
            del self._pending[: newline + 1]
            if raw == b"show":
                commands.append("show")
            elif raw == b"shutdown":
                commands.append("shutdown")
                self._closed = True
                return ControlResult(tuple(commands), shutdown=True)
        if len(self._pending) > self.maximum_bytes:
            self._closed = True
            return ControlResult(tuple(commands), shutdown=True, oversized=True)
        return ControlResult(tuple(commands))

    def eof(self) -> ControlResult:
        self._closed = True
        self._pending.clear()
        return ControlResult((), shutdown=True)


def close_decision(
    choice: str,
    *,
    session_running: bool,
    bypass_confirmation: bool = False,
) -> tuple[bool, bool]:
    """Return ``(accept, minimize)`` for a close request."""
    if not session_running or bypass_confirmation:
        return True, False
    if choice == "exit":
        return True, False
    if choice == "minimize":
        return False, True
    return False, False
