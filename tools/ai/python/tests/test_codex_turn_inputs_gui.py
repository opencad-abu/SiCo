"""Qt controls, durable drafts and media history use the worker boundary."""

from sico.service.frontend_session import SessionToken

import threading
from concurrent.futures import Future

import pytest
from sico.codex.access_policy import default_access
from agent_gui_wait import settle, wait_until
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel, QListWidget, QPlainTextEdit, QPushButton
from test_codex_steering_gui import app as app
from test_codex_steering_gui import publish
from test_codex_steering_gui import window as window
from test_codex_turn_inputs import PNG, catalog, wav

from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.service.controller import SessionController
from sico.storage.input_assets import prepare_inputs
from sico.storage.journal import SessionJournal
from sico_ui.turn_attachments import AttachmentDialog
from sico_ui.turn_input_detail import InputImage, show_input_detail
from sico_ui.turn_settings import TurnSettingsDialog


def configure(window):
    options = window.controller.loop.turn_options
    window.controller.loop.provider.model = "test-model"
    options.selected = {"model": "test-model", "mode": "default", "serviceTier": "default", "access": default_access()}
    options.catalog = catalog()
    options.catalog["models"][0]["inputModalities"] = ["text", "image"]
    return options


@pytest.mark.parametrize("width", [980, 1500])
def test_input_controls_round_trip_and_model_modalities(window, tmp_path, monkeypatch, width):
    configure(window)
    path = tmp_path / "picture.png"
    path.write_bytes(PNG)
    window.resize(width, 800)
    composer = window.turn_composer
    composer.refresh()
    composer.attachments.click()
    dialog = window.findChild(AttachmentDialog)
    dialog.show()
    try:
        wait_until(dialog.ok.isEnabled)
        audio = dialog.kind.findData("localAudio")
        assert not dialog.kind.model().item(audio).isEnabled()
        monkeypatch.setattr("sico_ui.turn_attachments.ask_file",
                            lambda *_args, **_kwargs: (str(path), True))
        dialog.add_input()
        dialog.rows.setCurrentRow(0)
        dialog.remove_input()
        assert not dialog.inputs
        dialog.add_input()
        assert dialog.grab().save(str(tmp_path / f"turn-attachments-{width}.png"))
        dialog.apply()
        assert window.input.turn_options is None
        composer.effort.setCurrentIndex(composer.effort.findData("novel"))
        composer.settings.click()
        dialog = window.findChild(TurnSettingsDialog)
        wait_until(dialog.ok.isEnabled)
        dialog.mode.setCurrentIndex(dialog.mode.findData("plan"))
        dialog.tier.setCurrentIndex(dialog.tier.findData("fast"))
        dialog.turn_tier.setCurrentIndex(dialog.turn_tier.findData("default"))
        dialog.schema.setPlainText("false")
        assert dialog.grab().save(str(tmp_path / f"turn-settings-{width}.png"))
        dialog.apply()
        assert window.input.turn_options == {
            "model": "test-model", "mode": "plan", "effort": "novel",
            "serviceTier": "fast", "serviceTierForTurn": "default", "outputSchema": False,
            "access": default_access(),
        }
        assert window.input.turn_inputs == [{"type": "localImage", "path": str(path),
                                             "detail": "auto"}]
        window.input.setPlainText("Read the attached image")
        draft = window.input.draft()
        window.input.clear_submission()
        window.input.restore_draft(draft)
        assert window.input.draft() == draft
        composer.refresh()
        assert composer.model.currentData() == "test-model"
        assert composer.effort.currentData() == "novel"
        assert composer.attachments.geometry().right() < composer.model.geometry().left()
        assert composer.model.geometry().right() < composer.effort.geometry().left()
        assert composer.effort.geometry().right() < composer.settings.geometry().left()
        assert composer.settings.geometry().right() < composer.width()
        assert composer.goal.geometry().right() < window.send.geometry().left()
        assert window.grab().save(str(tmp_path / f"turn-input-draft-{width}.png"))
        window.steering_input.submit(True)
        assert window.input.draft() == draft and not window.controller.loop.steering.pending
        window.steering_input.queue_action.trigger()
        settle(window, tasks=False)
        assert not window.input.turn_inputs and window.input.turn_options is None
        queued = window.controller.inbox.pending[-1]["message"]
        assert queued["turn_options"]["outputSchema"] is False
        assert queued["inputs"][0]["asset"]["size"] == len(PNG)
    finally:
        dialog.close()


