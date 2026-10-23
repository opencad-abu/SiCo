"""Product-session entry point for one command-scoped terminal and its input owner."""

from __future__ import annotations

import threading

from ..core.terminal_commands import CommandRef, CommandRequest
from ..core.terminal_output import OutputChunk
from ..storage.terminal_records import JOURNAL_KIND, restore
from .terminal_command_profile import command_claim
from .terminal_dispatcher import TerminalSessionDispatcher, TerminalUnavailable


class SessionTerminal:
    """Lazily attach a qualified backend to a real session journal.

    The facade owns no process by itself. A backend factory is injected by
    composition only after the backend, permissions and route are qualified.
    Until a command is submitted, the session has no terminal process.
    """

    def __init__(self, journal, context, *, cwd, environment_id, backend_factory,
                 host_id, boot_id, terminal_id="terminal", queue_limit=64):
        self._journal, self._context = journal, context
        self._backend_factory = backend_factory
        self._host_id, self._boot_id = host_id, boot_id
        self._claim = command_claim(
            journal.session_id, cwd, environment_id, terminal_id=terminal_id,
            queue_limit=queue_limit)
        self._lock = threading.RLock()
        self._dispatcher = None
        self._closed = False

    @property
    def claim(self):
        return self._claim

    @property
    def dispatcher(self):
        return self._dispatcher

    def request(self, command_id, request_id, *, text=None, argv=(), actor="model",
                cwd=None, task_id=None):
        """Build a request bound to this session's one command terminal."""
        return CommandRequest(
            CommandRef(self._claim.ref.session_id, self._claim.ref.terminal_id, command_id),
            request_id, cwd, self._claim.environment_id, actor=actor, mode=self._claim.mode,
            shell=self._claim.shell if text is not None else None, text=text,
            argv=argv, task_id=task_id,
        )

    def _owner(self):
        with self._lock:
            if self._closed:
                raise TerminalUnavailable("Terminal session is closed")
            if self._dispatcher is None:
                backend = self._backend_factory()
                self._dispatcher = TerminalSessionDispatcher(
                    self._journal, self._context, self._claim, backend=backend,
                    host_id=self._host_id, boot_id=self._boot_id)
            return self._dispatcher

    def submit(self, request):
        return self._owner().submit(request)

    def take_input(self, request, owner):
        self._owner().take_input(request, owner)

    def write_input(self, request, owner, data):
        self._owner().write_input(request, owner, data)

    def cancel(self, request):
        self._owner().cancel(request)

    def command(self, request):
        return self._owner().command(request)

    def wait(self, request, timeout=None):
        return self._owner().wait(request, timeout)

    def snapshot(self):
        with self._lock:
            dispatcher = self._dispatcher
            closed = self._closed
        if dispatcher is None:
            return dict(configured=True, opened=False, closed=closed, terminal=None)
        result = dispatcher.snapshot()
        result["closed"] = closed
        return result

    def pending(self):
        """Bounded active/queued count for service idle decisions."""
        with self._lock:
            dispatcher = self._dispatcher
        if dispatcher is None:
            return 0
        try:
            state = dispatcher.snapshot()["terminal"]
        except Exception:
            return 1
        return int(bool(state.get("active_command"))) + len(state.get("queued_commands") or ())

    def output_preview(self, request, limit=8192):
        """Read a bounded textual preview from committed command output facts."""
        if not isinstance(request, CommandRef) or type(limit) is not int or not 1 <= limit <= 65536:
            raise ValueError("Invalid terminal output preview")
        data = bytearray()
        truncated = False
        for row in self._journal.records:
            if row["kind"] != JOURNAL_KIND:
                continue
            fact = restore(row["payload"], self._journal)
            if fact["kind"] != "output":
                continue
            chunk = OutputChunk.from_record(fact["chunk"])
            if chunk.ref != request:
                continue
            remaining = limit - len(data)
            if remaining <= 0:
                truncated = True
                break
            data.extend(chunk.data[:remaining])
            truncated |= len(chunk.data) > remaining
            if truncated:
                break
        text = data.decode("utf-8", errors="replace")
        return dict(text=text, bytes=len(data), truncated=truncated,
                    lossy=text.encode("utf-8") != bytes(data))

    def close(self, timeout=5):
        with self._lock:
            if self._closed:
                return
            dispatcher = self._dispatcher
        if dispatcher is not None:
            dispatcher.close(timeout)
        with self._lock:
            self._closed = True
