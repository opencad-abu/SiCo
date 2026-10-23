"""Immutable identities for a session's single terminal and shell generations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .contracts import identifier
from .terminal_commands import MAX_PATH_BYTES, exact_fields, text_value
from .terminal_output import position

SESSION_CONTRACT = "sico.terminal.session.v1"
MAX_QUEUE = 256


@dataclass(frozen=True)
class TerminalRef:
    session_id: str
    terminal_id: str

    def __post_init__(self):
        identifier(self.session_id)
        identifier(self.terminal_id)

    def record(self):
        return dict(session_id=self.session_id, terminal_id=self.terminal_id)

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("session_id", "terminal_id"))
        return cls(**value)


@dataclass(frozen=True)
class TerminalClaim:
    """Initial configuration, never a copy of the mutable live shell state."""

    ref: TerminalRef
    shell: str
    cwd: str
    environment_id: str
    mode: str = "pty"
    queue_limit: int = 64

    def __post_init__(self):
        if not isinstance(self.ref, TerminalRef):
            raise ValueError("Terminal claim requires a terminal reference")
        for path in (self.shell, self.cwd):
            text_value(path, limit=MAX_PATH_BYTES)
            if not PurePosixPath(path).is_absolute():
                raise ValueError("Terminal path must be absolute")
        identifier(self.environment_id)
        if self.mode not in ("pipes", "pty"):
            raise ValueError("Invalid terminal mode")
        if type(self.queue_limit) is not int or not 1 <= self.queue_limit <= MAX_QUEUE:
            raise ValueError("Invalid terminal queue limit")

    def record(self):
        return dict(contract=SESSION_CONTRACT, ref=self.ref.record(), shell=self.shell,
                    cwd=self.cwd, environment_id=self.environment_id, mode=self.mode,
                    queue_limit=self.queue_limit)

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("contract", "ref", "shell", "cwd", "environment_id",
                             "mode", "queue_limit"))
        if value["contract"] != SESSION_CONTRACT:
            raise ValueError("Unsupported terminal session contract")
        fields = {key: item for key, item in value.items() if key != "contract"}
        fields["ref"] = TerminalRef.from_record(fields["ref"])
        return cls(**fields)


@dataclass(frozen=True)
class TerminalAttempt:
    """Durable creation intent; epoch increases before any possible spawn."""

    ref: TerminalRef
    epoch: int
    host_id: str
    boot_id: str

    def __post_init__(self):
        if not isinstance(self.ref, TerminalRef):
            raise ValueError("Terminal attempt requires a terminal reference")
        position(self.epoch, minimum=1)
        identifier(self.host_id)
        identifier(self.boot_id)

    def record(self):
        return dict(ref=self.ref.record(), epoch=self.epoch,
                    host_id=self.host_id, boot_id=self.boot_id)

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("ref", "epoch", "host_id", "boot_id"))
        return cls(TerminalRef.from_record(value["ref"]), value["epoch"],
                   value["host_id"], value["boot_id"])


@dataclass(frozen=True)
class TerminalGeneration:
    """Host-visible OS process identity, distinct from a backend's process handle."""

    attempt: TerminalAttempt
    pid: int
    process_start: int

    def __post_init__(self):
        if not isinstance(self.attempt, TerminalAttempt):
            raise ValueError("Terminal process requires a creation attempt")
        if type(self.pid) is not int or not 1 <= self.pid <= 2**31 - 1:
            raise ValueError("Invalid terminal process PID")
        position(self.process_start)

    def record(self):
        return dict(attempt=self.attempt.record(), pid=self.pid, process_start=self.process_start)

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("attempt", "pid", "process_start"))
        return cls(TerminalAttempt.from_record(value["attempt"]),
                   value["pid"], value["process_start"])


@dataclass(frozen=True)
class TerminalRetirement:
    """Structured evidence supplied by the trusted process owner, never UI input.

    The codec validates bindings, not truth. The service must verify receipt_id
    against supervisor/host evidence; absent a start record is not spawn failure.
    An exit receipt covers every child launched under the creation attempt.
    owner_stopped records acknowledged tree cleanup when startup identity was
    never committed; it does not assert that no process was created.
    """

    attempt: TerminalAttempt
    kind: str
    receipt_id: str
    generation: TerminalGeneration | None = None
    new_boot_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.attempt, TerminalAttempt):
            raise ValueError("Terminal retirement requires a creation attempt")
        identifier(self.receipt_id)
        if self.kind == "process_exited":
            if (not isinstance(self.generation, TerminalGeneration)
                    or self.generation.attempt != self.attempt or self.new_boot_id is not None):
                raise ValueError("Exit evidence requires the matching process identity")
        elif self.kind == "host_rebooted":
            identifier(self.new_boot_id)
            if self.new_boot_id == self.attempt.boot_id or self.generation is not None:
                raise ValueError("Reboot evidence requires a different boot identity")
        elif self.kind not in ("spawn_failed", "owner_stopped") or self.generation is not None or self.new_boot_id is not None:
            raise ValueError("Invalid terminal retirement evidence")

    def record(self):
        return dict(attempt=self.attempt.record(), kind=self.kind, receipt_id=self.receipt_id,
                    generation=self.generation.record() if self.generation else None,
                    new_boot_id=self.new_boot_id)

    @classmethod
    def from_record(cls, value):
        exact_fields(value, ("attempt", "kind", "receipt_id", "generation", "new_boot_id"))
        generation = (TerminalGeneration.from_record(value["generation"])
                      if value["generation"] is not None else None)
        return cls(TerminalAttempt.from_record(value["attempt"]), value["kind"],
                   value["receipt_id"], generation, value["new_boot_id"])
