"""Damaged and retired frontend publications never become accepted display state."""

import threading
from contextlib import ExitStack
from dataclasses import fields

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import drain, remote_window
from agent_worker_fixtures import heartbeat
from info_app_fixtures import notice_text
from sico.service.frontend_hub import FrontendHub
from sico.service.published import thaw
from sico.service.remote_catalog import RemoteCatalog
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_channel import ServiceChannel
from sico.service.workbench_query import WorkbenchQuery
from sico.transport.framing import ProtocolError
from sico_ui.service_connection import ServiceConnection


@pytest.mark.parametrize("catalog", [{}, {"complete": 1}, {"complete": "yes"}, []])
def test_catalog_completion_must_be_explicit_boolean_before_publication(catalog):
    publication = RemoteCatalog(None)
    previous = publication._view
    with pytest.raises(ProtocolError):
        publication._publish(dict(catalog=catalog, sessions=[]), ())
    assert publication._view is previous


@pytest.mark.parametrize("damage", ["bool", "watermark", "order", "size", "source", "snapshot"])
def test_corrupt_event_projection_is_rejected_before_cursor_moves(tmp_path, monkeypatch, damage):
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        controller = service.owner.controllers["live"]
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(token, 1, 200).result(5)
            api.control.request(token, "acquire").result(5)
            api.command(token, "submit", "recorded").result(5)
            assert controller.wait(5)
            stream = service.dispatch.frontend.replay._feeds[
                prepared.stream._wire_cursor.subscription_id][2]
            future = stream.request(service.dispatch.frontend.workspace.events)
            batch = future.result(5)
            stream.received(future)
            row = thaw({f.name: getattr(batch, f.name) for f in fields(batch)})
            if damage == "bool":
                row["activation"] = True
            elif damage == "watermark":
                row["committed"] = row["end"] - 1
            elif damage == "order":
                row["events"][-1]["sequence"] = row["events"][0]["sequence"]
            elif damage == "size":
                row["size"] += 1
            elif damage == "source":
                row["identity"][2] = "foreign"
            else:
                row["snapshot"][0]["sequence"] = row["end"] + 1
            before = prepared.stream._sequence
            with pytest.raises(ProtocolError):
                prepared.stream._publish(row)
            assert prepared.stream._sequence == before
        finally:
            api.close_desktop()


@pytest.mark.parametrize("change", ["query_connection", "runtime", "page"])
def test_late_query_cannot_cross_connection_runtime_or_page(tmp_path, monkeypatch, change):
    entered, release = threading.Event(), threading.Event()
    original = ServiceChannel.exchange

    def paused(channel, request, **kwargs):
        result = original(channel, request, **kwargs)
        if request.method == "frontend.query" and request.params["step"] == "read":
            entered.set()
            assert release.wait(5)
        return result

    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(token, 1, 200).result(5)
            source = api.source(token, scope=prepared.stream)
            monkeypatch.setattr(ServiceChannel, "exchange", paused)
            future = source.query_pages(WorkbenchQuery(query_id="late"))
            wait_until(entered.is_set)
            if change == "runtime":
                context = service.owner.controllers["live"].current
                service.owner.close_session("live")
                service.owner.open("live", {}, context, environment={})
                api.open_session("live").result(5)
            elif change == "page":
                prepared.stream.close()
            else:
                api._queries.epoch += 1
            release.set()
            with pytest.raises(ValueError):
                future.result(5)
        finally:
            release.set()
            api.close_desktop()


def test_explicit_reconnect_recovers_a_broken_stream(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        connection = ServiceConnection(window)
        window.binding.timer.stop()
        with pytest.raises(ProtocolError):
            window.api._events.request("invalid", {}).result(5)
        window.binding.poll()
        assert window.binding.failed and not service.owner.controllers["live"].closing
        connection.refresh()
        assert "断开" in connection.label.text()
        connection.reconnect()
        wait_until(lambda: not window.page.opening and not window.binding.failed)
        wait_until(lambda: window.binding.cursor is not None)
        heartbeat(window)


def test_close_choice_ends_sessions_then_stops_the_service(tmp_path, monkeypatch):
    """“结束服务”：先结束本窗口的会话，服务接受停止后窗口才退出。"""
    from sico_ui.service_connection import attach_service_connection

    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        window.close_choice.stop_everything()
        wait_until(lambda: "live" not in service.owner.controllers, timeout=8)
        wait_until(lambda: window.lifecycle.closing, timeout=8)
        assert service.owner.controllers == {}


def test_close_choice_stops_sessions_it_cannot_end(tmp_path, monkeypatch):
    """A project stop is authoritative even when this window cannot end a session."""
    from agent_service_gui_fixtures import ServiceTestWindow
    from sico.service.frontend_session import FrontendSession
    from sico_ui.service_connection import attach_service_connection
    from test_agent_service_control import attach

    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        holder, token = attach(stack, service, tmp_path)
        holder.control.request(token, "acquire").result(5)
        observer, observed = attach(stack, service, tmp_path)
        window = ServiceTestWindow(FrontendSession(observer, observed))
        stack.callback(window.deleteLater)
        stack.callback(window.request_quit)
        attach_service_connection(window)
        wait_until(lambda: not window.page.opening)
        window.close_choice.stop_everything()
        wait_until(lambda: window.lifecycle.closing, timeout=8)


def test_malformed_submit_response_preserves_draft_and_original_request(tmp_path, monkeypatch):
    original = FrontendHub.command
    keys = []

    def corrupt(hub, wire, operation, params):
        result = original(hub, wire, operation, params)
        if operation == "submit":
            keys.append(wire.operation)
            raise ProtocolError("Reply damaged after commit")
        return result

    with remote_window(tmp_path, monkeypatch) as (service, window):
        monkeypatch.setattr(FrontendHub, "command", corrupt)
        window.input.setPlainText("keep this draft")
        window.actions.submit()
        wait_until(lambda: "未确认" in notice_text(window))
        assert window.input.toPlainText() == "keep this draft"
        assert keys[0] in notice_text(window)
        result = window.api.query_input(window.page.session, keys[0]).result(5).status
        assert result.outcome == "accepted" and service.provider.seen == ["keep this draft"]


def test_new_runtime_cannot_receive_old_event_or_command(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        old = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(old, 1, 200).result(5)
            drain(api, prepared.stream)
            context = service.owner.controllers["live"].current
            service.owner.close_session("live")
            service.owner.open("live", {}, context, environment={})
            fresh = api.open_session("live").result(5)
            assert fresh.runtime_id != old.runtime_id
            with pytest.raises(ValueError):
                api.command(old, "submit", "must not send")
            with pytest.raises(ValueError):
                prepared.stream.validate_current()
            assert not service.provider.seen
        finally:
            api.close_desktop()


def test_close_choice_default_suspends_the_service(tmp_path, monkeypatch):
    """默认为“挂起”：窗口关闭而项目服务继续运行。"""
    from sico_ui.close_choice import WINDOW_ONLY
    from sico_ui.service_connection import attach_service_connection

    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        window.close_choice.ask()
        box = window.close_choice.dialog
        assert box is not None and box.button(WINDOW_ONLY).isDefault()
        box.button(WINDOW_ONLY).click()
        wait_until(lambda: window.lifecycle.closing, timeout=8)
        assert "live" in service.owner.controllers