@pytest.mark.parametrize("boundary", ["closed", "activation", "controller", "target"])
@pytest.mark.parametrize("dialog_type", [AttachmentDialog, TurnSettingsDialog])
def test_late_inventory_cannot_change_a_different_draft(window, monkeypatch, boundary, dialog_type):
    options = configure(window)
    future = Future()
    future.set_running_or_notify_cancel()
    original = window.sessions.worker.command
    monkeypatch.setattr(window.sessions.worker, "command", lambda owner, name, *a, **k:
                        future if name == "turn_input_status" else original(owner, name, *a, **k))
    dialog = dialog_type(window)
    dialog.show()
    before = (window.page.activation, window.page.session, window.presentation.submission_context)
    window.input.turn_inputs = [{"type": "mention", "name": "new", "path": "/new"}]
    expected = window.input.draft()
    try:
        if boundary == "closed":
            dialog.reject()
        elif boundary == "activation":
            window.page.activation += 1
        elif boundary == "controller":
            window.page.session = None
        else:
            window.presentation.submission_context = BoundContext("i", "g", "new", {})
        future.set_result(options.status(busy=True))
        dialog.receipt.poll()
        assert dialog.closed and window.input.draft() == expected
    finally:
        window.page.activation, window.page.session, window.presentation.submission_context = before
        dialog.close()


@pytest.mark.parametrize("outcome", ["edited", "failure", "cancel"])
def test_slow_media_submit_keeps_qt_responsive_and_draft(window, tmp_path, monkeypatch, outcome):
    configure(window)
    from sico.storage import input_assets

    path = tmp_path / "picture.png"
    path.write_bytes(PNG)
    entered, release = threading.Event(), threading.Event()
    original = input_assets.file_bytes
    qt_thread = threading.get_ident()

    def slow(value):
        assert threading.get_ident() != qt_thread
        entered.set()
        assert release.wait(5)
        if outcome == "failure":
            raise ValueError("Input disappeared")
        return original(value)

    monkeypatch.setattr(input_assets, "file_bytes", slow)
    window.input.turn_inputs = [{"type": "localImage", "path": str(path)}]
    window.input.setPlainText("Keep input draft")
    window.steering_input.queue_action.trigger()
    try:
        assert entered.wait(5)
        heartbeat(window)
        if outcome == "edited":
            window.input.turn_inputs = []
            window.input.setPlainText("Edited while storing input")
        if outcome == "cancel":
            window.sessions.worker.command(SessionToken(window.controller.session_id, window.controller.runtime_id), "cancel")
        expected = window.input.draft()
    finally:
        release.set()
    settle(window, tasks=False)
    assert window.input.draft() == expected
    assert len(window.controller.inbox.pending) == (outcome == "edited")


@pytest.mark.parametrize("change", ["inputs", "settings", "cursor"])
def test_submit_receipt_compares_complete_draft_content(window, monkeypatch, change):
    configure(window)
    future = Future()
    future.set_running_or_notify_cancel()
    original = window.sessions.worker.command
    monkeypatch.setattr(window.sessions.worker, "command", lambda owner, name, *a, **k:
                        future if name == "submit" else original(owner, name, *a, **k))
    window.input.setPlainText("Unchanged message")
    window.input.turn_inputs = [{"type": "mention", "name": "first", "path": "/first"}]
    window.input.turn_options = {"model": "test-model", "mode": "default"}
    window.actions.submit(queued=True)
    if change == "inputs":
        window.input.turn_inputs[0]["path"] = "/second"
    elif change == "settings":
        window.input.turn_options["mode"] = "plan"
    else:
        cursor = window.input.textCursor()
        cursor.setPosition(2)
        window.input.setTextCursor(cursor)
    expected = window.input.draft()
    future.set_result({"id": "accepted"})
    settle(window, tasks=False)
    assert not window.submissions
    if change == "cursor":
        assert window.input.draft_content() == ("", None, [], None)
    else:
        assert window.input.draft() == expected


