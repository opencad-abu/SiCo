"""Independent draft editors preserve other controls and reject stale inventories."""

from concurrent.futures import Future

import pytest
from agent_gui_wait import wait_until
from PyQt5.QtWidgets import QComboBox, QListWidget
from sico_ui.turn_attachments import AttachmentDialog
from sico_ui.turn_settings import TurnSettingsDialog
from test_codex_steering_gui import app as app
from test_codex_steering_gui import window as window
from test_codex_turn_inputs_gui import configure


def test_model_change_drops_only_unsupported_controls_and_restores_draft(window):
    controls = configure(window)
    controls.catalog["models"].append({
        "model": "small-model", "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
        "serviceTiers": [], "inputModalities": ["text"],
    })
    composer = window.turn_composer
    composer.use_catalog(controls.status(busy=True))
    inputs = [{"type": "mention", "name": "file", "path": "/file"}]
    window.input.turn_inputs = inputs
    window.input.turn_options = {
        "model": "test-model", "effort": "novel", "mode": "plan", "serviceTier": "fast",
        "serviceTierForTurn": "fast", "outputSchema": False,
    }
    composer.refresh()
    draft = window.input.draft()
    composer.model.setCurrentIndex(composer.model.findData("small-model"))
    assert composer.effort.itemText(composer.effort.findData("low")) == "低"
    assert window.input.turn_options == {
        "model": "small-model", "mode": "plan", "serviceTier": "default", "outputSchema": False,
    }
    assert composer.effort.findData("novel") == -1
    composer.effort.setCurrentIndex(composer.effort.findData("low"))
    assert window.input.turn_options["effort"] == "low"
    assert window.input.turn_inputs == inputs
    window.input.restore_draft(draft)
    composer.refresh()
    assert composer.model.currentData() == "test-model"
    assert composer.effort.currentData() == "novel"
    window.input.clear_submission()
    composer.refresh()
    assert composer.model.currentData() == "test-model"
    assert composer.effort.currentData() is None
    assert window.input.turn_options is None


def test_attachment_apply_and_cancel_leave_settings_untouched(window):
    configure(window)
    settings = {"model": "test-model", "mode": "plan", "effort": "novel", "outputSchema": False}
    window.input.turn_options = settings
    dialog = AttachmentDialog(window)
    dialog.show()
    try:
        wait_until(dialog.ok.isEnabled)
        assert len(dialog.findChildren(QComboBox)) == 2
        assert not hasattr(dialog, "schema")
        dialog.inputs.append({"type": "mention", "name": "file", "path": "/file"})
        dialog.reject()
        assert window.input.turn_inputs == []
        assert window.input.turn_options == settings
    finally:
        dialog.close()
    dialog = AttachmentDialog(window)
    try:
        wait_until(dialog.ok.isEnabled)
        dialog.inputs.append({"type": "mention", "name": "file", "path": "/file"})
        dialog.apply()
        assert len(window.input.turn_inputs) == 1
        assert window.input.turn_options == settings
    finally:
        dialog.close()


def test_settings_validation_and_cancel_keep_attachment_draft(window):
    configure(window)
    inputs = [{"type": "mention", "name": "file", "path": "/file"}]
    window.input.turn_inputs = inputs
    dialog = TurnSettingsDialog(window)
    dialog.show()
    try:
        wait_until(dialog.ok.isEnabled)
        assert not dialog.findChildren(QListWidget)
        assert len(dialog.findChildren(QComboBox)) == 4
        dialog.schema.setPlainText("invalid JSON")
        dialog.apply()
        assert not dialog.closed
        assert window.input.turn_options is None and window.input.turn_inputs == inputs
        dialog.mode.setCurrentIndex(dialog.mode.findData("plan"))
        dialog.reject()
        assert window.input.turn_options is None and window.input.turn_inputs == inputs
    finally:
        dialog.close()


def test_turn_mode_and_service_default_labels_are_localized_but_payloads_stay_native(window):
    options = configure(window)
    options.selected["mode"] = "plan"
    window.input.turn_options = {"model": "test-model", "mode": "plan"}
    dialog = TurnSettingsDialog(window)
    dialog.show()
    try:
        wait_until(dialog.ok.isEnabled)
        assert dialog.mode.currentData() == "plan"
        assert dialog.mode.currentText() == "计划"
        for combo in (dialog.mode, dialog.tier, dialog.turn_tier):
            default = combo.findData("default")
            assert default >= 0
            assert combo.itemText(default) == "默认"
        dialog.mode.setCurrentIndex(dialog.mode.findData("default"))
        dialog.apply()
        assert window.input.turn_options["mode"] == "default"
    finally:
        dialog.close()


@pytest.mark.parametrize("boundary", ["activation", "target", "review", "closing"])
def test_toolbar_inventory_cannot_cross_its_session_scope(window, monkeypatch, boundary):
    from sico.core.contracts import BoundContext

    options = configure(window)
    composer = window.turn_composer
    pending = Future()
    pending.set_running_or_notify_cancel()
    monkeypatch.setattr(window.api, "command", lambda *a, **k: pending)
    composer.receipt.clear()
    composer.catalog = None
    composer.refresh()
    composer.load_catalog()
    before = (window.page.activation, window.presentation.submission_context,
              window.page.reviewing, window.lifecycle.closing)
    expected = window.input.draft()
    try:
        if boundary == "activation":
            window.page.activation += 1
        elif boundary == "target":
            window.presentation.submission_context = BoundContext("i", "g", "new", {})
        elif boundary == "review":
            window.page.reviewing = "archived-session"
        else:
            window.lifecycle.closing = True
        pending.set_result(options.status(busy=True))
        composer.receipt.poll()
        assert composer.catalog is None
        assert window.input.draft() == expected
    finally:
        (window.page.activation, window.presentation.submission_context,
         window.page.reviewing, window.lifecycle.closing) = before


def test_access_control_position_draft_and_network_scope(window, tmp_path):
    options = configure(window)
    composer = window.turn_composer
    composer.use_catalog(options.status(busy=True))
    window.resize(980, 800)
    window.layout().activate()
    assert composer.layout().itemAt(0).widget() is composer.attachments
    assert composer.layout().itemAt(1).widget() is composer.access
    assert composer.access.currentData() == "workspace-write"
    composer.access.setCurrentIndex(composer.access.findData("read-only"))
    saved = window.input.draft()
    assert window.input.turn_options["access"]["sandbox"] == "read-only"
    assert options.selected["access"]["sandbox"] == "workspace-write"
    composer.access.setCurrentIndex(composer.access.findData("danger-full-access"))
    dialog = TurnSettingsDialog(window)
    try:
        wait_until(dialog.ok.isEnabled)
        assert dialog.network.isChecked() and not dialog.network.isEnabled()
        dialog.reject()
    finally:
        dialog.close()
    window.input.restore_draft(saved)
    composer.refresh()
    assert composer.access.currentData() == "read-only"
    window.grab().save(str(tmp_path / "access-composer.png"))
