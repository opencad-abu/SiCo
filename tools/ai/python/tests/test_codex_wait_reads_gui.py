"""The existing review center remains actionable while independent reads finish."""

import pytest
from agent_gui_wait import settle, wait_until
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication, QRadioButton
from test_codex_elicitation_gui import app  # noqa: F401
from test_codex_steering_gui import publish
from wait_reads_fixture import backend, host_question, invoke, project, start

from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from agent_window_fixture import create_window as AssistantWindow


@pytest.mark.parametrize("width", [980, 1500])
def test_independent_read_keeps_question_draft_and_original_answer(tmp_path, app, width):  # noqa: F811
    project(tmp_path)
    with SessionJournal(tmp_path, "wait-window") as journal:
        loop = backend(journal, tmp_path)
        start(loop)
        controller = SessionController(loop)
        controller._running = True
        window = AssistantWindow(controller)
        window.resize(width, 850)
        window.show()
        try:
            settle(window, tasks=False)
            row = host_question(loop)
            publish(window)
            settle(window, tasks=False)
            panel = window.centers.audit
            window.centers.tabs.setCurrentWidget(panel)
            wait_until(lambda: row["id"] in panel.audit_rows)
            panel.list.setCurrentItem(panel.list.topLevelItem(0))
            panel.select(panel.list.currentItem())
            panel.text_edits["pdk_library"].setPlainText("Keep my draft")
            assert invoke(loop, "read_project_document", {"path": "docs/independent.txt"})[
                "status"] == "ok"
            assert invoke(loop, "read_project_document", {"path": "docs/pdk.txt"})[
                "status"] == "waiting_user"
            publish(window)
            settle(window, tasks=False)
            assert panel.text_edits["pdk_library"].toPlainText() == "Keep my draft"
            assert row["status"] == "pending" and panel.answer.isEnabled()
            assert not window.binding.failed and not controller.inbox.pending
            assert window.width() <= width
            QApplication.processEvents()
            assert window.grab().save(str(tmp_path / f"independent-read-waiting-{width}.png"))
            panel.text_edits["pdk_library"].setPlainText("")
            next(button for button in panel.choices.findChildren(QRadioButton)
                 if button.property("choice") == "first").setChecked(True)
            panel.answer.clicked.emit(True)
            wait_until(lambda: row["status"] == "answer_received")
            assert loop.audit.deliver_host()
            assert row["status"] == "answered" and not loop.audit.pending
        finally:
            loop.runtime = None
            loop.steering.end()
            controller._running = False
            window.close()
            assert window.sessions.wait(5)
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
