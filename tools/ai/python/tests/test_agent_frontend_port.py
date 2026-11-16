"""Frontend capabilities reject backend inputs and stale queued identities."""

import threading
from concurrent.futures import Future
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest
from agent_boundary_guard import product_errors
from agent_frontend_harness import FrontendPort
from agent_gui_wait import wait_until
from agent_worker_fixtures import window as window
from sico.service.frontend_session import SessionToken
from sico.service.remote_objects import RemoteObjectSource
from sico.service.workbench_query import WorkbenchQuery


def test_public_port_and_async_products_have_no_backend_escape(window, monkeypatch):
    from sico.core.tools import ToolRegistry

    monkeypatch.setattr(window.sessions, "tools_factory", lambda _journal: ToolRegistry())
    api, token = window.api, window.page.session
    assert type(api) is FrontendPort
    assert [field.name for field in fields(token)] == ["session_id", "runtime_id"]
    assert vars(token) == {"session_id": token.session_id, "runtime_id": token.runtime_id}
    assert not product_errors(api)
    assert not product_errors(token)
    assert not product_errors(api.view(token))
    opened = api.open_session("history").result(5)
    assert type(opened) is SessionToken
    created = api.create_session(api.view(token).context).result(5)
    assert type(created) is SessionToken
    replay = api.prepare_replay(token, 91, 200).result(5)
    preview = api.preview_session("history", 92, 200).result(5)
    sources = [api.source(token, scope=replay.stream), api.preview_source(preview)]
    try:
        for value in (replay, preview, replay.stream, preview.stream, *sources):
            assert not product_errors(value)
        for source in sources:
            _, publication = source.query_pages(WorkbenchQuery()).result(5)
            assert publication.source is source
            assert publication.identity == source.identity
    finally:
        replay.stream.close()
        preview.stream.close()
        for source in sources:
            source.close()


@pytest.mark.parametrize("operation", [
    lambda api, token: api.handle(token),
    lambda api, token: api.view(token),
    lambda api, token: api.command(token, "rename", "name"),
    lambda api, token: api.accept_quick(token, {}),
    lambda api, token: api.prepare_replay(token, 1, 20),
    lambda api, token: api.source(token),
    lambda api, token: api.tool_result(token, {}),
    lambda api, token: api.background_request(token, "list_background"),
    lambda api, token: api.target_source(token),
])
def test_session_operations_reject_raw_and_expired_inputs(window, operation):
    for value in (object(), window.controller, SimpleNamespace(**vars(window.page.session))):
        with pytest.raises(TypeError):
            operation(window.api, value)
    with pytest.raises(ValueError, match="runtime"):
        operation(window.api, replace(window.page.session, runtime_id="expired"))


@pytest.mark.parametrize("kind", ["replay", "detail", "background", "source", "quick"])
def test_runtime_rechecked_on_independent_worker_execution(window, monkeypatch, kind):
    api, owner, sessions = window.api, window.controller, window.sessions
    service = {"replay": sessions.events, "detail": sessions.data,
               "source": sessions.data, "background": sessions.background._api,
               "quick": sessions.worker}[kind]
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(5)

    blocker = service._request(hold)
    assert entered.wait(5)
    calls = []
    monkeypatch.setattr(sessions.quick_input, "accept", lambda *args: calls.append(args))
    source = api.source(window.page.session)
    operations = {
        "replay": lambda: api.prepare_replay(window.page.session, 20, 200),
        "detail": lambda: api.tool_result(window.page.session, {}),
        "background": lambda: api.background_request(window.page.session, "list_background"),
        "source": lambda: source.query_pages(WorkbenchQuery()),
        "quick": lambda: api.accept_quick(window.page.session, {}),
    }
    receipt = operations[kind]()
    runtime = owner.runtime_id
    owner.runtime_id = "replacement"
    try:
        release.set()
        blocker.result(5)
        with pytest.raises(ValueError, match="runtime"):
            receipt.result(5)
        assert not calls
    finally:
        release.set()
        owner.runtime_id = runtime
        source.close()


def test_replaced_controller_with_same_session_id_cannot_receive_queued_command(window):
    sessions, token = window.sessions, window.page.session
    original = window.controller
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(5)

    blocker = sessions.worker._request(hold)
    assert entered.wait(5)
    receipt = window.api.command(token, "rename", "stale name")
    replacement = SimpleNamespace(session_id=token.session_id, runtime_id="other-runtime")
    sessions.controllers[token.session_id] = replacement
    try:
        release.set()
        blocker.result(5)
        with pytest.raises(ValueError, match="runtime"):
            receipt.result(5)
    finally:
        release.set()
        sessions.controllers[token.session_id] = original


