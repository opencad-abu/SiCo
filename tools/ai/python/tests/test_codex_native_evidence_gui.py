"""Qt evidence links and native counters share the existing journal replay path."""

from __future__ import annotations

import os

import pytest
from test_codex_native_projection import begin, command, emit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QApplication, QTextBrowser

from sico.core.links import object_link
from sico.service.detail_content import DetailContent
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex
from sico_ui.transcript import Transcript
from agent_window_fixture import create_window as AssistantWindow
from sico_ui.workbench_view import WorkbenchDetail


@pytest.mark.parametrize("width", [480, 1100])
def test_native_transcript_and_detail_replay(tmp_path, width):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "native-gui") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None))
        backend._event("model.delta", {"text": "检查文件"})
        emit(backend, "item/completed", item=command(aggregatedOutput="literal <output>\nready"))
        for status in ("pending", "inProgress", "completed"):
            emit(backend, "turn/plan/updated", plan=[{"step": "核对工程文件", "status": status}])
        emit(backend, "turn/diff/updated", diff="--- before\n+++ after\n+changed")
        backend._finish("completed")
        transcript = Transcript(read_only=True)
        for event in journal.events():
            transcript.receive(event)
        group = next(m for m in transcript.messages if m["role"] == "tools")
        assert group["counts"]["total"] == group["counts"]["ok"] == 1
        assert group["counts"]["unconfirmed"] == 0
        assert len([m for m in transcript.messages if m["role"] == "native"]) == 2
        view = QTextBrowser()
        view.setOpenLinks(False)
        view.resize(width, 550)
        view.show()
        transcript.render(view, force=True)
        app.processEvents()
        assert "执行计划" in view.toPlainText() and "文件差异" in view.toPlainText()
        assert view.horizontalScrollBar().maximum() == 0
        assert view.grab().save(str(tmp_path / "native-transcript.png"))
        index = WorkbenchIndex(journal).sync()
        row = next(r for r in index.data.values() if r.get("tool") == "codex_command")
        prepared = DetailContent(index).prepare("data", row["id"])
        view.setHtml(prepared["html"])
        app.processEvents()
        # 原生输出走结构化字段，文档区只留字段表与命令。
        assert prepared["output"] == "literal <output>\nready"
        assert not prepared["output_truncated"]
        assert "literal <output>" not in view.toPlainText()
        assert "退出码" in view.toPlainText() and "printf evidence" in view.toPlainText()
        assert view.horizontalScrollBar().maximum() == 0
        assert view.grab().save(str(tmp_path / "native-command-detail.png"))
        detail = WorkbenchDetail()
        detail.resize(width, 550)
        detail.show()
        detail.current = ("data", row["id"])
        detail._render_detail(0, prepared)
        app.processEvents()
        assert detail.output_area.isVisible()
        assert detail.output.toPlainText() == "literal <output>\nready"
        detail.close()
        diff = next(r for r in index.data.values() if r["data_type"] == "codex_diff")
        link = object_link("data", journal.session_id, diff["id"])
        seen = []
        view.anchorClicked.connect(lambda url: seen.append(url.toString()))
        view.anchorClicked.emit(QUrl(link))
        assert seen == [link]
        view.setHtml(DetailContent(index).prepare("data", diff["id"])["html"])
        assert "+changed" in view.toPlainText()
        view.close()


def test_native_mainwindow_data_navigation(tmp_path):
    from agent_gui_wait import receipts, settle

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "window") as journal:
        backend = begin(journal)
        emit(backend, "item/completed", item=command(aggregatedOutput="window evidence"))
        emit(backend, "turn/plan/updated", plan=[{"step": "Review files", "status": "completed"}])
        emit(backend, "turn/diff/updated", diff="+window evidence")
        backend._finish("completed")
        window = AssistantWindow(backend)
        window.resize(1100, 740)
        window.show()
        try:
            settle(window)
            window.renderer.flush()
            index = WorkbenchIndex(journal).sync()
            for row in index.data.values():
                if not row.get("native"):
                    continue
                window.details.open_object_link(QUrl(object_link("data", journal.session_id, row["id"])))
                receipts(window)
                app.processEvents()
                assert window.tabs.currentWidget() is window.detail
                assert not window.detail.notice.text()
                assert window.detail.document.toPlainText()
                window.detail.go_back()
                app.processEvents()
            assert "执行计划" in window.display.toPlainText()
            assert window.grab().save(str(tmp_path / "native-mainwindow.png"))
        finally:
            window.close()
            window.sessions.close()
            assert window.sessions.wait(5)
