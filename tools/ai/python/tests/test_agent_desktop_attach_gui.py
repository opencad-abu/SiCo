"""Host attach uses real Qt navigation and service control through completion."""

import queue
import threading
from concurrent.futures import Future
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import open_session
from agent_service_gui_fixtures import remote_window
from agent_worker_fixtures import heartbeat
from info_app_fixtures import notice_text
from sico_ui.lifecycle import DesktopControl
from test_agent_service_control import attach


def desktop_control(window):
    reader = SimpleNamespace(closed=threading.Event(), lines=queue.Queue(),
                             close=lambda **_kwargs: None)
    control = DesktopControl(reader)
    control.attach(window)
    return control


def send_attach(control, session_id):
    control.reader.lines.put(dict(kind="attach", session_id=session_id))
    control.poll()


@pytest.mark.parametrize("catalogued", [False, True])
@pytest.mark.parametrize("preview", [False, True])
def test_attach_keeps_the_accepted_session_current_and_writable(
    tmp_path, monkeypatch, catalogued, preview,
):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        session_id = "a" * 32
        open_session(service, session_id)
        owner = service.owner.controllers[session_id]
        owner.submit("ADE Ask accepted before desktop wake")
        assert owner.wait(5)
        assert not any(row["kind"] == "session.control" for row in owner.journal.events())
        window.input.setPlainText("initial session draft")
        if preview:
            window.page.preview_session(session_id)
            wait_until(lambda: not window.page.opening)
        if catalogued:
            window.api.catalog.refresh().result(5)
            assert window.api.session(session_id) is not None
        control = desktop_control(window)
        try:
            send_attach(control, session_id)
            # Focus must not invalidate the pending attach/replay receipt.
            assert window.page.opening
            wait_until(lambda: window.page.session.session_id == session_id
                       and not window.page.opening and window.send.isEnabled())
            token = window.page.session
            assert window.api.can_control(token)
            assert window.page.reviewing is None
            assert session_id in window.page.opened
            assert not window.input.isReadOnly()
            assert [row["payload"]["action"] for row in owner.journal.events()
                    if row["kind"] == "session.control"] == ["acquire"]
            window.input.setPlainText("follow-up after the Ask reply")
            window.actions.submit()
            wait_until(lambda: service.provider.seen[-1] == "follow-up after the Ask reply"
                       and window.send.isEnabled() and not window.input.toPlainText())
            assert owner.wait(5)
            heartbeat(window)
            assert window.page.session == token and not window.api.is_closing(token)
            send_attach(control, session_id)
            heartbeat(window)
            assert window.page.session == token and window.api.can_control(token)
            window.page.activate_session("live")
            wait_until(lambda: not window.page.opening)
            assert window.input.toPlainText() == "initial session draft"
        finally:
            control.close()


def test_attach_does_not_take_control_from_another_live_connection(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window), ExitStack() as stack:
        session_id = "b" * 32
        open_session(service, session_id)
        other, token = attach(stack, service, tmp_path, session_id)
        other.control.request(token, "acquire").result(5)
        control = desktop_control(window)
        try:
            send_attach(control, session_id)
            wait_until(lambda: window.page.session.session_id == session_id
                       and "其他连接" in notice_text(window))
            assert other.can_control(token) and not window.api.can_control(token)
            assert not window.send.isEnabled()
            events = service.owner.controllers[session_id].journal.events()
            assert [row["payload"]["action"] for row in events
                    if row["kind"] == "session.control"] == ["acquire"]
        finally:
            control.close()


@pytest.mark.parametrize("exists", [False, True])
def test_attach_from_an_ended_page_completes_or_reports_failure(tmp_path, monkeypatch, exists):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        token = window.page.session
        window.api.end_session(token, "end-initial").result(5)
        wait_until(lambda: window.api.is_closing(token))
        session_id = "c" * 32
        if exists:
            open_session(service, session_id)
        control = desktop_control(window)
        try:
            send_attach(control, session_id)
            wait_until(lambda: not window.page.opening)
            if exists:
                wait_until(lambda: window.api.can_control(window.page.session))
                assert window.page.session.session_id == session_id
            else:
                assert window.page.session == token
                assert "会话未打开" in notice_text(window)
        finally:
            control.close()


