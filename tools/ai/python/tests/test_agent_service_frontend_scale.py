"""Remote history paging retains existing paint, heartbeat and publication budgets."""

import json
import time

from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from agent_workbench_query_fixtures import History
from PyQt5.QtCore import QTimer

from sico.core.contracts import RunState
from sico.service.event_display import BATCH_BYTES, BATCH_EVENTS
from sico.storage.journal import SessionJournal
from sico_ui.event_batches import BatchDelivery
from sico_ui.transcript import Transcript


def test_remote_large_preview_pages_and_return_to_live(tmp_path, monkeypatch):
    history = History("scale").populate(tasks=8, tools=300, reports=30)
    with SessionJournal(tmp_path, "scale") as journal:
        for event in history.events:
            journal.append(event["kind"], event["payload"], RunState(history.context,
                task=dict(id=event["task_id"], status="executing")))
    with remote_window(tmp_path, monkeypatch) as (service, window):
        spans, beats, batches = {"apply": [], "render": [], "page": []}, [], []

        def timed(function, key):
            def call(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    spans[key].append((time.perf_counter() - started) * 1000)
            return call

        monkeypatch.setattr(BatchDelivery, "apply", timed(BatchDelivery.apply, "apply"))
        monkeypatch.setattr(Transcript, "render", timed(Transcript.render, "render"))
        for listing in (window.centers.data, window.centers.reports):
            monkeypatch.setattr(listing, "apply_page", timed(listing.apply_page, "page"))
        request = window.api.event_batch

        def capture(feed):
            receipt = request(feed)
            def done(value):
                if not value.cancelled():
                    try:
                        batches.append(value.result())
                    except ValueError:
                        pass
            receipt.add_done_callback(done)
            return receipt

        monkeypatch.setattr(window.api, "event_batch", capture)
        timer = QTimer(window)
        timer.setInterval(5)
        timer.timeout.connect(lambda: beats.append(time.perf_counter()))
        timer.start()
        try:
            started = time.perf_counter()
            window.page.preview_session("scale")
            wait_until(lambda: not window.page.opening and window.centers.data.total > 0,
                       timeout=30)
            assert window.page.reviewing == "scale" and not window.input.isEnabled()
            assert window.centers.data.list.topLevelItemCount() <= 200
            window.centers.data.search.setText("measure_beta")
            wait_until(lambda: window.centers.data.total == 800, timeout=20)
            window.centers.data.page(1)
            wait_until(lambda: window.centers.index.pages["data"]["offset"] == 200, timeout=10)
            assert window.centers.data.list.topLevelItemCount() == 200
            window.page.return_from_preview()
            wait_until(lambda: not window.page.opening and window.input.isEnabled(), timeout=10)
            gaps = [(b - a) * 1000 for a, b in zip(beats, beats[1:])]
            p95 = sorted(gaps)[int((len(gaps) - 1) * .95)]
            metrics = dict(events=len(history.events), heartbeat_p95_ms=p95,
                heartbeat_max_ms=max(gaps), elapsed_seconds=time.perf_counter() - started,
                **{key + "_max_ms": max(values) for key, values in spans.items()})
            print("AS04_SCALE " + json.dumps(metrics, sort_keys=True))
            assert p95 < 60 and max(gaps) < 250
            assert max(spans["apply"]) < 50 and max(spans["render"]) < 100
            assert max(spans["page"]) < 100
            assert batches and all(b.size <= BATCH_BYTES and len(b.events) <= BATCH_EVENTS
                                   for b in batches)
            wait_until(lambda: len(service.dispatch.frontend.replay._feeds) == 1)
        finally:
            timer.stop()
