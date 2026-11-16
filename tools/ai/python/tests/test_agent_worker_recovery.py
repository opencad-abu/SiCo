"""Command failures are not authorization to release desktop sessions."""

import threading
from types import SimpleNamespace

import pytest

from sico.service.worker import WorkerApi


@pytest.mark.parametrize("fault", ["operation", "result_callback", "error_callback", "catalog"])
def test_command_fault_keeps_worker_and_sessions_until_explicit_close(fault, caplog):
    entered, release = threading.Event(), threading.Event()
    closed = []

    def catalog():
        if fault == "catalog":
            raise ValueError("catalog temporarily unreadable")

    sessions = SimpleNamespace(
        catalog=SimpleNamespace(request=catalog, close=lambda: None), controllers={},
        _close_controllers=lambda: closed.append(True),
    )
    worker = WorkerApi(sessions)

    def operation():
        entered.set()
        assert release.wait(3)
        if fault in {"operation", "error_callback"}:
            raise SystemExit("command only")
        return 42

    def callback(_future):
        raise SystemExit("callback only")

    try:
        future = worker._request(operation)
        assert entered.wait(2)
        if "callback" in fault:
            future.add_done_callback(callback)
        assert not future.cancel()  # A running Future cannot enter the alleged cancel race.
        release.set()
        if fault in {"operation", "error_callback"}:
            with pytest.raises(SystemExit):
                future.result(2)
        else:
            assert future.result(2) == 42
        assert worker._request(lambda: "still usable").result(2) == "still usable"
        assert not closed
    finally:
        release.set()
        worker.close()
        assert worker.wait(3)
    assert closed == [True]
