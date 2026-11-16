"""Initial startup work belongs only to the first prepared page activation."""

import threading

import agent_startup_harness as startup_module
import pytest
from agent_gui_wait import settle, wait_until
from agent_local_startup_fixtures import local_setup as local_setup
from agent_startup_fixture import startup_backend
from agent_worker_fixtures import heartbeat
from info_app_fixtures import notice_text

import sico.service.worker as worker_module
from sico.core.contracts import BoundContext
from sico.providers.mock import ScriptedProvider


@pytest.mark.parametrize("command", ["demo", "connect"])
@pytest.mark.parametrize("change", ["stay", "activate", "roundtrip", "preview", "close", "fail"])
def test_startup_initialization_respects_page_identity_and_draft(
    local_setup, monkeypatch, command, change,
):
    entered, release = threading.Event(), threading.Event()
    original = worker_module.prepare_replay
    provider = ScriptedProvider([])
    provider.label = "startup test"
    monkeypatch.setattr(startup_module, "provider_from_config", lambda *a, **kw: provider)

    def blocked(*args, **kwargs):
        assert threading.get_ident() != local_setup.owner
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
            if change == "fail":
                raise OSError("initial replay failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_module, "prepare_replay", blocked)
    startup, view = local_setup.create(command)
    try:
        wait_until(lambda: entered.is_set() and view.window is not None)
        window = view.window
        first = window.page.session
        assert window.page.opening and window.input.toPlainText() == "draft"
        window.input.setPlainText("new unsent draft")
        heartbeat(window)
        assert not release.is_set() and not provider.requests
        if change in {"activate", "roundtrip", "preview"}:
            context = startup_backend(startup).controller.current
            other = BoundContext(context.instance_id, context.generation, "other",
                                 {"valid": True, "cwd": context.snapshot.get("cwd")})
            startup_backend(startup).broker.register_target(other)
            second = window.api.create_session(other).result(3)
            if change == "preview":
                window.page.preview_session(second.session_id)
            else:
                window.page.activate_session(second.session_id)
                if change == "roundtrip":
                    window.page.activate_session(first.session_id)
        elif change == "close":
            window.request_quit()
        release.set()
        wait_until(lambda: not view._initial_timer.isActive())
        if change == "close":
            view.close()
            assert startup.wait(5)
        elif change == "fail":
            wait_until(lambda: not window.page.opening and not window.api.desktop_busy)
            heartbeat(window)
        else:
            settle(window)
        journals = [controller.journal for controller in
                    startup_backend(startup).sessions.controllers.values()]
        starts = [event for journal in journals for event in journal.events()
                  if event["kind"] == "task.started"]
        if change == "stay":
            assert len(starts) == 1 and starts[0]["payload"]["origin"] == "startup"
            assert window.input.toPlainText() == "new unsent draft"
        else:
            assert not starts
            if change == "preview":
                assert window.presentation.replay.read_only and not window.send.isEnabled()
            elif change == "fail":
                assert "initial replay failed" in notice_text(window)
            elif change == "roundtrip":
                assert window.input.toPlainText() == "new unsent draft"
        assert not provider.requests
    finally:
        release.set()