def test_later_attach_supersedes_pending_replay_without_acquiring_the_old_target(
    tmp_path, monkeypatch,
):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        first, second = "d" * 32, "e" * 32
        for session_id in (first, second):
            open_session(service, session_id)
            window.api.open_session(session_id).result(5)
        original = window.api.prepare_replay
        pending = Future()
        pending.set_running_or_notify_cancel()
        held = []

        def replay(token, activation, messages):
            if token.session_id == first:
                held.append(original(token, activation, messages).result(5))
                return pending
            return original(token, activation, messages)

        monkeypatch.setattr(window.api, "prepare_replay", replay)
        control = desktop_control(window)
        try:
            send_attach(control, first)
            assert held and window.page.opening
            send_attach(control, second)
            wait_until(lambda: window.page.session.session_id == second
                       and window.api.can_control(window.page.session))
            pending.set_result(held[0])
            heartbeat(window)
            assert window.page.session.session_id == second
            assert not window.api.can_control(window.api.session(first))
            assert not any(row["kind"] == "session.control"
                           for row in service.owner.controllers[first].journal.events())
        finally:
            if not pending.done():
                pending.set_result(held[0])
            held[0].stream.close()
            control.close()


def test_startup_wake_overflow_is_visible_without_changing_admission(monkeypatch):
    from test_ask_activation_gui import activation

    ask, _page, _calls, notices, _receipts = activation()
    reader = SimpleNamespace(closed=threading.Event(), lines=queue.Queue(),
                             close=lambda **_kwargs: None)
    control = DesktopControl(reader)
    captured = []
    monkeypatch.setattr(control, "_activate", lambda *args: captured.append(args))
    control.ask_activation = ask
    try:
        for sequence in range(1, 66):
            reader.lines.put(dict(kind="attach", session_id="a" * 32,
                                  input_id="q" + str(sequence), ask_sequence=sequence))
            control.poll()
        assert len(control.pending_asks) == 64 and control.ask_overflow
        control.attach(ask.window)
        assert len(captured) == 64 and captured[0][1:] == ("q1", 1)
        assert "任务接收状态不变" in notices[-1][-1]
        assert not control.pending_asks and not control.ask_overflow
    finally:
        control.close()


def test_sequenced_wake_uses_real_receipt_after_navigation_and_ended_page(tmp_path, monkeypatch):
    from sico.core.contracts import BoundContext
    from sico.service.frontend_session import SessionToken
    from sico.service.service_session_dto import SessionAddress

    with remote_window(tmp_path, monkeypatch) as (service, window):
        session_id = "f" * 32
        open_session(service, session_id)
        owner = service.owner.controllers[session_id]
        owner.paused = True
        address = SessionAddress(service.descriptor.project_id, service.descriptor.service_id,
                                 SessionToken(session_id, owner.runtime_id)).record()
        for input_id, sequence in (("first", 1), ("second", 2)):
            context = BoundContext("instance", "generation", input_id, {"valid": True})
            owner.accept(dict(kind="submit", id=input_id, text=input_id,
                              context=context.record(), ask_sequence=sequence),
                         service_address=address)
        control = desktop_control(window)
        query = window.api.ask_head
        pending = Future()
        monkeypatch.setattr(window.api, "ask_head", lambda *_args: pending)

        def wake(input_id, sequence):
            control.reader.lines.put(dict(kind="attach", session_id=session_id,
                                          input_id=input_id, ask_sequence=sequence))
            control.poll()

        try:
            wake("first", 1)
            window.page.invalidate()
            pending.set_result(query(session_id, "first").result(5))
            wait_until(lambda: not control.ask_activation.wake.busy)
            assert window.page.session.session_id == "live"
            monkeypatch.setattr(window.api, "ask_head", query)
            window.api.end_session(window.page.session, "end-live").result(5)
            wait_until(lambda: window.api.is_closing(window.page.session))
            wake("second", 2)
            wait_until(lambda: not control.ask_activation.wake.busy)
            assert window.page.session.session_id == "live"
            wake("first", 1)
            wait_until(lambda: window.page.session.session_id == session_id
                       and window.api.can_control(window.page.session))
            assert [r["id"] for r in owner.inbox.pending] == ["first", "second"]
            assert not service.provider.seen
            assert [r["payload"]["action"] for r in owner.journal.events()
                    if r["kind"] == "session.control"] == ["acquire"]
        finally:
            control.close()