def test_discarded_preparation_is_retired_without_publishing_backend(window, monkeypatch):
    events = window.sessions.events
    entered, release = threading.Event(), threading.Event()
    original = window.controller.journal.prepare_replay

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    before = set(events._streams)
    monkeypatch.setattr(window.controller.journal, "prepare_replay", slow)
    receipt = window.api.prepare_replay(window.page.session, 30, 200)
    try:
        assert entered.wait(5)
        receipt.discard()
        assert receipt.cancel()
    finally:
        release.set()
    wait_until(lambda: not events.busy)
    wait_until(lambda: set(events._streams) <= before)


def test_frontend_cannot_inject_broker_or_block_shutdown(window):
    with pytest.raises(TypeError):
        window.api.command(window.page.session, "attach_targets", object())
    with pytest.raises(ValueError, match="nonblocking"):
        window.api.wait_desktop(1)


def test_quick_receipt_publishes_token_and_keeps_durable_success(window, monkeypatch):
    from sico.service.quick_routing import QuickInputReceipt

    owner, sessions = window.controller, window.sessions

    def accepted(_owner, _message):
        sessions.controllers.pop(owner.session_id)
        return QuickInputReceipt(owner, False, True)

    monkeypatch.setattr(sessions.quick_input, "accept", accepted)
    try:
        result = window.api.accept_quick(window.page.session, {}).result(5)
        assert result.accepted and result.session == window.page.session
        assert not product_errors(result)
        assert not hasattr(result, "controller")
    finally:
        sessions.controllers[owner.session_id] = owner


def test_target_lookup_rechecks_runtime_before_bridge_call(window):
    sessions, owner = window.sessions, window.controller
    calls = []
    sessions.broker = SimpleNamespace(circuit_call=lambda *args: calls.append(args))
    source = window.api.target_source(window.page.session)
    entered, release = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(5)

    blocker = sessions.targets._request(hold)
    assert entered.wait(5)
    receipt = source.libraries()
    runtime = owner.runtime_id
    owner.runtime_id = "replacement"
    try:
        release.set()
        blocker.result(5)
        with pytest.raises(ValueError, match="runtime"):
            receipt.result(5)
        assert not calls
    finally:
        release.set()
        owner.runtime_id = runtime
        source.close()
        sessions.broker = None


def test_remote_object_source_uses_read_only_frontend_query():
    calls = []

    class Queries:
        epoch = 1

        def request(self, operation, params, publish, *, validate=None):
            value = params()
            if validate is not None:
                validate()
            calls.append((operation, value))
            future = Future()
            future.set_result(publish({"ok": True, "value": value["operation"]}))
            return future

    class Api:
        _queries = Queries()

        def handle(self, _token):
            return None

    address = SimpleNamespace(record=lambda: {"session_id": "s"})
    source = RemoteObjectSource(Api(), address, object())
    assert source.window_list().result()["value"] == "window_list"
    assert calls[0][0] == "object_context"
    assert calls[0][1]["operation"] == "window_list"


def test_remote_object_result_survives_local_close_after_dispatch():
    class Queries:
        epoch = 1

        def request(self, _operation, params, publish, *, validate=None):
            value = params()
            if validate is not None:
                validate()
            future = Future()
            future.set_result(publish({"ok": True, "value": value["operation"]}))
            return future

    class Api:
        _queries = Queries()

        def handle(self, _token):
            return None

    address = SimpleNamespace(record=lambda: {"session_id": "s"})
    source = RemoteObjectSource(Api(), address, object())
    result = source.highlight({"kind": "instance", "name": "V6"})
    source.close()
    assert result.result(5)["value"] == "highlight"
    with pytest.raises(ValueError, match="对象读取器已关闭"):
        source.window_list()


def test_remote_object_query_preserves_the_native_error_code():
    class Queries:
        epoch = 1

        def request(self, _operation, params, publish, *, validate=None):
            params()
            if validate is not None:
                validate()
            future = Future()
            try:
                value = publish({
                    "ok": False, "code": "target_not_open",
                    "message": "The requested cellview is not open",
                })
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(value)
            return future

    class Api:
        _queries = Queries()

        def handle(self, _token):
            return None

    address = SimpleNamespace(record=lambda: {"session_id": "s"})
    source = RemoteObjectSource(Api(), address, object())
    with pytest.raises(ValueError) as captured:
        source.highlight({"kind": "instance", "name": "V6"}).result(5)
    assert captured.value.code == "target_not_open"
