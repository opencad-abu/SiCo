"""Versioned terminal command admission values, without execution or storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .contracts import identifier

REQUEST_CONTRACT = "sico.terminal.request.v1"
MAX_COMMAND_BYTES = 65536
MAX_PATH_BYTES = 4096
MAX_ARGUMENTS = 1024


def text_value(value, *, limit, empty=False):
    """Validate bounded UTF-8 text; execution arguments cannot contain NUL."""
    if not isinstance(value, str) or "\0" in value or (not empty and not value):
        raise ValueError("Invalid terminal text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("Invalid terminal UTF-8 text") from exc
    if size > limit:
        raise ValueError("Terminal text exceeds byte limit")
    return value


def exact_fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Invalid terminal record fields")


def _absolute(value):
    text_value(value, limit=MAX_PATH_BYTES)
    if not PurePosixPath(value).is_absolute():
        raise ValueError("Terminal path must be absolute")


@dataclass(frozen=True)
class CommandRef:
    session_id: str
    terminal_id: str
    command_id: str

    def __post_init__(self):
        for value in (self.session_id, self.terminal_id, self.command_id):
            identifier(value)

    def record(self):
        return dict(session_id=self.session_id, terminal_id=self.terminal_id,
                    command_id=self.command_id)

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("session_id", "terminal_id", "command_id"))
        return cls(**value)


@dataclass(frozen=True)
class CommandRequest:
    """Admission intent: cwd=None preserves the persistent shell's directory.

    A non-null cwd requests an explicit directory change in the input sequence.
    The owner records resolved execution context separately at dispatch; this
    request cannot predict directory changes from earlier queued commands.
    """

    ref: CommandRef
    request_id: str
    cwd: str | None
    environment_id: str
    actor: str = "model"
    mode: str = "pipes"
    shell: str | None = None
    text: str | None = None
    argv: tuple[str, ...] = ()
    task_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.ref, CommandRef):
            raise ValueError("Command requires a terminal reference")
        identifier(self.request_id)
        identifier(self.environment_id)
        if self.task_id is not None:
            identifier(self.task_id)
        if self.cwd is not None:
            _absolute(self.cwd)
        if self.actor not in ("model", "user") or self.mode not in ("pipes", "pty"):
            raise ValueError("Invalid terminal actor or mode")
        if not isinstance(self.argv, (list, tuple)) or len(self.argv) > MAX_ARGUMENTS:
            raise ValueError("Invalid terminal argv")
        object.__setattr__(self, "argv", tuple(self.argv))
        if self.text is not None:
            if self.argv:
                raise ValueError("Choose shell text or argv")
            text_value(self.text, limit=MAX_COMMAND_BYTES)
            _absolute(self.shell)
        else:
            if self.shell is not None or not self.argv:
                raise ValueError("Argv command requires arguments and no shell")
            size = 0
            for index, arg in enumerate(self.argv):
                text_value(arg, limit=MAX_COMMAND_BYTES, empty=index > 0)
                size += len(arg.encode("utf-8")) + 1
            if size > MAX_COMMAND_BYTES:
                raise ValueError("Terminal argv exceeds byte limit")

    def record(self):
        return dict(contract=REQUEST_CONTRACT, ref=self.ref.record(),
                    request_id=self.request_id, cwd=self.cwd,
                    environment_id=self.environment_id, actor=self.actor,
                    mode=self.mode, shell=self.shell, text=self.text,
                    argv=list(self.argv), task_id=self.task_id)

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("contract", "ref", "request_id", "cwd", "environment_id",
                             "actor", "mode", "shell", "text", "argv", "task_id"))
        if value["contract"] != REQUEST_CONTRACT:
            raise ValueError("Unsupported terminal request contract")
        arguments = {key: item for key, item in value.items() if key != "contract"}
        arguments["ref"] = CommandRef.from_record(arguments["ref"])
        return cls(**arguments)
