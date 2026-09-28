from __future__ import annotations

from pathlib import Path
from threading import Event, get_ident
import time
from cadlsf_selection_fixtures import (
    application as application,
    FakeValidationController,
    SelectorWindow,
    _wait_until,
)
from PyQt5.QtWidgets import QApplication
from cadgui.protocol import TransferDocument, read_transfer
from cadlsf.collector import CollectorCancelled, CollectorConfig, CollectorError
from cadlsf.gui.selection_validation import SelectionValidationController


def test_selector_validates_in_background_before_publish(
    application: QApplication, tmp_path: Path
) -> None:
    output = tmp_path / "auto.tsv"
    validation = FakeValidationController()
    window = SelectorWindow(output, validation)

    window.selector.use_auto_button.click()

    assert validation.requests == [("normal", None)]
    assert not output.exists()
    assert not window.selector.use_auto_button.isEnabled()
    assert not window.selector.use_host_button.isEnabled()
    assert window.statuses[-1][1] == "Validating LSF selection"

    validation.complete()

    assert read_transfer(output) == TransferDocument(
        records=(("QUEUE", "normal"),), status="applied", version="1"
    )
    assert window.close_calls == 1


def test_selector_discards_success_after_host_selection_changes(
    application: QApplication, tmp_path: Path
) -> None:
    output = tmp_path / "host.tsv"
    validation = FakeValidationController()
    window = SelectorWindow(output, validation)
    window.host_table.selectRow(0)
    selected = window.selector.validated_host()

    window.selector.use_host_button.click()
    assert validation.requests == [("normal", selected)]
    window.host_table.selectRow(1)
    validation.complete()

    assert not output.exists()
    assert window.close_calls == 0
    assert window.statuses[-1][0:2] == (
        "stale",
        "Selection changed during validation",
    )


def test_selector_validation_failure_does_not_publish_and_shutdowns(
    application: QApplication, tmp_path: Path
) -> None:
    output = tmp_path / "failed.tsv"
    validation = FakeValidationController()
    window = SelectorWindow(output, validation)

    window.selector.use_auto_button.click()
    validation.reject("Selected queue is no longer open")

    assert not output.exists()
    assert window.statuses[-1] == (
        "error",
        "Cannot validate selection",
        "Selected queue is no longer open",
    )
    assert window.selector.shutdown()
    assert validation.shutdown_calls == 1


def test_validation_controller_runs_collector_off_gui_thread(
    application: QApplication,
) -> None:
    gui_thread = get_ident()
    worker_threads: list[int] = []
    release = Event()

    class FakeCollector:
        def validate_selection(self, queue: str, host: str | None) -> None:
            worker_threads.append(get_ident())
            assert (queue, host) == ("normal", "node-a")
            assert release.wait(2)

    def factory(_config: CollectorConfig, _cancel: Event) -> FakeCollector:
        return FakeCollector()

    controller = SelectionValidationController(
        CollectorConfig(), collector_factory=factory
    )
    received: list[tuple[str, object]] = []
    controller.succeeded.connect(lambda queue, host: received.append((queue, host)))

    started = time.monotonic()
    assert controller.validate("normal", "node-a")
    assert time.monotonic() - started < 0.1
    assert controller.busy
    _wait_until(application, lambda: bool(worker_threads))
    assert worker_threads != [gui_thread]
    release.set()
    _wait_until(application, lambda: bool(received))
    assert received == [("normal", "node-a")]
    assert not controller.busy
    assert controller.shutdown()


def test_validation_controller_sanitizes_unexpected_errors(
    application: QApplication,
) -> None:
    class BrokenCollector:
        def validate_selection(self, _queue: str, _host: str | None) -> None:
            raise RuntimeError("secret command output")

    controller = SelectionValidationController(
        CollectorConfig(),
        collector_factory=lambda _config, _cancel: BrokenCollector(),
    )
    failures: list[str] = []
    controller.failed.connect(failures.append)
    assert controller.validate("normal")
    _wait_until(application, lambda: bool(failures))

    assert failures == ["Unexpected error while validating the LSF selection"]
    assert "secret" not in failures[0]
    assert controller.shutdown()


def test_validation_controller_shutdown_cancels_running_request(
    application: QApplication,
) -> None:
    started = Event()

    class CancellableCollector:
        def __init__(self, cancelled: Event) -> None:
            self.cancelled = cancelled

        def validate_selection(self, _queue: str, _host: str | None) -> None:
            started.set()
            while not self.cancelled.wait(0.005):
                pass
            raise CollectorCancelled("cancelled")

    controller = SelectionValidationController(
        CollectorConfig(),
        collector_factory=lambda _config, cancelled: CancellableCollector(cancelled),
    )
    received: list[object] = []
    controller.succeeded.connect(lambda queue, host: received.append((queue, host)))
    controller.failed.connect(received.append)
    assert controller.validate("normal")
    assert started.wait(1)

    assert controller.shutdown()
    application.processEvents()
    assert not received
    assert not controller.busy


def test_validation_controller_keeps_collector_errors_user_facing(
    application: QApplication,
) -> None:
    class RejectedCollector:
        def validate_selection(self, _queue: str, _host: str | None) -> None:
            raise CollectorError("Selected host is no longer available")

    controller = SelectionValidationController(
        CollectorConfig(),
        collector_factory=lambda _config, _cancel: RejectedCollector(),
    )
    failures: list[str] = []
    controller.failed.connect(failures.append)
    assert controller.validate("normal", "node-a")
    _wait_until(application, lambda: bool(failures))
    assert failures == ["Selected host is no longer available"]
    assert controller.shutdown()
