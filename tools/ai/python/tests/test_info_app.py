"""Chinese notice levels, nonblocking delivery and owned popup lifetime."""

import threading

import pytest
from agent_gui_wait import wait_until
from info_app_fixtures import dismiss_notice, notice_dialog, notice_text
from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton, QWidget
from sico_ui.info_app import InfoApp


@pytest.fixture
def notices():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.show()
    presenter = InfoApp(window)
    yield window, presenter
    presenter.close()
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("method,label", [
    ("error", "错误"), ("warning", "警告"), ("info", "信息"), ("notice", "提示"),
])
def test_all_four_levels_use_chinese_and_do_not_block_qt(notices, method, label):
    window, presenter = notices
    pulse = []
    QTimer.singleShot(0, lambda: pulse.append(True))
    getattr(presenter, method)("操作结果", "完整的原因与下一步")
    dialog = notice_dialog(window)
    assert dialog.windowTitle() == "Silicon Copilot · " + label
    assert notice_text(window).startswith(label + " · 操作结果")
    assert not dialog.isModal() and dialog.windowModality() == Qt.NonModal
    wait_until(lambda: pulse)
    QTest.keyClick(dialog.findChild(QPushButton, "infoConfirm"), Qt.Key_Return)
    assert notice_dialog(window) is None


def test_long_plain_text_is_complete_scrollable_and_copyable(notices):
    window, presenter = notices
    text = '<b>原请求</b> & "参数"\n' + "详细原因\n" * 400
    presenter.warning("停止项目服务未执行", text)
    dialog = notice_dialog(window)
    body = dialog.findChild(QPlainTextEdit, "infoMessage")
    assert body.isReadOnly() and body.toPlainText() == text
    QApplication.processEvents()
    assert body.verticalScrollBar().maximum() > 0
    dialog.findChild(QPushButton, "infoCopy").click()
    assert QApplication.clipboard().text() == "警告 · 停止项目服务未执行\n\n" + text


def test_identical_pending_messages_coalesce_and_distinct_messages_queue(notices):
    window, presenter = notices
    presenter.warning("原请求", "结果未知")
    first = notice_dialog(window)
    for _ in range(3):
        presenter.warning("原请求", "结果未知")
    presenter.info("后续结果", "未执行")
    presenter.info("后续结果", "未执行")
    assert notice_dialog(window) is first
    assert first.findChild(QLabel, "infoCount").text() == "相同消息 4 次；还有 1 条消息"
    dismiss_notice(window)
    wait_until(lambda: "信息 · 后续结果" in notice_text(window))
    assert notice_dialog(window) is first
    assert first.findChild(QLabel, "infoCount").text() == "相同消息 2 次"
    first.reject()
    QApplication.processEvents()
    assert notice_dialog(window) is None
    # A later explicit action can report the same problem again.
    presenter.warning("原请求", "结果未知")
    assert "原请求" in notice_text(window)


def test_worker_delivery_runs_on_qt_and_shutdown_discards_pending_notices(notices):
    window, presenter = notices
    worker = threading.Thread(target=lambda: presenter.error("异步操作", "失败证据"))
    worker.start()
    worker.join(1)
    wait_until(lambda: "失败证据" in notice_text(window))
    presenter.notice("下一步", "保留草稿")
    presenter.close()
    presenter.error("迟到结果", "不会打开窗口")
    QApplication.processEvents()
    assert notice_dialog(window) is None


def test_closing_guard_prevents_late_popup(notices):
    window, _presenter = notices
    presenter = InfoApp(window, closing=lambda: True)
    presenter.error("迟到结果", "不会打开窗口")
    assert notice_dialog(window) is None
