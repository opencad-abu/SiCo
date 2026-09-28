"""Asynchronous selector publication through a narrow value-query boundary."""

from dataclasses import replace

import pytest

from cadlsf_gui_fixtures import application as application
from cadlsf_gui_fakes import FakeValidationController
from PyQt5.QtWidgets import QVBoxLayout, QWidget
from cadgui.protocol import read_transfer
from cadlsf.gui.selection_state import SelectionState
from cadlsf.gui.selector import SelectorActions


@pytest.fixture
def selector_harness(application, tmp_path):
    state = [SelectionState("normal", "node-a", True, True, False)]
    statuses, closed = [], []
    validator = FakeValidationController(auto_complete=False)
    output = tmp_path / "selected.tsv"

    def show_status(kind, label, detail):
        statuses.append((kind, label, detail))

    selector = SelectorActions(
        output,
        read_selection=lambda: state[0],
        show_status=show_status,
        close=lambda: closed.append(True),
        validation_controller=validator,
    )
    widget = QWidget()
    selector.add_to_layout(QVBoxLayout(widget), widget)
    selector.connect()
    yield selector, state, validator, output, statuses, closed
    selector.shutdown()
    widget.close()


@pytest.mark.parametrize("host_required", [False, True])
def test_selector_publishes_without_window_or_models(selector_harness, host_required):
    selector, state, validator, output, statuses, closed = selector_harness
    selector.publish("node-a" if host_required else None)
    assert not output.exists()
    assert statuses[-1][:2] == ("refreshing", "Validating LSF selection")
    validator.complete()
    expected = (("QUEUE", "normal"),)
    if host_required:
        expected += (("HOST", "node-a"),)
    assert read_transfer(output).records == expected
    assert closed == [True]


@pytest.mark.parametrize("late_result", ["success", "failure"])
def test_closed_selector_ignores_late_validation(selector_harness, late_result):
    selector, state, validator, output, statuses, closed = selector_harness
    selector.use_auto()
    state[0] = replace(state[0], closing=True)
    selector.shutdown()
    count = len(statuses)
    if late_result == "success":
        validator.complete()
    else:
        validator.failed.emit("late failure")
    assert not output.exists()
    assert len(statuses) == count
    assert not closed


def test_selector_refuses_overwrite(selector_harness):
    selector, state, validator, output, statuses, closed = selector_harness
    output.write_text("existing selection", encoding="utf-8")
    selector.use_auto()
    validator.complete()
    assert output.read_text() == "existing selection"
    assert statuses[-1][:2] == ("error", "Cannot publish selection")
    assert not closed


def test_invalid_validation_host_is_rejected(selector_harness):
    selector, state, validator, output, statuses, closed = selector_harness
    selector.use_auto()
    validator.succeeded.emit("normal", object())
    assert not output.exists()
    assert statuses[-1] == (
        "error",
        "Cannot validate selection",
        "Invalid validation result",
    )
    assert not closed
