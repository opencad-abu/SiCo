"""Admit terminal stdin ownership and bounded delivery for one active command."""

from ..core.terminal_input import TerminalInput
from ..storage.terminal_journal import TerminalPersistenceError


class TerminalInputChannel:
    """A narrow input coordinator sharing the dispatcher's lock and journal."""

    def __init__(self, *, condition, records, backend, input_lock, available,
                 session, observe, faulted, cleanup):
        self._condition = condition
        self._records = records
        self._backend = backend
        self._input_lock = input_lock
        self._available = available
        self._session = session
        self._observe = observe
        self._faulted = faulted
        self._cleanup = cleanup

    def take(self, ref, owner):
        """Commit an explicit model/user takeover for the active command's stdin."""
        with self._input_lock:
            with self._condition:
                self._available()
                command = self._records.command(ref)
                if owner not in ("model", "user") or command.phase != "running":
                    raise ValueError("Terminal input requires a running command")
                state = self._records.terminal
                if state.input_owner == owner and state.input_command == ref:
                    return
                try:
                    self._session("input_owner", generation=state.generation.record(),
                                  command=ref.record(), owner=owner)
                except TerminalPersistenceError:
                    self._faulted("Terminal input ownership could not be committed")
                    self._cleanup()
                    raise
                self._condition.notify_all()

    def write(self, ref, owner, data):
        """Deliver one bounded stdin chunk after the owner committed a takeover."""
        if type(data) is not bytes:
            raise ValueError("Terminal input must be bytes")
        with self._input_lock:
            with self._condition:
                self._available()
                state = self._records.terminal
                command = self._records.command(ref)
                if (state.input_owner != owner or state.input_command != ref
                        or command.phase != "running" or command.cancel_requested):
                    raise ValueError("Terminal input is not owned by this command")
                value = TerminalInput(ref, owner, command.input_chunks + 1, data)
            try:
                self._backend.write_input(value)
            except ValueError:
                raise
            except Exception:
                self._faulted("Terminal input delivery requires reconciliation")
                self._cleanup()
                raise
            with self._condition:
                self._observe(ref, "input", owner=owner, size=len(data))
