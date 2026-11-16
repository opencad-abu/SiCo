"""A model-free native startup is durable before the session catalog can expose it."""

import json
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from sico.codex.backend import CodexBackend
from sico.codex.thread_startup import persist_initial_source
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.service.remote_frontend import RemoteFrontend
from sico.storage.history import CatalogIndex
from sico.storage.journal import SessionJournal
from sico.storage.record_integrity import IncompleteSession
from sico.transport.bridge_client import BridgeClient
from sico_ui.home_window import HomeWindow
from sico_ui.service_connection import attach_service_connection
from sico_ui.window import AssistantWindow
from test_agent_codex_native import gateway, native_codex, settings  # noqa: F401
from test_ic_platform_service import host


def test_platform_metadata_startup_is_readable_without_a_task_or_model(
        tmp_path, monkeypatch, gateway, native_codex):  # noqa: F811
    provider = settings(gateway, native_codex, "native")
    provider.release = None
    with business_service(tmp_path, monkeypatch, provider=provider,
                          bridge_factory=BridgeClient) as service, ExitStack() as stack:
        bridge, _context = stack.enter_context(host(tmp_path))
        api = RemoteFrontend(service.descriptor, tmp_path, "")
        stack.callback(api.close_desktop)
        home = HomeWindow(str(tmp_path), tmp_path / "workspace.ini", lambda _frontend: None)
        stack.callback(home.deleteLater)
        stack.callback(home.finish)
        home.show()
        home.ready(api)
        frontend = api.platforms.open(bridge.path.stem, provider.options,
                                      provider.environment).result(10)
        token = frontend.session
        api.control.request(token, "acquire").result(5)
        window = AssistantWindow(frontend)
        attach_service_connection(window)
        window.show()
        controller = service.owner.controllers[token.session_id]
        try:
            wait_until(lambda: controller.loop.thread_id is not None, timeout=15)
            catalog = service.dispatch.frontend.workspace.catalog
            catalog.request().result(5)
            retained = catalog.snapshot()["retained"][token.session_id]
            assert not retained.get("unavailable"), retained
            wait_until(lambda: window.input.isEnabled() and not window.page.opening, timeout=5)
            assert not window.navigation._catalog_blocked_runtime
            journal = controller.journal
            assert not any(e["kind"] == "task.started" for e in journal.events())
            assert journal.state.task == {}
            assert controller.loop.state.task == {}
            assert not gateway.requests
            thread_id = controller.loop.thread_id
            rollout = next((journal.directory / "codex/sessions").glob("*/*/*/rollout-*.jsonl"))
            assert thread_id in rollout.name
            assert "SiCo" in rollout.read_text()
            context = controller.current
            wait_until(lambda: api.can_control(token), timeout=5)
            api.end_session(token, "end-empty").result(5)
            wait_until(lambda: not service.owner.controllers, timeout=8)
            api.recovery.inspect(token.session_id).result(5)
            api.preview_session(token.session_id).result(5)
            # A fresh backend must resume this exact empty native history.
            with SessionJournal(tmp_path, token.session_id) as reopened:
                backend = CodexBackend(provider, ToolRegistry(), reopened, context)
                try:
                    backend.resume_thread()
                    assert backend.thread_id == thread_id and not gateway.requests
                finally:
                    backend.close()
            # Actual missing history remains an error, including cached catalogs.
            index = CatalogIndex(journal.root)
            assert not index.read_session(token.session_id).get("unavailable")
            rollout.unlink()
            with pytest.raises(IncompleteSession):
                index.read_session(token.session_id)
            assert not gateway.requests
        finally:
            window.lifecycle.closing = True
            window.close()
            window.deleteLater()


def test_initial_source_is_metadata_only_and_checks_identity():
    context = BoundContext("v1", "g1", "bound", {"cwd": "/project"})
    calls = []

    class Rpc:
        def request(self, method, params):
            calls.append((method, params))
            raise RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        persist_initial_source(Rpc(), {"id": "thread"}, context)
    assert len(calls) == 1 and calls[0][0] == "thread/inject_items"
    item = calls[0][1]["items"][0]
    assert item["role"] == "developer"
    assert json.dumps(context.record(), ensure_ascii=False) in item["content"][0]["text"]
    from sico.codex.rpc import RpcError

    for thread in (None, {}, {"id": ""}):
        with pytest.raises(RpcError, match="Invalid thread identity"):
            persist_initial_source(Rpc(), thread, context)
    assert len(calls) == 1
