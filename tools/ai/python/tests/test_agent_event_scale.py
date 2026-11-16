"""Synthetic replay scale and scheduler measurements for the FB-02 baseline."""

import json
import statistics
import time
import tracemalloc

from agent_gui_wait import settle, wait_until
from agent_replay_fixtures import record_task
from agent_worker_fixtures import window as window
from PyQt5.QtCore import QTimer

from sico.service.event_display import BATCH_BYTES, BATCH_EVENTS, EVENT_BYTES
from sico.storage.journal import SessionJournal
from sico_ui.transcript import Transcript


def p95(values):
    return sorted(values)[int((len(values) - 1) * .95)]


def test_large_multisession_preview_has_bounded_batches_and_live_qt(window, monkeypatch):
    journal = window.controller.journal
    context = window.controller.current
    # Six historical sessions, approximately 40 MB of event payloads. Use one
    # 8 KiB note per event so validation spans many real sequence boundaries.
    for number in range(6):
        with SessionJournal(journal.root.parents[2], "scale-" + str(number)) as history:
            _, state = record_task(history, context, "task", terminal=False)
            for _ in range(850):
                history.append("session.note", {"text": "x" * 8192}, state)
            state.task["status"] = "completed"
            history.append("task.completed", {}, state)
    window.sessions.catalog.request().result(5)
    calls, renders, batches, beats = [], [], [], []
    publish = window.sessions.worker.event_batch

    def request(stream):
        future = publish(stream)
        future.add_done_callback(lambda result: batches.append(result.result()))
        return future

    monkeypatch.setattr(window.sessions.worker, "event_batch", request)
    from sico_ui.event_batches import BatchDelivery

    apply = BatchDelivery.apply

    def measured(self, *args):
        started = time.perf_counter()
        try:
            return apply(self, *args)
        finally:
            calls.append((time.perf_counter() - started) * 1000)

    monkeypatch.setattr(BatchDelivery, "apply", measured)
    render = Transcript.render

    def measured_render(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return render(self, *args, **kwargs)
        finally:
            renders.append((time.perf_counter() - started) * 1000)

    monkeypatch.setattr(Transcript, "render", measured_render)
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    tracemalloc.start()
    started = time.perf_counter()
    window.page.preview_session("scale-0")
    wait_until(lambda: not window.page.opening and not window.presentation.replay.dirty, timeout=15)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    timer.stop()
    assert window.page.reviewing == "scale-0"
    assert len(beats) >= 5
    assert batches and all(not batch.error for batch in batches)
    assert all(batch.size <= BATCH_BYTES and len(batch.events) <= BATCH_EVENTS for batch in batches)
    gaps = [(end - start) * 1000 for start, end in zip(beats, beats[1:])]

    metrics = {"sessions": 6, "events_per_session": 852, "payload_bytes": 6 * 850 * 8192,
               "event_limit": EVENT_BYTES, "batch_limit": BATCH_BYTES,
               "elapsed_seconds": elapsed, "peak_traced_bytes": peak,
               "qt_apply_p95_ms": p95(calls), "qt_apply_max_ms": max(calls),
               "qt_render_p95_ms": p95(renders), "qt_render_max_ms": max(renders),
               "heartbeat_p95_ms": p95(gaps), "heartbeat_max_ms": max(gaps),
               "heartbeat_median_ms": statistics.median(gaps)}
    print("FB02_SCALE " + json.dumps(metrics, sort_keys=True))
    assert p95(gaps) < 50 and max(gaps) < 250
    assert max(calls) < 50 and max(renders) < 100
    assert peak < 24 * 1024 * 1024
    window.page.return_from_preview()
    settle(window)
    assert window.input.isEnabled() and window.page.reviewing is None


def test_visible_markdown_long_reply_and_task_rows_yield_between_paints(window, monkeypatch):
    from agent_worker_fixtures import heartbeat

    from sico.service.state_publication import state_view
    from sico_ui.event_batches import BatchDelivery
    from sico_ui.tasks import TaskPanel

    window.show()
    render, paint, poll = Transcript.render, TaskPanel._paint_rows, window.binding.poll
    spans = {"render": [], "poll": [], "tasks": []}

    def measured(operation, kind):
        def run(*args, **kwargs):
            begin = time.perf_counter()
            try:
                return operation(*args, **kwargs)
            finally:
                spans[kind].append((time.perf_counter() - begin) * 1000)
        return run

    monkeypatch.setattr(Transcript, "render", measured(render, "render"))
    monkeypatch.setattr(TaskPanel, "_paint_rows", measured(paint, "tasks"))
    monkeypatch.setattr(window.binding, "poll", measured(poll, "poll"))
    beats = []
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    controller = window.controller
    context = controller.current
    transcript = window.presentation.transcript
    for number in range(100):
        transcript.append("assistant", "## Reply " + str(number) + "\n\n"
                          + "- **Measured** `value` in the circuit.\n" * 12)
    transcript.append("assistant", "# Long reply\n" + "body " * 60000)
    snapshot = controller.updates.read_updates(limit=1000)["snapshot"]
    snapshot["requests"] = [{"id": str(i), "text": "queued " + str(i), "status": "queued",
                             "context": context.record()} for i in range(200)]
    state, contexts = state_view(snapshot)
    window.renderer.update_session(state, contexts=contexts)
    assert window.task_panel._paint_rows_pending
    # Input remains usable while both widgets have pending work.
    window.input.setPlainText("draft survives replay")
    window.renderer.flush()
    settle(window, timeout=15)
    wait_until(lambda: not window.task_panel._paint_rows_pending)
    heartbeat(window)
    timer.stop()
    assert window.input.toPlainText() == "draft survives replay"
    assert "Long reply" in window.display.toPlainText()
    assert len(window.presentation.transcript.messages) <= Transcript.RETAIN_MESSAGES
    assert len(window.display.toPlainText()) < 70000
    assert len(spans["render"]) > 5 and len(beats) > 5
    gaps = [(end - start) * 1000 for start, end in zip(beats, beats[1:])]
    metrics = {key + "_p95_ms": p95(values) for key, values in spans.items()}
    metrics.update({key + "_max_ms": max(values) for key, values in spans.items()})
    metrics.update(heartbeat_p95_ms=p95(gaps), heartbeat_max_ms=max(gaps),
                   event_apply_budget_ms=BatchDelivery.BUDGET * 1000,
                   render_budget_ms=Transcript.RENDER_BUDGET * 1000)
    print("FB02_VISIBLE " + json.dumps(metrics, sort_keys=True))
    assert p95(gaps) < 60 and max(gaps) < 250
    assert max(spans["render"]) < 100 and max(spans["poll"]) < 150
