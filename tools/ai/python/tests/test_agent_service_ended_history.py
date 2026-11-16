"""Retired runtimes preserve read-only history without closing later attachments."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict

from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import drain
from agent_service_launch_fixtures import eventually
from test_agent_service_control import attach


def test_ended_history_is_readonly_in_original_and_new_client(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        address = open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        api.command(token, "submit", "retained history").result(5)
        assert service.owner.controllers["live"].wait(5)
        api.end_session(token, "history-end").result(5)
        eventually(lambda: api.end_session(token, "history-end", observe=True).result(5).state
                   == "ended")
        from sico.service.remote_frontend import RemoteFrontend

        fresh = RemoteFrontend(service.descriptor, tmp_path, "live")
        stack.callback(fresh.close_desktop)
        for client in (api, fresh):
            prepared = client.preview_session("live", activation=2).result(5)
            stack.callback(prepared.stream.close)
            assert prepared.stream._wire_cursor.readonly
            assert prepared.stream.identity[1] is None
            batches = drain(client, prepared.stream)
            assert batches[-1].end == prepared.committed_sequence
            assert not service.owner.controllers
            assert not client.can_control(address.session)


def test_late_empty_catalog_does_not_close_session_attached_after_request(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, _ = attach(stack, service, tmp_path)
        captured = api.opened_sessions()
        open_session(service, "later")
        later = api.open_session("later").result(5)
        api.catalog._publish(dict(sessions=[], catalog=None), captured)
        assert not api.is_closing(later)


def test_partial_catalog_does_not_close_an_attached_live_session(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        captured = api.opened_sessions()
        assert token in captured
        api.catalog._publish(dict(sessions=[], catalog=None), captured)
        assert not api.is_closing(token)
        api.catalog._publish(dict(sessions=[], catalog={"complete": False}), captured)
        assert not api.is_closing(token)
        api.catalog._publish(dict(sessions=[], catalog={"complete": True}), captured)
        assert api.is_closing(token)


def test_competing_identical_end_intents_observe_one_original_result(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        address = open_session(service)
        api, token = attach(stack, service, tmp_path)
        grant = api.control.request(token, "acquire").result(5)
        ending = service.dispatch.frontend.ending
        entry = service.dispatch.control._entries["live"]
        original = ending.observe
        barrier = threading.Barrier(2)
        def observe(address, operation):
            result = original(address, operation)
            if result.state == "unknown":
                barrier.wait(5)
            return result
        monkeypatch.setattr(ending, "observe", observe)
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = [pool.submit(ending.request, address, "same-end", entry.holder,
                                    asdict(grant.claim)) for _ in range(2)]
            assert all(receipt.result(5).state in {"closing", "ended"} for receipt in receipts)
        eventually(lambda: original(address, "same-end").state == "ended")
        from sico.storage.journal import SessionJournal

        with SessionJournal(tmp_path, "live") as journal:
            assert sum(e["kind"] == "session.end_requested" for e in journal.events()) == 1
