"""Partial construction and overloaded disposal release frontend-owned resources."""

import threading

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session

from sico.service.frontend_hub import FrontendHub
from sico.service.project_sessions import ProjectSessionOwner
from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_channel import ServiceChannel


def test_hub_partial_constructor_stops_its_workspace(tmp_path, monkeypatch):
    from sico.service import frontend_hub

    captured = []
    original = frontend_hub.ProjectWorkspace
    def own(owner):
        workspace = original(owner)
        captured.append(workspace)
        return workspace
    def failed(*args):
        raise RuntimeError("attach construction failed")

    monkeypatch.setattr(frontend_hub, "ProjectWorkspace", own)
    monkeypatch.setattr(frontend_hub, "FrontendAttach", failed)
    with ProjectSessionOwner(tmp_path, environment={}) as owner:
        with pytest.raises(RuntimeError, match="construction"):
            FrontendHub(owner, None)
    assert len(captured) == 1
    workspace = captured[0]
    for service in (workspace.events, workspace.data, workspace.catalog, workspace.worker):
        assert not service._thread.is_alive()
    assert workspace.background.wait(0)


def test_full_event_queue_disposal_retires_connection_and_subscriptions(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(token, 1, 200).result(5)
            original = ServiceChannel.exchange
            def paused(channel, request, **kwargs):
                if request.method == "frontend.events" and not entered.is_set():
                    entered.set()
                    assert release.wait(5)
                return original(channel, request, **kwargs)
            monkeypatch.setattr(ServiceChannel, "exchange", paused)
            api.event_batch(prepared.stream)
            wait_until(entered.is_set)
            for _ in range(32):
                api._events.request("batch", dict(cursor=prepared.stream._wire_cursor.record()))
            prepared.stream.close()
            assert api._events.retiring.is_set()
            release.set()
            wait_until(lambda: not service.dispatch.frontend.replay._feeds)
            wait_until(lambda: not service.dispatch.frontend.workspace.events._streams)
            assert not service.owner.controllers["live"].closing
        finally:
            release.set()
            api.close_desktop()


def test_published_local_session_cannot_retire_before_resource_transfer(tmp_path, monkeypatch):
    from contextlib import ExitStack

    from sico.service import frontend_attach

    entered, release, retiring = (threading.Event() for _ in range(3))
    closed = []
    original_peer = frontend_attach.DemoPeer
    original_own = ProjectSessionOwner.own_resource
    original_retire = ProjectSessionOwner.retire_session

    def peer(*args, **kwargs):
        result = original_peer(*args, **kwargs)
        close = result.close
        def close_peer():
            closed.append("peer")
            close()
        result.close = close_peer
        return result

    def own(owner, controller, close):
        entered.set()
        assert release.wait(5)
        original_own(owner, controller, close)

    def retire(owner, controller, **kwargs):
        retiring.set()
        return original_retire(owner, controller, **kwargs)

    monkeypatch.setattr(frontend_attach, "DemoPeer", peer)
    monkeypatch.setattr(ProjectSessionOwner, "own_resource", own)
    monkeypatch.setattr(ProjectSessionOwner, "retire_session", retire)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        opening = RemoteFrontend(service.descriptor, tmp_path, "local")
        observer = RemoteFrontend(service.descriptor, tmp_path, "local")
        stack.callback(opening.close_desktop)
        stack.callback(observer.close_desktop)
        ready = opening.open_initial("demo", dict(session_id="local", provider_config=None,
            environment={}, timeout=3, context_file=None))
        try:
            assert entered.wait(3)
            token = observer.open_session("local").result(3)
            observer.control.request(token, "acquire").result(3)
            observer.end_session(token, "concurrent-end").result(3)
            assert retiring.wait(3)
            assert observer.end_session(token, "concurrent-end", observe=True).result(3).state \
                == "closing"
            assert service.owner.controllers["local"].journal._fd >= 0
            assert not closed
            release.set()
            ready.result(3)
            wait_until(lambda: observer.end_session(token, "concurrent-end", observe=True)
                       .result(3).state == "ended")
            assert closed == ["peer"]
        finally:
            release.set()
