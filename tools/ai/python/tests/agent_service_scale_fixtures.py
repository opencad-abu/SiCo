"""Independent GUI/service scale setup and existing FB measurement budgets."""

import time
import tracemalloc
from contextlib import ExitStack, contextmanager

from agent_gui_wait import wait_until
from agent_service_gui_fixtures import ServiceTestWindow
from agent_service_matrix_fixtures import client, matrix_service
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from sico.service.frontend_session import FrontendSession
from sico_ui.event_batches import BatchDelivery
from sico_ui.transcript import Transcript


@contextmanager
def process_window(project, **service_options):
    with matrix_service(project, **service_options) as service, ExitStack() as stack:
        api, token = client(stack, service, project)
        api.control.request(token, "acquire").result(5)
        window = ServiceTestWindow(FrontendSession(api, token))
        window.show()
        try:
            wait_until(window.steering_input.available)
            yield service, window
        finally:
            window.request_quit()
            wait_until(lambda: api.wait_desktop(0))
            window.deleteLater()
            QApplication.instance().processEvents()


class Measurements:
    def __init__(self, window, monkeypatch):
        self.spans, self.beats, self.batches = {"apply": [], "render": [], "page": []}, [], []
        def timed(function, key):
            def call(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    self.spans[key].append((time.perf_counter() - started) * 1000)
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
                        batch = value.result()
                        self.batches.append((batch.size, len(batch.events), batch.error))
                    except ValueError:
                        pass
            receipt.add_done_callback(done)
            return receipt
        monkeypatch.setattr(window.api, "event_batch", capture)
        self.timer = QTimer(window)
        self.timer.setInterval(5)
        self.timer.timeout.connect(lambda: self.beats.append(time.perf_counter()))

    def __enter__(self):
        self.timer.start()
        tracemalloc.start()
        return self

    def __exit__(self, *_exc):
        self.timer.stop()
        tracemalloc.stop()

    def check(self, *, memory_mib, heartbeat_p95=60, pages=True):
        from sico.service.event_display import BATCH_BYTES, BATCH_EVENTS

        _, peak = tracemalloc.get_traced_memory()
        gaps = [(b - a) * 1000 for a, b in zip(self.beats, self.beats[1:])]
        assert len(gaps) >= 5
        p95 = sorted(gaps)[int((len(gaps) - 1) * .95)]
        metrics = dict(heartbeat_p95_ms=p95, heartbeat_max_ms=max(gaps), peak_traced_bytes=peak,
            **{key + "_max_ms": max(values) for key, values in self.spans.items() if values})
        assert p95 < heartbeat_p95 and max(gaps) < 250, metrics
        assert peak < memory_mib * 1024 * 1024, metrics
        budgets = (("apply", 50), ("render", 100)) + ((("page", 100),) if pages else ())
        for key, limit in budgets:
            assert self.spans[key] and max(self.spans[key]) < limit, metrics
        assert self.batches and all(size <= BATCH_BYTES and count <= BATCH_EVENTS and not error
                                   for size, count, error in self.batches)
        return metrics
