"""Service transport preserves recap control authority and delivers the footer."""
# ruff: noqa: F401, F811 -- pytest fixture registration and injection

import threading
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import remote_window
from test_agent_service_control import attach
from test_agent_codex_native import answer, gateway, native_codex, registry, settings  # noqa: F401
from test_codex_steering_gui import app  # noqa: F401

from sico.codex.backend import CodexBackend


def test_recap_start_requires_current_control(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        calls = []
        owner = service.owner.controllers["live"].recap
        monkeypatch.setattr(owner, "start", lambda **kwargs: calls.append(kwargs) or {})
        with pytest.raises(ValueError, match="只读"):
            api.command(token, "recap_start", expected_task="", request_id="one", automatic=False).result(5)
        assert not calls
        api.control.request(token, "acquire").result(5)
        api.command(token, "recap_start", expected_task="", request_id="one", automatic=False).result(5)
        assert calls == [{"expected_task": "", "request_id": "one", "automatic": False}]
        assert api.command(token, "recap_status").result(5)["supported"] is False


def test_service_backed_window_displays_generated_recap(tmp_path, monkeypatch, gateway, native_codex, app):
    monkeypatch.setattr("sico.service.project_sessions.create_backend",
        lambda provider, _tools, journal, context: CodexBackend(provider, registry([]), journal, context))
    provider = settings(gateway, native_codex, "native")
    provider.release = threading.Event()
    with remote_window(tmp_path, monkeypatch, provider=provider) as (service, window):
        controller = service.owner.controllers["live"]
        window.show()
        wait_until(window.steering_input.available)
        gateway.responses.put(answer("接口检查完成。"))
        window.input.setPlainText("检查接口")
        window.actions.submit()
        wait_until(lambda: window.presentation.state.get("task", {}).get("status") == "completed", timeout=15)
        panel = window.recap_panel
        panel.tick()
        wait_until(lambda: panel.receipt.pending is None)
        gateway.responses.put(answer('{"recap":"接口检查完成，下一步确认测试条件。"}'))
        window.input.setPlainText("未发送的草稿")
        panel.button.click()
        wait_until(lambda: controller.recap.status()["text"], timeout=15)
        panel.tick()
        wait_until(lambda: "下一步确认测试条件" in panel.label.text())
        assert panel.isVisible() and window.input.toPlainText() == "未发送的草稿"
        assert len(gateway.requests) == 2
