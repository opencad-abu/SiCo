"""Commit terminal facts through the existing session writer before publication."""

from collections import OrderedDict

from ..core.contracts import RunState
from ..core.terminal_commands import CommandRef, CommandRequest
from ..core.terminal_session_events import EVENT_CONTRACT as SESSION_EVENT, event as session_event
from ..core.terminal_session_state import apply_event as apply_terminal
from ..core.terminal_state import apply_event as apply_command, event as command_event
from .terminal_records import JOURNAL_KIND, restore, store

COMMAND_CACHE = 257  # At most the configured FIFO plus its active command.


class TerminalPersistenceError(RuntimeError):
    """Persistence failed; this writer attachment must not dispatch again."""


class TerminalJournal:
    """A bounded derived view; it never creates a writer or a second state file.

    The journal's append lock also serializes competing attachments to the same
    in-process writer. Cold command/request lookup scans committed metadata, so
    retaining all historical commands in memory is unnecessary. A later index
    can accelerate those reads without changing the journal's authority.
    """

    def __init__(self, journal, initial_context, claim):
        if journal.session_id != claim.ref.session_id:
            raise ValueError("Terminal claim belongs to another session")
        self._journal, self._initial = journal, initial_context
        self._terminal, self._after = None, 0
        self._commands = OrderedDict()
        self._failed = False
        with journal._events_lock:
            self._refresh()
            if self._terminal is not None and self._terminal.claim != claim:
                raise ValueError("Session already owns a different terminal claim")
            if self._terminal is None:
                self.observe(session_event("claimed", claim.ref, claim=claim.record()))

    def _remember(self, state):
        ref = state.request.ref
        self._commands[ref] = state
        self._commands.move_to_end(ref)
        while len(self._commands) > COMMAND_CACHE:
            self._commands.popitem(last=False)

    def _refresh(self):
        journal = self._journal
        for position in range(self._after, len(journal.records)):
            row = journal.row(position)
            if row["kind"] == JOURNAL_KIND:
                value = restore(row["payload"], journal)
                if value["contract"] == SESSION_EVENT:
                    self._terminal = apply_terminal(self._terminal, value)
                else:
                    ref = CommandRef.from_record(value["ref"])
                    prior = self._commands.get(ref)
                    if prior is None and value["kind"] != "accepted":
                        prior = self._read_command(ref, position)
                    self._remember(apply_command(prior, value))
            self._after = position + 1

    def _read_command(self, ref, end=None):
        result = None
        journal = self._journal
        for position in range(len(journal.records) if end is None else end):
            row = journal.row(position)
            if row["kind"] == JOURNAL_KIND and row["payload"].get("ref") == ref.record():
                value = restore(row["payload"], journal)
                result = apply_command(result, value)
        return result

    @property
    def terminal(self):
        with self._journal._events_lock:
            self._refresh()
            return self._terminal

    def command(self, ref):
        if ref.session_id != self._journal.session_id:
            raise ValueError("Command belongs to another session")
        with self._journal._events_lock:
            self._refresh()
            state = self._commands.get(ref)
            if state is None:
                state = self._read_command(ref)
            if state is None:
                raise KeyError(ref)
            self._remember(state)
            return state

    def duplicate(self, request):
        """Exact retries resolve to the original reference, including after reopen."""
        with self._journal._events_lock:
            for position in range(len(self._journal.records)):
                row = self._journal.row(position)
                value = row["payload"]
                if row["kind"] != JOURNAL_KIND or value.get("kind") != "accepted":
                    continue
                original = CommandRequest.from_record(restore(value, self._journal)["request"])
                if original.request_id == request.request_id or original.ref == request.ref:
                    if original != request:
                        raise ValueError("Terminal request or command identity conflict")
                    return original.ref
        return None

    def admit(self, request):
        with self._journal._events_lock:
            if self.duplicate(request) is not None:
                return False
            state = self.terminal
            queued = session_event("queued", state.terminal_ref,
                                   generation=state.generation.record(), command=request.ref.record())
            apply_terminal(state, queued)  # Reject queue admission before committing acceptance.
            self.observe(command_event("accepted", request.ref, request=request.record()))
            self.observe(queued)
            return True

    def observe(self, value):
        with self._journal._events_lock:
            if self._failed:
                raise TerminalPersistenceError("Terminal persistence requires reopening")
            self._refresh()
            session = value.get("contract") == SESSION_EVENT
            if session:
                candidate = apply_terminal(self._terminal, value)
            else:
                ref = CommandRef.from_record(value["ref"])
                try:
                    prior = self.command(ref)
                except KeyError:
                    prior = None
                candidate = apply_command(prior, value)
            try:
                payload = store(value, self._journal)
                self._journal.append(JOURNAL_KIND, payload,
                                     self._journal.state or RunState(self._initial))
            except Exception as exc:
                self._failed = True
                raise TerminalPersistenceError("Terminal observation could not be committed") from exc
            # An attachment, candidate state or notification is not a commit.
            self._after = len(self._journal.records)
            if session:
                self._terminal = candidate
            else:
                self._remember(candidate)
            return candidate
