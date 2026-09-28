from __future__ import annotations

import os
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dspf_gui_test_support import application, wait_until
from rcepy.dspf.indexer import IndexCancelled, IndexProgress
from rcepy.dspf_gui.workers import IndexController, IndexWorker, QueryController


def test_index_worker_reports_progress_and_result() -> None:
    application()
    result = object()
    progress = []
    completed = []

    def build(_source, **kwargs):
        kwargs["progress"](IndexProgress(5, 10, 2, 3, 1))
        assert kwargs["cancelled"]() is False
        return result

    worker = IndexWorker("input.dspf", build=build)
    worker.signals.progress.connect(progress.append)
    worker.signals.completed.connect(completed.append)
    worker.run()
    assert progress[0].bytes_read == 5
    assert completed == [result]


def test_index_worker_can_cancel_before_start() -> None:
    application()
    called = []
    cancelled = []
    worker = IndexWorker("input.dspf", build=lambda *_args, **_kwargs: called.append(True))
    worker.signals.cancelled.connect(lambda: cancelled.append(True))
    worker.cancel()
    worker.run()
    assert called == []
    assert cancelled == [True]


def test_index_controller_cancels_running_build(monkeypatch) -> None:
    import rcepy.dspf.indexer as indexer

    application()
    entered = Event()

    def build(_source, **kwargs):
        entered.set()
        while not kwargs["cancelled"]():
            Event().wait(0.005)
        raise IndexCancelled("cancelled")

    monkeypatch.setattr(indexer, "build_index", build)
    cancelled = []
    controller = IndexController()
    controller.cancelled.connect(lambda: cancelled.append(True))
    controller.start("input.dspf", cache_dir=None, force=False)
    assert wait_until(entered.is_set)
    controller.cancel()
    assert wait_until(lambda: cancelled == [True])
    assert controller.busy is False
    controller.shutdown()


def test_query_controller_discards_stale_selection_result() -> None:
    application()
    started, release = Event(), Event()
    results = []
    controller = QueryController()
    controller.resultReady.connect(results.append)

    def old_query():
        started.set()
        release.wait(2)
        return "old"

    controller.submit(old_query)
    assert wait_until(started.is_set)
    controller.submit(lambda: "new")
    release.set()
    assert wait_until(lambda: results == ["new"])
    controller.shutdown()
