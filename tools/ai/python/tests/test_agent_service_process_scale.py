"""Real service processes retain FB replay/query memory, heartbeat and paint limits."""

import json
import shutil
from contextlib import ExitStack

from agent_gui_wait import wait_until
from agent_replay_fixtures import record_task
from agent_service_matrix_fixtures import client, matrix_service
from agent_service_scale_fixtures import Measurements, process_window
from agent_workbench_query_fixtures import History

from sico.core.contracts import BoundContext, RunState
from sico.storage.journal import SessionJournal


def test_six_large_histories_switch_and_return_to_live_with_fb_budgets(tmp_path, monkeypatch):
    context = BoundContext("history", "captured", "target", {})
    for number in range(6):
        with SessionJournal(tmp_path, "scale-" + str(number)) as history:
            _, state = record_task(history, context, "task", terminal=False)
            for _ in range(850):
                history.append("session.note", {"text": "x" * 8192}, state)
            state.task["status"] = "completed"
            history.append("task.completed", {}, state)
    with process_window(tmp_path) as (service, window), Measurements(window, monkeypatch) as meter:
        failures = []
        window.page.failed.connect(failures.append)
        for number in range(6):
            window.page.preview_session("scale-" + str(number))
            wait_until(lambda: failures or (not window.page.opening
                       and window.presentation.replay is not None
                       and not window.presentation.replay.dirty), timeout=20)
            assert not failures, failures
            assert window.page.reviewing == "scale-" + str(number)
            assert not window.input.isEnabled()
        window.page.return_from_preview()
        wait_until(window.steering_input.available, timeout=10)
        window.input.setPlainText("draft survives independent history service")
        metrics = meter.check(memory_mib=24, heartbeat_p95=50, pages=False)
        assert window.page.reviewing is None and service.process.is_alive()
        print("AS07_REPLAY " + json.dumps(dict(metrics, sessions=6, events_per_session=852,
                                              payload_bytes=6 * 850 * 8192), sort_keys=True))


def test_large_remote_queries_keep_client_memory_and_paint_bounded(tmp_path, monkeypatch):
    history = History("query").populate(tasks=8, tools=600, reports=80)
    count = len(history.events)
    with SessionJournal(tmp_path, "query") as journal:
        for event in history.events:
            journal.append(event["kind"], event["payload"], RunState(history.context,
                task=dict(id=event["task_id"], status="executing")))
    del history
    with process_window(tmp_path) as (service, window), Measurements(window, monkeypatch) as meter:
        window.page.preview_session("query")
        # Replay preparation and the first bounded query are separate operations.
        wait_until(lambda: not window.page.opening, timeout=30)
        wait_until(lambda: not window.page.opening and window.centers.data.total == 5456,
                   timeout=30)
        assert window.centers.reports.total == 640
        for term, total in (("measure_beta", 1600), ("no match", 0), ("", 5456)):
            window.centers.data.search.setText(term)
            wait_until(lambda: window.centers.data.total == total, timeout=15)
        for offset in (200, 400, 600):
            window.centers.data.page(1)
            wait_until(lambda: window.centers.index.pages["data"]["offset"] == offset)
            assert window.centers.data.list.topLevelItemCount() == 200
        window.page.return_from_preview()
        wait_until(window.steering_input.available, timeout=10)
        assert service.process.is_alive()
        metrics = meter.check(memory_mib=48)
        print("AS07_QUERY " + json.dumps(dict(metrics, events=count, data_rows=5456,
                                             report_rows=640, page_limit=200), sort_keys=True))


def test_partial_catalog_cannot_retire_preview_but_complete_scan_detects_deletion(tmp_path):
    from agent_worker_fixtures import heartbeat

    context = BoundContext("history", "captured", "target", {})
    for name in ("older", "newer"):
        with SessionJournal(tmp_path, name) as journal:
            record_task(journal, context, name)
    with process_window(tmp_path, pause_catalog=True) as (service, window):
        assert service.entered.is_set() and not service.release.is_set()
        wait_until(lambda: window.navigation.catalog_version > 0)
        assert window.navigation.catalog_snapshot["complete"] is False
        assert "older" not in window.navigation.catalog_snapshot["availability"]
        window.page.preview_session("older")
        wait_until(lambda: not window.page.opening and window.presentation.replay is not None)
        heartbeat(window)
        window.navigation.refresh()
        assert window.page.reviewing == "older" and not window.input.isEnabled()
        assert not service.release.is_set()
        service.release.set()
        wait_until(lambda: window.navigation.catalog_snapshot["complete"], timeout=10)
        assert window.page.reviewing == "older"
        shutil.rmtree(tmp_path / ".sico/ai/agent/sessions/older")
        wait_until(lambda: window.page.reviewing is None, timeout=10)
        wait_until(window.steering_input.available)


def test_repeated_subscribe_close_and_disconnect_release_server_capacity(tmp_path):
    with matrix_service(tmp_path) as service, ExitStack() as stack:
        api, token = client(stack, service, tmp_path)
        # Exceed the server's 64-live-subscription capacity without accessing its registry.
        for activation in range(1, 73):
            prepared = api.prepare_replay(token, activation, 20).result(5)
            prepared.stream.close()
        observer, other = client(stack, service, tmp_path, create=False)
        feed = observer.prepare_replay(other, 1, 20).result(5)
        batch = observer.event_batch(feed.stream)
        assert not batch.result(5).error
        feed.stream.received(batch)
        assert service.process.is_alive()
