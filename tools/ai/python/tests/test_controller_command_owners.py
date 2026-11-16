"""Retired Controller aliases cannot route queued commands or regain authority."""

from sico.service.frontend_session import SessionToken

from concurrent.futures import Future
import threading
from types import SimpleNamespace

import pytest

from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.service.python_backend import PythonBackend
from sico.service.worker import WorkerApi
from sico.storage.journal import SessionJournal


RETIRED = {
    'bindings': ('reserve_submission', 'release_submission', 'attach_targets',
                 'poll_binding_events', 'propose_binding', 'resolve_binding',
                 'select_binding_target', 'configure_bindings', 'release_binding',
                 'release_binding_resource', 'reconcile_binding_operation', 'released_targets'),
    'backend_commands': ('turn_input_status', 'resource_status', 'steer', 'steering_scope',
                         'check_target_request', 'answer_audit', 'elicitation'),
    'thread_commands': ('thread_scope', 'thread_status', 'thread_operation'),
    'reconciliation': ('query_interrupted', 'resolve_interrupted'),
    'router': ('poll_router_status', 'cancel_router_request', 'router_receipt'),
    'updates': ('read_updates', '_state_snapshot'),
}
QUEUED = [('bindings', 'configure_bindings'), ('backend_commands', 'resource_status'),
          ('thread_commands', 'thread_status'), ('reconciliation', 'query_interrupted'),
          ('router', 'router_receipt')]


@pytest.fixture
def session(tmp_path):
    with SessionJournal(tmp_path, 'owners') as journal:
        controller = SessionController(PythonBackend(
            ScriptedProvider([]), ToolRegistry(), journal, BoundContext('i', 'g', 'target', {})))
        sessions = SimpleNamespace(controllers={'owners': controller},
            catalog=SimpleNamespace(request=lambda: None, close=lambda: None),
            _close_controllers=controller.close)
        worker = WorkerApi(sessions)
        try:
            yield controller, worker
        finally:
            worker.close()
            assert worker.wait(5)
            assert controller.wait(5)


@pytest.mark.parametrize('component', RETIRED)
def test_components_are_the_only_command_surface(session, component):
    controller, _ = session
    for name in RETIRED[component]:
        assert not hasattr(controller, name), name
        assert callable(getattr(getattr(controller, component), name)), name


@pytest.mark.parametrize('component,name', QUEUED)
def test_worker_calls_the_owner_even_if_a_stale_alias_is_present(session, monkeypatch, component, name):
    controller, worker = session
    calls = []
    owner_thread = threading.get_ident()

    def current(*args, **kwargs):
        assert threading.get_ident() != owner_thread
        calls.append((args, kwargs))
        return 'owner receipt'

    def stale(*args, **kwargs):
        pytest.fail('Retired Controller alias was invoked')

    monkeypatch.setattr(getattr(controller, component), name, current)
    monkeypatch.setattr(controller, name, stale, raising=False)
    assert worker.command(SessionToken(controller.session_id, controller.runtime_id), name, 'captured', request='identity').result(5) == 'owner receipt'
    assert calls == [(('captured',), {'request': 'identity'})]


@pytest.mark.parametrize('component,name', QUEUED)
def test_queued_component_command_rejects_a_replaced_runtime(session, monkeypatch, component, name):
    controller, worker = session
    entered, release = threading.Event(), threading.Event()
    calls = []

    def block():
        entered.set()
        assert release.wait(5)

    blocker = worker._request(block)
    original_runtime = controller.runtime_id
    try:
        assert entered.wait(5)
        monkeypatch.setattr(getattr(controller, component), name, lambda: calls.append(name))
        receipt = worker.command(SessionToken(controller.session_id, controller.runtime_id), name)
        controller.runtime_id = 'replacement'
        release.set()
        blocker.result(5)
        with pytest.raises(ValueError, match='runtime'):
            receipt.result(5)
        assert not calls
    finally:
        release.set()
        controller.runtime_id = original_runtime


def test_async_owner_receipt_does_not_hold_the_command_queue(session, monkeypatch):
    controller, worker = session
    native = Future()
    monkeypatch.setattr(controller.backend_commands, 'steer', lambda: native)
    receipt = worker.command(SessionToken(controller.session_id, controller.runtime_id), 'steer')
    assert worker._request(lambda: 'queue available').result(5) == 'queue available'
    assert not receipt.done()
    native.set_exception(ValueError('native turn changed'))
    with pytest.raises(ValueError, match='native turn changed'):
        receipt.result(5)


@pytest.mark.parametrize('name', ['read_updates', '_state_snapshot', 'steering_scope',
                                 'thread_scope', 'poll_router_status', 'release_submission'])
def test_internal_component_capabilities_are_not_worker_commands(session, name):
    controller, worker = session
    with pytest.raises(ValueError, match='Unknown worker command'):
        worker.command(SessionToken(controller.session_id, controller.runtime_id), name)


@pytest.mark.parametrize('operation', [
    lambda worker, value: worker.command(value, 'rename', 'must not run'),
    lambda worker, value: worker.accept_quick(value, {}),
    lambda worker, value: worker.prepare_replay(value, 1, 20),
])
def test_worker_rejects_controller_and_token_lookalikes_at_admission(session, operation):
    controller, worker = session
    for value in (controller, SimpleNamespace(session_id=controller.session_id,
                                              runtime_id=controller.runtime_id)):
        with pytest.raises(TypeError, match='SessionToken'):
            operation(worker, value)
