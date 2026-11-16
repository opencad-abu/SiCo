"""Failures must fence execution and preserve unresolved process ownership."""

from dataclasses import replace

import pytest

from bash_channel_fixtures import command
from session_terminal_fixtures import CLAIM
from sico.core.terminal_session import TerminalAttempt, TerminalGeneration, TerminalRetirement
from sico.core.terminal_session_events import event as session_event
from sico.core.terminal_session_state import apply_event as apply_session
from sico.core.terminal_state import apply_event, event
from sico.execution.managed_bash import ManagedBashBackend


ATTEMPT = TerminalAttempt(CLAIM.ref, 1, 'fixture', 'boot')
GENERATION = TerminalGeneration(ATTEMPT, 123, 456)

def test_new_context_is_optional_for_old_records_and_cannot_change_after_start():
    request = command()
    accepted = event('accepted', request.ref, request=request.record())
    state = apply_event(None, accepted)
    context = event('execution_context', request.ref, cwd='/kernel/cwd', generation=GENERATION.record())
    known = apply_event(state, context)
    assert known.execution_cwd == '/kernel/cwd' and known.generation == GENERATION
    assert apply_event(state, event('started', request.ref, execution_id='old')).execution_cwd is None
    with pytest.raises(ValueError):
        apply_event(known, context)
    with pytest.raises(ValueError):
        apply_event(apply_event(state, event('started', request.ref, execution_id='new')), context)
    foreign = replace(GENERATION, attempt=replace(ATTEMPT, ref=replace(ATTEMPT.ref, session_id='other')))
    with pytest.raises(ValueError):
        apply_event(state, event('execution_context', request.ref, cwd='/kernel/cwd', generation=foreign.record()))


def test_confirmed_startup_cleanup_is_distinct_from_no_spawn():
    state = apply_session(None, session_event('claimed', CLAIM.ref, claim=CLAIM.record()))
    state = apply_session(state, session_event('creation_requested', CLAIM.ref, attempt=ATTEMPT.record()))
    receipt = TerminalRetirement(ATTEMPT, 'owner_stopped', 'supervisor-cleaned')
    retired = apply_session(state, session_event('retired', CLAIM.ref, evidence=receipt.record()))
    assert retired.retirement.kind == 'owner_stopped'
    active = apply_session(state, session_event('started', CLAIM.ref, generation=GENERATION.record()))
    with pytest.raises(ValueError):
        apply_session(active, session_event('retired', CLAIM.ref, evidence=receipt.record()))


@pytest.fixture
def backend(monkeypatch, tmp_path):
    import sico.execution.managed_bash as module
    class Process:
        def __init__(self, *args, **kwargs):
            self.failure = False
            self.writes = 0
        def write(self, data):
            self.writes += 1
            raise RuntimeError('uncertain delivery')
        def close(self):
            if self.failure:
                raise RuntimeError('cleanup unconfirmed')
    monkeypatch.setattr(module, 'ManagedExecProcess', Process)
    value = ManagedBashBackend('/codex', {}, cwd=tmp_path, home=tmp_path, environment={},
                              environment_id=CLAIM.environment_id, access={})
    value._attempt = ATTEMPT
    return value


def test_unknown_cleanup_never_produces_retirement_and_later_confirmation_is_required(backend):
    backend._process.failure = True
    with pytest.raises(RuntimeError, match='unconfirmed'):
        backend.close(ATTEMPT)
    assert backend._retirement is None
    backend._process.failure = False
    receipt = backend.close(ATTEMPT)
    assert receipt.kind == 'owner_stopped' and receipt.generation is None
    assert backend.close(ATTEMPT) is receipt


def test_wrong_attempt_cannot_stop_current_owner(backend):
    with pytest.raises(ValueError):
        backend.close(replace(ATTEMPT, epoch=2))
    assert not backend._stopped.is_set()


def test_uncertain_command_delivery_is_never_retried(backend):
    backend._generation = GENERATION
    for _ in range(2):
        with pytest.raises((RuntimeError, ValueError)):
            backend.execute(command(), started=lambda *args: None, output=lambda *args: None,
                            cancelled=lambda: False)
    assert backend._process.writes == 1
