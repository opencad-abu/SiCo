"""Required execution capabilities for a qualified session-terminal backend.

There is deliberately no default process launcher. Composition must qualify
the backend's permission enforcement, command boundaries and output ownership
before exposing it to a model or a terminal window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..core.terminal_commands import CommandRequest
from ..core.terminal_input import TerminalInput
from ..core.terminal_session import TerminalAttempt, TerminalClaim, TerminalGeneration, TerminalRetirement


@dataclass(frozen=True)
class TerminalCompletion:
    exit_code: int | None
    signal: int | None = None
    output_complete: bool = False
    shell_ready: bool = False
    cancelled: bool = False

    def __post_init__(self):
        code, signal = self.exit_code, self.signal
        if ((code is None) == (signal is None)
                or code is not None and (type(code) is not int or not 0 <= code <= 255)
                or signal is not None and (type(signal) is not int or not 1 <= signal <= 127)
                or type(self.output_complete) is not bool or type(self.shell_ready) is not bool
                or type(self.cancelled) is not bool):
            raise ValueError("Invalid terminal completion")


class TerminalBackend(Protocol):
    """A trusted owner, never constructed from frontend-supplied handles.

    start uses the committed claim and returns only after verifying OS identity
    and a usable control channel. Composition resolves its environment_id within
    the session permission policy, never from frontend-supplied process handles.
    execute calls started only on actual execution, then emits attributed bytes;
    started(execution_id) remains supported. A backend with independently verified
    context uses started(execution_id, dict(cwd=..., generation=...record())); the
    dispatcher commits that context before started, matching its active generation.
    it drains that command's output before returning. Unattributable asynchronous
    output cannot be assigned to the next command. Cancellation is a request; a
    backend may stop the entire generation and must declare that behavior rather
    than claim front-job cancellation or readiness of the old shell. write_input
    is called only after the dispatcher committed the active owner; it must reject
    a different command, a closed foreground, or a backend without PTY stdin.
    start and close must have finite backend deadlines. close must be idempotent,
    fence future start calls for the attempt, and interrupt concurrent operations;
    its evidence covers the main shell and every managed descendant. Failure to
    establish cleanup must raise, leaving the creation reservation occupied.
    """

    def start(self, attempt: TerminalAttempt, claim: TerminalClaim) -> TerminalGeneration: ...

    def execute(self, request: CommandRequest, *, started: Callable,
                output: Callable, cancelled: Callable) -> TerminalCompletion: ...

    def write_input(self, value: TerminalInput) -> None: ...

    def close(self, attempt: TerminalAttempt) -> TerminalRetirement: ...
