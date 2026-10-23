"""Serialize one terminal's commands, committing facts before external effects."""

import threading

from ..core.terminal_commands import CommandRequest
from ..core.terminal_output import OutputChunk
from ..core.terminal_session import TerminalAttempt, TerminalGeneration, TerminalRetirement
from ..core.terminal_session_events import event as session_event
from ..core.terminal_session_state import recovered as recovered_terminal
from ..core.terminal_state import event as command_event, recovered as recovered_command
from .terminal_backend import TerminalCompletion
from .terminal_input import TerminalInputChannel
from ..storage.terminal_journal import TerminalJournal, TerminalPersistenceError


class TerminalUnavailable(RuntimeError):
    """Dispatch is suspended; a replacement shell must not be started."""


class TerminalSessionDispatcher:
    """Application composition must supply a qualified backend and existing writer.

    Reading or reopening this owner never starts a process or resumes a queue.
    Only a fresh claim may be lazily started by submit. A recovered creation
    remains occupied until a future explicit reconciliation operation proves
    its process and control-channel identity. No production backend is selected
    by this component.
    """

    def __init__(self, journal, initial_context, claim, *, backend, host_id, boot_id):
        TerminalAttempt(claim.ref, 1, host_id, boot_id)  # Validate before any external effect.
        self._records = TerminalJournal(journal, initial_context, claim)
        self._backend = backend
        self._host_id, self._boot_id = host_id, boot_id
        self._condition = threading.Condition(threading.RLock())
        self._cleanup_lock = threading.Lock()
        self._input_lock = threading.Lock()
        self._attempt = None  # This attachment's control authority, not a second lifecycle state.
        self._cancel = threading.Event()
        self._worker = None
        self._cleanup_worker = None
        self._closing = False
        self._fault = ""
        self._input_channel = TerminalInputChannel(
            condition=self._condition, records=self._records, backend=self._backend,
            input_lock=self._input_lock, available=self._check_available,
            session=lambda kind, **fields: self._session(kind, **fields),
            observe=lambda ref, kind, **fields: self._observe_command(ref, kind, **fields),
            faulted=self._fail, cleanup=self._cleanup_async)

    def snapshot(self):
        with self._condition:
            state = self._records.terminal
            if self._attempt is None or self._fault:
                state = recovered_terminal(state)
            return dict(terminal=state.record(), fault=self._fault, closing=self._closing)

    def command(self, ref):
        with self._condition:
            state = self._records.command(ref)
            return recovered_command(state) if self._attempt is None or self._fault else state

    def submit(self, request):
        with self._condition:
            claim = self._records.terminal.claim
            if (not isinstance(request, CommandRequest)
                    or request.ref.session_id != claim.ref.session_id
                    or request.ref.terminal_id != claim.ref.terminal_id
                    or request.environment_id != claim.environment_id or request.mode != claim.mode
                    or request.shell is not None and request.shell != claim.shell):
                raise ValueError("Command does not match its terminal claim")
            previous = self._records.duplicate(request)
            if previous is not None:
                return previous
        try:
            self._ensure_started()
            with self._condition:
                self._check_available()
                self._records.admit(request)
                if self._worker is None:
                    self._worker = threading.Thread(target=self._run, name="sico-terminal", daemon=True)
                    self._worker.start()
        except TerminalPersistenceError:
            with self._condition:
                self._fail("Terminal persistence failed; dispatch stopped")
                self._cleanup_async()
            raise
        return request.ref

    def _check_available(self):
        state = self._records.terminal
        if (self._closing or self._fault or state.phase not in ("claimed", "ready", "busy")
                or state.phase != "claimed" and state.attempt != self._attempt):
            raise TerminalUnavailable("Terminal process ownership requires reconciliation")

    def _session(self, kind, **fields):
        return self._records.observe(session_event(kind, self._records.terminal.terminal_ref, **fields))

    def _ensure_started(self):
        with self._condition:
            self._condition.wait_for(lambda: self._closing or self._fault
                or self._records.terminal.phase != "creating"
                or self._records.terminal.attempt != self._attempt)
            self._check_available()
            state = self._records.terminal
            if state.phase != "claimed":
                return
            attempt = TerminalAttempt(state.terminal_ref, 1, self._host_id, self._boot_id)
            self._session("creation_requested", attempt=attempt.record())
            self._attempt = attempt
        try:
            generation = self._backend.start(attempt, state.claim)
            if not isinstance(generation, TerminalGeneration) or generation.attempt != attempt:
                raise ValueError("Backend returned a different terminal generation")
            with self._condition:
                if self._closing or self._fault:
                    raise TerminalUnavailable("Terminal closed during startup")
                self._session("started", generation=generation.record())
                self._session("ready", generation=generation.record())
                self._session("resumed", generation=generation.record())
                self._condition.notify_all()
        except Exception:
            with self._condition:
                if not self._closing:
                    self._fail("Terminal startup could not be verified")
            self._cleanup_async()
            raise TerminalUnavailable("Terminal startup requires reconciliation") from None

    def _run(self):
        try:
            while True:
                with self._condition:
                    state = self._records.terminal
                    if self._closing or self._fault or not state.queued_commands:
                        self._worker = None
                        self._condition.notify_all()
                        return
                    self._check_available()
                    ref = state.queued_commands[0]
                    request = self._records.command(ref).request
                    self._cancel.clear()
                    self._session("dispatched", generation=state.generation.record(), command=ref.record())
                result = self._backend.execute(
                    request, started=lambda execution_id, context=None, ref=ref: self._started(ref, execution_id, context),
                    output=lambda stream, data, ref=ref: self._output(ref, stream, data),
                    cancelled=self._cancel.is_set)
                with self._input_lock:
                    with self._condition:
                        if self._fault or self._closing:
                            return
                        if not isinstance(result, TerminalCompletion):
                            raise ValueError("Backend did not return a terminal completion")
                        state = self._records.command(ref)
                        if result.cancelled and not state.cancel_requested:
                            raise ValueError("Backend reported an unrequested cancellation")
                        self._records.observe(command_event("exited", ref, exit_code=result.exit_code,
                            signal=result.signal, output_complete=result.output_complete,
                            output_sequence=state.output_sequence, output_bytes=state.output_bytes))
                        if not result.shell_ready:
                            raise TerminalUnavailable("Command ended without a usable shell boundary")
                        self._session("finished", generation=self._records.terminal.generation.record(),
                                      command=ref.record())
                        self._condition.notify_all()
        except Exception:
            with self._condition:
                if self._closing:
                    return  # close owns cleanup and records the unresolved command.
                self._fail("Terminal execution or persistence requires reconciliation")
            self._cleanup_async()
        finally:
            with self._condition:
                if self._worker is threading.current_thread():
                    self._worker = None
                self._condition.notify_all()

    def _active(self, ref):
        try:
            state = self._records.terminal
        except Exception:
            self._fail("Terminal callback could not read committed state")
            self._cleanup_async()
            raise
        if (self._fault or self._closing or state.attempt != self._attempt or state.active_command != ref
                or state.phase != "busy"):
            raise TerminalUnavailable("Terminal callback no longer owns the active command")

    def _started(self, ref, execution_id, context=None):
        with self._condition:
            if context is not None:
                if context.get("generation") != self._records.terminal.generation.record():
                    raise ValueError("Execution context belongs to another generation")
                self._observe_command(ref, "execution_context", **context)
            self._observe_command(ref, "started", execution_id=execution_id)

    def _output(self, ref, stream, data):
        with self._condition:
            self._observe_command(ref, "output", stream=stream, data=data)

    def _observe_command(self, ref, kind, **fields):
        self._active(ref)
        try:
            if kind == "output":
                state = self._records.command(ref)
                chunk = OutputChunk(ref, state.output_sequence + 1, state.output_bytes, **fields)
                fields = dict(chunk=chunk.record())
            self._records.observe(command_event(kind, ref, **fields))
        except Exception:
            self._fail("Terminal callback could not be committed")
            self._cleanup_async()
            raise

    def _fail(self, reason):
        self._fault = reason
        self._cancel.set()
        try:
            state = self._records.terminal
            if state.active_command is not None:
                command = self._records.command(state.active_command)
                if command.phase in ("accepted", "running", "unknown"):
                    self._records.observe(command_event("observation_lost", command.request.ref,
                                                        reason=reason))
            if state.phase not in ("claimed", "retired", "unknown"):
                self._session("observation_lost", attempt=state.attempt.record(), reason=reason)
        except Exception:
            self._fault = "Terminal persistence failed; process ownership requires reconciliation"
        self._condition.notify_all()

    def cancel(self, ref):
        with self._condition:
            self._check_available()
            command = self._records.command(ref)
            if command.phase in ("exited", "not_started") or command.cancel_requested:
                return
            try:
                self._records.observe(command_event("cancel_requested", ref))
                state = self._records.terminal
                if ref in state.queued_commands:
                    self._records.observe(command_event("not_started", ref, reason="Cancelled before dispatch"))
                    self._session("withdrawn", attempt=state.attempt.record(), command=ref.record(),
                                  reason="Cancelled before dispatch")
                elif state.active_command == ref:
                    self._cancel.set()
            except TerminalPersistenceError:
                self._fail("Terminal cancellation could not be committed")
                self._cleanup_async()
                raise
            self._condition.notify_all()

    def take_input(self, ref, owner):
        """Commit an explicit model/user takeover for the active command's stdin."""
        self._input_channel.take(ref, owner)

    def write_input(self, ref, owner, data):
        """Deliver one bounded stdin chunk after the owner committed a takeover."""
        self._input_channel.write(ref, owner, data)

    def wait(self, ref, timeout=None):
        with self._condition:
            self._records.command(ref)
            def ended():
                return (self._fault or self._closing or self._attempt is None
                        or self._records.command(ref).phase in ("exited", "unknown", "not_started"))
            if not self._condition.wait_for(ended, timeout):
                raise TimeoutError("Terminal command did not finish")
            state = self._records.command(ref)
            return recovered_command(state) if self._fault or self._attempt is None or self._closing else state

    def _cleanup_async(self):
        # The callback may hold the dispatch lock. Cleanup must run outside it
        # so the backend can finish output/control callbacks while being stopped.
        with self._condition:
            if self._cleanup_worker is None or not self._cleanup_worker.is_alive():
                self._cleanup_worker = threading.Thread(target=self._cleanup_background,
                                                       name="sico-terminal-close", daemon=True)
                self._cleanup_worker.start()

    def _cleanup_background(self):
        try:
            self._cleanup()
        except Exception:
            with self._condition:
                self._fault = "Terminal descendant cleanup is unconfirmed"
                self._condition.notify_all()

    def _cleanup(self):
        with self._cleanup_lock:
            if self._attempt is None:
                return
            # Even an unreadable writer must not prevent process cleanup.
            evidence = self._backend.close(self._attempt)
            if not isinstance(evidence, TerminalRetirement) or evidence.attempt != self._attempt:
                raise ValueError("Backend did not confirm matching descendant cleanup")
            with self._condition:
                state = self._records.terminal
                if state.phase == "retired":
                    return
                if state.active_command is not None:
                    command = self._records.command(state.active_command)
                    if command.phase in ("accepted", "running"):
                        self._records.observe(command_event("observation_lost", command.request.ref,
                                                            reason="Terminal owner stopped"))
                self._session("retired", evidence=evidence.record())
                self._condition.notify_all()

    def close(self, timeout=5):
        with self._condition:
            self._closing = True
            self._cancel.set()
            worker = self._worker
            self._condition.notify_all()
        error = None
        try:
            self._cleanup()
        except Exception as exc:
            error = exc
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)
            if worker.is_alive():
                error = RuntimeError("Terminal worker still requires reconciliation")
        with self._condition:
            if error is not None:
                self._fail("Terminal descendant cleanup is unconfirmed")
                raise TerminalUnavailable(self._fault) from error
