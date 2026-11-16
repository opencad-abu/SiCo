"""Wire replay preserves committed watermarks, page identities and source validation."""

import threading
from dataclasses import replace

import pytest
from agent_gui_wait import wait_until
from agent_replay_fixtures import blocked_append, history_item, record_task
from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import drain, remote_window
from agent_worker_fixtures import heartbeat

from sico.service.remote_frontend import RemoteFrontend
from sico.service.workbench_query import WorkbenchQuery
from sico.storage.journal import SessionJournal
from sico.transport.framing import ProtocolError


@pytest.mark.parametrize("close", [False, True])
def test_fsync_paused_switch_return_and_close_leave_qt_alive(tmp_path, monkeypatch, close):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        controller = service.owner.controllers["live"]
        other = open_session(service, "other")
        window.api.open_session("other").result(5)
        original_activation = window.page.activation
        with blocked_append(monkeypatch, controller) as gate:
            window.page.activate_session("other")
            window.page.activate_session("live")
            window.page.activate()
            heartbeat(window)
            assert not gate.release.is_set() and window.page.activation > original_activation
            if close:
                window.request_quit()
                heartbeat(window)
                assert window.api.detached and not controller.closing
        if not close:
            wait_until(lambda: not window.page.opening and window.binding.cursor is not None)
            wait_until(lambda: window.binding.cursor.sequence == len(controller.journal.records))
            assert not window.binding.failed
            assert window.page.session != other.session


def test_preparation_incremental_replay_and_preview_keep_fixed_commits(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        controller = service.owner.controllers["live"]
        journal, context = controller.journal, controller.current
        record_task(journal, context, "old", native=True)
        journal.append("codex.item", history_item(journal), controller.loop.state)
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(address.session, 2, 1).result(5)
            preview = api.preview_session("live", 3, 1).result(5)
            committed = preview.committed_sequence
            journal.append("session.note", {"text": "later"}, controller.loop.state)
            live = drain(api, prepared.stream)
            assert live[-1].end == len(journal.records)
            assert all(b.snapshot is None or b.snapshot[0]["sequence"] <= b.end for b in live)
            history = drain(api, preview.stream)
            assert history[-1].end == committed
            assert all(b.committed == committed for b in history)
            journal.append("session.note", {"text": "next"}, controller.loop.state)
            tail = drain(api, prepared.stream)
            assert tail[0].start == live[-1].end + 1
            assert tail[-1].end == len(journal.records)
            changed = replace(address.session, runtime_id="f" * 32)
            assert not api.replay_matches(prepared, changed, 2)
        finally:
            api.close_desktop()


def test_history_preview_never_creates_writer_and_foreign_client_is_rejected(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        with SessionJournal(tmp_path, "history") as journal:
            record_task(journal, service.owner.controllers["live"].current, "saved")
        path = tmp_path / ".sico/ai/agent/sessions/history/events.jsonl"
        before = path.read_bytes()
        a = RemoteFrontend(service.descriptor, tmp_path, "live")
        b = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            a.open_initial("attach", dict(session_id="live")).result(5)
            preview = a.preview_session("history", 4, 200).result(5)
            assert drain(a, preview.stream)[-1].end == preview.committed_sequence
            assert path.read_bytes() == before and "history" not in service.owner.controllers
            cursor = preview.stream._wire_cursor.record()
            with pytest.raises(ProtocolError):
                b._events.request("batch", dict(cursor=cursor)).result(5)
            preview.stream.close()
            wait_until(lambda: not service.dispatch.frontend.replay._feeds)
            with pytest.raises(ValueError):
                a.open_session("history").result(5)
            assert service.owner.controllers["live"].runtime_id == address.session.runtime_id
        finally:
            a.close_desktop()
            b.close_desktop()


def test_slow_query_does_not_block_events_and_late_page_result_is_retired(tmp_path, monkeypatch):
    from sico.service.workbench_data import WorkbenchSource

    entered, release = threading.Event(), threading.Event()
    original = WorkbenchSource._query_pages

    def slow(source, query):
        entered.set()
        assert release.wait(5)
        return original(source, query)

    with remote_window(tmp_path, monkeypatch) as (service, window):
        monkeypatch.setattr(WorkbenchSource, "_query_pages", slow)
        source = window.centers.source
        query = source.query_pages(WorkbenchQuery(activation=window.page.activation,
                                                  query_id="held"))
        try:
            wait_until(entered.is_set)
            window.api.command(window.page.session, "submit", "while query waits").result(5)
            wait_until(lambda: window.presentation.state.get("task", {}).get("status")
                       == "completed")
            heartbeat(window)
            assert not release.is_set() and not query.done()
            source.close()
            release.set()
            with pytest.raises(ValueError):
                query.result(5)
            assert not window.binding.failed
        finally:
            release.set()


def test_reconnect_rebuilds_original_window_even_after_more_than_a_tail_of_events(
        tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        controller = service.owner.controllers["live"]
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            record_task(controller.journal, controller.current, "prefix", native=True)
            record_task(controller.journal, controller.current, "first")
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(token, 1, 1).result(5)
            original = drain(api, prepared.stream)
            with pytest.raises(ProtocolError):
                api._events.request("invalid", {}).result(5)
            for number in range(220):
                record_task(controller.journal, controller.current, "later_" + str(number))
            controller.journal.append("codex.item", history_item(controller.journal, "prefix"),
                                      controller.loop.state)
            api.reconnect_session(token, prepared.stream)
            fresh = api.prepare_replay(token, 2, 1).result(5)
            rebuilt = drain(api, fresh.stream)
            assert rebuilt[0].start == original[0].start
            events = [event for batch in rebuilt for event in batch.events]
            sequences = [event["sequence"] for event in events]
            assert sequences == list(range(original[0].start, rebuilt[-1].committed + 1))
            assert len([e for e in events if e["kind"] == "task.started"]) == 221
            assert events[-1]["kind"] == "codex.item"
            cursor = fresh.stream._wire_cursor
            # A reconnect may start mid-task at any committed prefix. It must
            # still reject a restart at/past the journal head.
            head = len(controller.journal.records)
            invalid = [replace(cursor, instance_id="foreign"),
                       replace(cursor, sequence=head, committed=head),
                       replace(cursor, sequence=head + 1, committed=head + 1),
                       replace(cursor, activation=3),
                       replace(cursor, address=replace(cursor.address, session=replace(
                           token, runtime_id="f" * 32)))]
            for stale in invalid:
                with pytest.raises((ProtocolError, ValueError)):
                    api._events.request("prepare", dict(session_id="live",
                        runtime_id=token.runtime_id, activation=3, messages=1,
                        readonly=False, resume=stale.record())).result(5)
        finally:
            api.close_desktop()
