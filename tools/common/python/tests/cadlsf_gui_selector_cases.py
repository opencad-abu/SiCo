"""LSF GUI selector behavior cases."""

from __future__ import annotations

from cadlsf_gui_fixtures import application as application, _snapshot
from pathlib import Path
from PyQt5.QtWidgets import QApplication
from cadgui.protocol import TransferDocument, read_transfer
from cadlsf.gui.main_window import LsfLoadMonitorWindow
from cadlsf_gui_fakes import FakeController, FakeValidationController


def test_selector_publishes_only_latest_verified_auto_or_host(
    application: QApplication, tmp_path: Path
) -> None:
    auto_output = tmp_path / "auto.tsv"
    auto_controller = FakeController()
    auto_validation = FakeValidationController()
    auto = LsfLoadMonitorWindow(
        controller=auto_controller,
        validation_controller=auto_validation,
        initial_queue="normal",
        output=auto_output,
        auto_start=False,
    )
    assert not auto.selector.use_auto_button.isEnabled()
    auto_controller.busy = False
    auto._apply_snapshot(_snapshot())
    assert auto.selector.use_auto_button.isEnabled()
    auto.selector.use_auto_button.click()
    application.processEvents()
    assert read_transfer(auto_output) == TransferDocument(
        records=(("QUEUE", "normal"),), status="applied", version="1"
    )
    assert auto_validation.requests == [("normal", None)]

    host_output = tmp_path / "host.tsv"
    host_controller = FakeController()
    host_validation = FakeValidationController()
    host = LsfLoadMonitorWindow(
        controller=host_controller,
        validation_controller=host_validation,
        initial_queue="normal",
        output=host_output,
        auto_start=False,
    )
    host_controller.busy = False
    host._apply_snapshot(_snapshot())
    host.resources.host_table.selectRow(0)
    application.processEvents()
    assert host.selector.use_host_button.isEnabled()
    selected = host.selector.validated_host()
    host.selector.use_host_button.click()
    application.processEvents()
    assert read_transfer(host_output) == TransferDocument(
        records=(("QUEUE", "normal"), ("HOST", selected)),
        status="applied",
        version="1",
    )
    assert host_validation.requests == [("normal", selected)]


def test_selector_cancel_and_stale_snapshot_do_not_publish(
    application: QApplication, tmp_path: Path
) -> None:
    cancel_output = tmp_path / "cancel.tsv"
    cancel = LsfLoadMonitorWindow(
        output=cancel_output,
        validation_controller=FakeValidationController(),
        auto_start=False,
    )
    cancel.selector.cancel_button.click()
    application.processEvents()
    assert not cancel_output.exists()

    stale_output = tmp_path / "stale.tsv"
    controller = FakeController()
    stale = LsfLoadMonitorWindow(
        controller=controller,
        validation_controller=FakeValidationController(),
        output=stale_output,
        auto_start=False,
    )
    controller.busy = False
    stale._apply_snapshot(_snapshot())
    controller.busy = True
    controller.busyChanged.emit(True)
    assert not stale.selector.use_auto_button.isEnabled()
    stale.selector.use_auto()
    assert not stale_output.exists()
    controller.busy = False
    controller.failed.emit("latest refresh failed")
    assert not stale.selector.use_auto_button.isEnabled()
    stale.selector.use_auto()
    assert not stale_output.exists()
    stale.close()
    application.processEvents()


def test_selector_rejects_selection_changed_during_live_validation(
    application: QApplication, tmp_path: Path
) -> None:
    output = tmp_path / "changed.tsv"
    controller = FakeController()
    validation = FakeValidationController(auto_complete=False)
    window = LsfLoadMonitorWindow(
        controller=controller,
        validation_controller=validation,
        initial_queue="normal",
        output=output,
        auto_start=False,
    )
    controller.busy = False
    window._apply_snapshot(_snapshot())
    window.selector.use_auto()
    assert validation.requests == [("normal", None)]

    window.resources.queue_combo.setCurrentText("batch")
    validation.complete()
    application.processEvents()

    assert not output.exists()
    assert window.status.state_label.text() == "Selection changed during validation"
    window.close()
    application.processEvents()
