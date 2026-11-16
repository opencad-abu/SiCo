"""Single-click process details, live output and stale-session isolation."""

import os
from concurrent.futures import Future

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import receipts, settle
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.storage.journal import SessionJournal
from agent_window_fixture import create_window as AssistantWindow
from sico_ui.processes import ProcessPanel
from test_codex_native_projection import begin, command, emit


def test_process_tab_click_live_output_and_history_isolation(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "old") as old:
        old_backend = begin(old)
        emit(old_backend, "item/completed", item=command(aggregatedOutput="old command output"))
        old_backend._finish("completed")
    with SessionJournal(tmp_path, "live") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None,
                                                  processId="PTY-123"))
        window = AssistantWindow(backend)
        window.resize(1280, 820)
        window.show()
        try:
            settle(window)
            window.show_panel("processes")
            panel = window.centers.processes
            receipts(window)
            assert window.centers.tabText(3) == "进程"
            assert window.centers.work_filter.isHidden()
            item = panel.list.topLevelItem(0)
            assert item.text(1) == "—" and item.text(2) == "运行中"
            QTest.mouseClick(panel.list.viewport(), Qt.LeftButton,
                             pos=panel.list.visualItemRect(item).center())
            receipts(window)
            assert window.tabs.currentWidget() is window.detail
            detail = window.detail.document
            assert all(label in detail.toPlainText() for label in
                       ("PID", "名称", "运行状态", "运行时长", "启动命令", "PTY-123"))
            # 还没有输出时只显示正文；输出单独落在下方只读文本区。
            output = window.detail.output
            assert not window.detail.output_area.isVisible()
            emit(backend, "item/commandExecution/outputDelta", itemId="command",
                 delta="live <literal>\n" + "long log row\n" * 250)
            backend.native.flush(force=True)
            window.detail.refresh_process()
            receipts(window)
            assert "live <literal>" in output.toPlainText()
            assert window.detail.output_area.isVisible()
            bar = output.verticalScrollBar()
            bar.setValue(bar.maximum() // 2)
            position = bar.value()
            assert position > 0
            emit(backend, "item/commandExecution/outputDelta", itemId="command", delta="newest output\n")
            backend.native.flush(force=True)
            window.detail.refresh_process()
            receipts(window)
            assert bar.value() == position and "newest output" in output.toPlainText()
            bar.setValue(bar.maximum())
            emit(backend, "item/completed", item=command(aggregatedOutput="finished\n" * 350))
            window.detail.refresh_process()
            receipts(window)
            assert bar.value() == bar.maximum() and "已完成" in detail.toPlainText()
            panel.poll()
            receipts(window)
            assert panel.list.topLevelItem(0).text(2) == "已完成"
            bar.setValue(0)
            window.grab().save(str(tmp_path / "process-detail.png"))
            window.detail.go_back()
            assert window.tabs.currentWidget() is window.pages["conversation"]
            backend._finish("completed")
            window.page.preview_session("old")
            settle(window)
            panel.poll()
            receipts(window)
            assert window.detail.current is None
            panel.open_item(panel.list.topLevelItem(0))
            receipts(window)
            # 历史进程的输出同样落在独立文本区；现场会话的输出不串台。
            assert "old command output" in window.detail.output.toPlainText()
            assert "PTY-123" not in detail.toPlainText()
            assert "PTY-123" not in window.detail.output.toPlainText()
            window.centers.show_tools()
            assert not window.centers.work_filter.isHidden()
        finally:
            window.sessions.close()
            assert window.sessions.wait(5)
            window.close()
            app.processEvents()


def test_terminal_tab_opens_and_reports_an_unconfigured_terminal(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "terminal-tab") as journal:
        backend = begin(journal)
        window = AssistantWindow(backend)
        window.resize(1280, 820)
        window.show()
        try:
            settle(window)
            window.show_panel("terminal")
            settle(window)
            assert window.centers.tabText(4) == "终端"
            assert window.centers.work_filter.isHidden()
            assert "暂不可读" in window.centers.terminal.status.text()
            assert window.centers.terminal.status.isHidden()
            for _ in range(4):
                window.show_panel("processes")
                settle(window)
                window.show_panel("terminal")
                settle(window)
        finally:
            window.close()


def test_process_invalidation_discards_pending_source_result():
    app = QApplication.instance() or QApplication([])
    pending = Future()
    pending.set_running_or_notify_cancel()

    class Source:
        def processes(self, *_args):
            return pending

        def validate_current(self):
            raise AssertionError("Stale process source was consulted")

    panel = ProcessPanel(Source())
    panel.show()
    try:
        app.processEvents()
        assert panel.receipt.pending
        panel.invalidate()
        pending.set_result({"rows": [], "total": 0, "running": 0, "offset": 0})
        panel.receipt.poll()
        assert panel.list.topLevelItemCount() == 0
        assert panel.summary.text() == "暂无 SiCo 启动的进程"
    finally:
        panel.close()
