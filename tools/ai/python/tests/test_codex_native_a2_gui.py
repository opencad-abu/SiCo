"""A2 media, evidence links and historical origin in the real Qt detail surface."""

import base64

import pytest
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices, QTextDocument
from PyQt5.QtWidgets import QApplication
from test_codex_native_a2 import web
from test_codex_native_a2_runtime import png
from test_codex_native_projection import CONTEXT, begin, emit

from sico.core.links import object_link
from sico.service.detail_content import DetailContent
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex
from sico_ui.transcript import Transcript
from agent_window_fixture import create_window as AssistantWindow
from sico_ui.workbench_view import WorkbenchDetail


@pytest.mark.parametrize("width", [480, 1100])
def test_image_and_web_detail_are_verified_and_passive(tmp_path, monkeypatch, width):
    app = QApplication.instance() or QApplication([])
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or True)
    with SessionJournal(tmp_path, "a2-media") as journal:
        backend = begin(journal)
        image = {"id": "image", "type": "imageGeneration", "status": "completed",
                 "result": base64.b64encode(png()).decode(), "revisedPrompt": "图像证据"}
        emit(backend, "item/completed", item=image)
        emit(backend, "item/completed", item=web())
        backend._finish("completed")
        index = WorkbenchIndex(journal).sync()
        view = WorkbenchDetail()
        view.resize(width, 650)
        view.show()
        try:
            row = next(r for r in index.data.values() if r.get("tool") == "codex_image_generation")
            view.current = ("data", row["id"])
            detail = DetailContent(index).prepare("data", row["id"])
            assert detail["images"] == [png()]
            view._render_detail(0, detail)
            app.processEvents()
            document = view.document.document()
            assert len(view.image_sizes) == 1
            name = next(iter(view.image_sizes))
            resource = document.resource(QTextDocument.ImageResource, QUrl(name))
            assert resource is not None and not resource.isNull()
            assert view.document.horizontalScrollBar().maximum() == 0
            assert view.grab().save(str(tmp_path / "a2-image.png"))
            assert not opened
            search = next(r for r in index.data.values() if r.get("tool") == "codex_web_search")
            detail = DetailContent(index).prepare("data", search["id"])
            view._render_detail(0, detail)
            assert "Evidence <one>" in view.document.toPlainText()
            assert view.open_url("https://example.org/evidence")
            assert opened == ["https://example.org/evidence"]
            assert not view.open_url("file:///etc/passwd")
            assert len(opened) == 1
            assert view.grab().save(str(tmp_path / "a2-web.png"))
        finally:
            view.close()


def test_tail_mainwindow_replays_old_origin_without_counting_current_tools(tmp_path, monkeypatch):
    from agent_gui_wait import receipts, settle

    from sico_ui.session_binding import SessionBinding

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(SessionBinding, "REPLAY_MESSAGES", 1)
    with SessionJournal(tmp_path, "a2-tail") as journal:
        backend = begin(journal)
        backend.native_history.bind("old")
        backend._finish("completed")
        backend._begin("Current task", CONTEXT, "new", "chat")
        backend._finish("completed")
        backend.native_history.capture("old", web(), observed=True)
        transcript = Transcript(read_only=True)
        for event in journal.events():
            transcript.receive(event)
        assert not any(m["role"] == "tools" for m in transcript.messages)
        window = AssistantWindow(backend)
        window.resize(1100, 740)
        window.show()
        try:
            settle(window)
            window.renderer.flush()
            assert "历史补采" in window.display.toPlainText()
            row = next(r for r in WorkbenchIndex(journal).sync().data.values()
                       if r["data_type"] == "codex_history")
            window.details.open_object_link(QUrl(object_link("data", journal.session_id, row["id"])))
            receipts(window)
            app.processEvents()
            assert not window.detail.notice.text()
            assert "原任务" in window.detail.document.toPlainText()
            assert window.grab().save(str(tmp_path / "a2-history.png"))
        finally:
            window.close()
            window.sessions.close()
            assert window.sessions.wait(5)