@pytest.mark.parametrize("accepted", [True, False])
def test_media_submit_receipt_after_switch_preserves_other_draft(
        window, tmp_path, monkeypatch, accepted):
    configure(window)
    owner = window.controller
    future = Future()
    future.set_running_or_notify_cancel()
    original = window.sessions.worker.command
    monkeypatch.setattr(window.sessions.worker, "command", lambda source, name, *a, **k:
                        future if name == "submit" else original(source, name, *a, **k))
    window.input.setPlainText("Original media task")
    window.input.turn_inputs = [{"type": "mention", "name": "first", "path": "/first"}]
    window.input.turn_options = {"model": "test-model", "mode": "default"}
    draft = window.input.draft()
    with SessionJournal(tmp_path, "media-other") as journal:
        other = SessionController(CodexBackend(owner.loop.provider, ToolRegistry(), journal,
                                               owner.current))
        window.sessions.controllers[other.session_id] = other
        window.actions.submit(queued=True)
        window.page.activate_session(other.session_id)
        wait_until(lambda: not window.page.opening)
        window.input.restore_draft(draft)
        if accepted:
            future.set_result({"id": "accepted"})
        else:
            future.set_exception(ValueError("Input was not accepted"))
        settle(window, tasks=False)
        assert not window.submissions
        assert window.input.draft() == draft
        window.page.activate_session(owner.session_id)
        settle(window, tasks=False)
        if accepted:
            assert window.input.draft_content() == ("", None, [], None)
        else:
            assert window.input.draft() == draft


@pytest.mark.parametrize("kind", ["image", "mention", "audio", "corrupt"])
def test_input_history_displays_snapshot_and_integrity_failure(window, tmp_path, monkeypatch, kind):
    options = configure(window)
    loop = window.controller.loop
    path = tmp_path / "selected"
    path.write_bytes(b"Project source text" if kind == "mention" else wav()
                     if kind == "audio" else PNG)
    part = ({"type": "mention", "name": "project", "path": str(path)} if kind == "mention"
            else {"type": "localAudio" if kind == "audio" else "localImage", "path": str(path)})
    inputs = prepare_inputs(loop.journal, [part])
    loop.steering.end()
    loop._finish("completed")
    loop._begin("Read selected data", loop.state.context, "media-task", "chat", inputs=inputs,
                turn_options=options.freeze())
    loop._event("codex.turn.settings", {"status": "accepted", "options": options.freeze()})
    publish(window)
    settle(window, tasks=False)
    sequence = [e["sequence"] for e in loop.journal.events() if e["kind"] == "task.started"][-1]
    if kind == "corrupt":
        (loop.journal.root / inputs[0]["asset"]["path"]).write_bytes(b"changed")
    dialog = show_input_detail(window, str(sequence))
    try:
        rows = dialog.findChild(QListWidget)
        text = dialog.findChild(QPlainTextEdit)
        wait_until(lambda: rows.count() == 1)
        assert '"status": "accepted"' in text.toPlainText()
        rows.setCurrentRow(0)
        if kind == "image":
            wait_until(lambda: any(label.pixmap() is not None and not label.pixmap().isNull()
                                   for label in dialog.findChildren(QLabel)))
        elif kind == "audio":
            opened = []
            monkeypatch.setattr("sico_ui.turn_input_detail.QDesktopServices.openUrl",
                                lambda url: opened.append(url.toLocalFile()))
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "打开媒体")
            wait_until(button.isVisible)
            button.click()
            assert opened == [str(loop.journal.root / inputs[0]["asset"]["path"])]
            assert not dialog.findChild(InputImage).isVisible()
        else:
            wait_until(lambda: ("checksum" if kind == "corrupt" else "Project source text")
                       in text.toPlainText())
            assert not dialog.findChild(InputImage).isVisible()
            if kind == "mention":
                assert text.toPlainText().startswith("Project source text\n")
                assert text.verticalScrollBar().value() == 0
        assert dialog.grab().save(str(tmp_path / f"turn-input-history-{kind}.png"))
    finally:
        dialog.close()
