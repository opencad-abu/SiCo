"""Measured Qt response while a worker indexes and searches large workbench data."""

import json
import threading
import time
import tracemalloc

from agent_gui_wait import receipts
from agent_workbench_query_fixtures import History
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.service.workbench_data import WorkbenchService
from sico_ui.centers import Centers


def p95(values):
    return sorted(values)[int((len(values) - 1) * .95)]


def test_large_query_keeps_qt_input_and_paints_bounded_pages(monkeypatch):
    app = QApplication.instance() or QApplication([])
    history = History().populate(tasks=8, tools=600, reports=80)
    service = WorkbenchService()
    owner, beats, paints, queries = threading.get_ident(), [], [], []
    from sico.service import workbench_data

    original = workbench_data.publish_query

    def queried(*args):
        assert threading.get_ident() != owner
        started = time.perf_counter()
        result = original(*args)
        queries.append((time.perf_counter() - started) * 1000)
        return result

    monkeypatch.setattr(workbench_data, "publish_query", queried)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: beats.append(time.perf_counter()))
    timer.start()
    tracemalloc.start()
    view = Centers(history.reader, service)
    view.resize(800, 600)
    view.show()
    for listing in (view.data, view.reports):
        original_paint = listing.apply_page

        def paint(page, original=original_paint):
            assert threading.get_ident() == owner and len(page["rows"]) <= 200
            started = time.perf_counter()
            original(page)
            paints.append((time.perf_counter() - started) * 1000)

        monkeypatch.setattr(listing, "apply_page", paint)
    try:
        receipts(view)
        for text in ("measure_beta", "read_alpha", "Report", "no match", ""):
            view.data.search.setText(text)
            receipts(view)
        for _ in range(3):
            view.data.page(1)
            receipts(view)
        QTest.qWait(30)
        assert view.data.search.text() == ""
        assert view.data.list.topLevelItemCount() == 200
        assert view.data.total == 5456 and view.reports.total == 640
        _, peak = tracemalloc.get_traced_memory()
        gaps = [(end - start) * 1000 for start, end in zip(beats, beats[1:])]
        metrics = {"events": len(history.events), "data_rows": view.data.total,
                   "report_rows": view.reports.total, "page_limit": 200,
                   "peak_traced_bytes": peak, "queries": len(queries),
                   "query_p95_ms": p95(queries), "query_max_ms": max(queries),
                   "qt_page_p95_ms": p95(paints), "qt_page_max_ms": max(paints),
                   "heartbeat_p95_ms": p95(gaps), "heartbeat_max_ms": max(gaps)}
        print("FB03_SCALE " + json.dumps(metrics, sort_keys=True))
        assert len(beats) >= 10 and p95(gaps) < 60 and max(gaps) < 250
        assert max(paints) < 100 and peak < 48 * 1024 * 1024
    finally:
        timer.stop()
        tracemalloc.stop()
        view.close()
        service.close()
        assert service.wait(5)
        view.deleteLater()
        app.processEvents()
