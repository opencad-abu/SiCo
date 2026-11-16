"""SiCo 顶层窗口契约：只用自绘 chrome，确认与输入走 si_prompt。"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import settle, wait_until
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileSystemModel,
    QTreeView,
)

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.si_prompt import (
    SiConfirm,
    SiFilePrompt,
    ask_choice,
    ask_text,
    confirm,
    notice,
)

UI_ROOT = pathlib.Path(__file__).resolve().parents[2] / "sico" / "python" / "sico_ui"
# 系统消息框与输入框一律不要：确认和输入统一走 si_prompt 的自绘对话框。
BANNED_DIALOGS = frozenset({"QMessageBox", "QInputDialog", "QProgressDialog",
                            "QErrorMessage", "QColorDialog", "QFontDialog"})
# 顶层窗口只能从 chrome 的三个基类派生。
BANNED_WINDOWS = frozenset({"QDialog", "QMainWindow"})
# 文件选择也走 si_prompt：QFileDialog 只能出现在那里，且必须嵌进自绘窗口。
FILE_DIALOG = "QFileDialog"


def build_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    journal = SessionJournal(tmp_path, "sico-dialogs")
    journal.__enter__()
    loop = PythonBackend(
        ContextDemoProvider(), ToolRegistry(), journal,
        BoundContext("dialog-instance", "dialog-generation", "bound", {"valid": True}),
    )
    return app, journal, AssistantWindow(loop, persistent=True)


def click_when_open(widget, key, seen=None):
    """在模态循环里点按钮：``seen`` 用来回收点下去时的状态。"""
    def click():
        dialog = QApplication.activeModalWidget()
        if seen is not None:
            seen.append(dialog.button(key))
        dialog.button(key).click()
    QTimer.singleShot(10, click)
    return widget


def test_sico_ui_has_no_qt_message_or_input_dialogs():
    """主界面不再引用 QMessageBox/QInputDialog，文件选择只从 si_prompt 走。"""
    found = {}
    for path in sorted(UI_ROOT.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "PyQt5.QtWidgets":
                names = {alias.name for alias in node.names}
                if names & BANNED_DIALOGS:
                    found.setdefault(path.name, set()).update(names & BANNED_DIALOGS)
                if FILE_DIALOG in names and path.name != "si_prompt.py":
                    found.setdefault(path.name, set()).add(FILE_DIALOG)
            elif isinstance(node, ast.Name) and node.id in BANNED_DIALOGS:
                found.setdefault(path.name, set()).add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in BANNED_DIALOGS:
                found.setdefault(path.name, set()).add(node.attr)
    assert found == {}


def test_sico_ui_top_level_windows_only_come_from_the_shared_chrome():
    """每个顶层窗口都从 SiMainWindow/SiWidget/SiDialog 派生，边框和标题栏只有一套。"""
    offside = {}
    for path in sorted(UI_ROOT.glob("*.py")):
        if path.name == "chrome.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(base) for base in node.bases}
            if bases & BANNED_WINDOWS:
                offside[path.name] = (node.name, sorted(bases))
    assert offside == {}


def test_infoapp_notice_is_a_frameless_sico_window(tmp_path):
    """InfoApp 通知也是无边框 SiCo 窗口：自绘标题栏、只留关闭按钮。"""
    app, journal, window = build_window(tmp_path)
    try:
        window.show()
        window.info_app.warning("初始化失败", "示例正文")
        wait_until(lambda: window.findChild(QDialog, "infoDialog") is not None)
        settle(window, tasks=False)
        dialog = window.findChild(QDialog, "infoDialog")
        assert dialog.isVisible() and not dialog.isModal()
        assert dialog.windowFlags() & Qt.FramelessWindowHint
        assert dialog.windowTitle() == "Silicon Copilot · 警告"
        assert dialog.title_bar.title.text() == "Silicon Copilot · 警告"
        assert sorted(dialog.title_bar.buttons) == ["close"]
        dialog.title_bar.buttons["close"].click()
        app.processEvents()
        assert not dialog.isVisible()
        app.processEvents()
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_confirm_keeps_the_registered_order_and_chrome_close_cancels():
    """确认框按注册顺序排按钮；标题栏关闭等同取消，不会误触发破坏性动作。"""
    app = QApplication.instance() or QApplication([])
    dialog = SiConfirm("关闭 SiCo", "选择")
    for key in ("结束服务", "关闭窗口", "取消"):
        dialog.add_choice(key, key, escape=key == "取消")
    dialog.adjustSize()
    app.processEvents()
    assert [button.text() for button in dialog.findChildren(type(dialog.button("取消")))] == \
        ["结束服务", "关闭窗口", "取消"]
    click_when_open(dialog, "取消")
    assert dialog.ask() == "取消"

    dialog = SiConfirm("关闭 SiCo", "选择")
    dialog.add_choice("accept", "关闭窗口", default=True)
    dialog.add_choice("cancel", "取消", escape=True)
    QTimer.singleShot(10, lambda: dialog.title_bar.buttons["close"].click())
    assert dialog.ask() == "cancel"


def test_confirm_and_notice_helpers_return_the_choice(tmp_path):
    """confirm()/notice() 的返回值与危险按钮属性。"""
    app = QApplication.instance() or QApplication([])
    seen = []
    click_when_open(None, "accept", seen)
    assert confirm(None, "删除会话", "确定删除？", choice="删除", danger=True) is True
    assert seen[0].property("dialogDanger") is True and seen[0].text() == "删除"

    seen.clear()
    click_when_open(None, "cancel", seen)
    assert confirm(None, "删除会话", "确定删除？", choice="删除") is False
    assert seen[0].property("dialogDanger") in (None, False)

    seen.clear()
    click_when_open(None, "ok", seen)
    assert notice(None, "终端", "终端工具不可用") == "ok"


def test_prompt_helpers_return_what_the_user_typed():
    """输入框读的是对话框关闭后的值：确认返回 (文本, True)，取消返回 (文本, False)。"""
    app = QApplication.instance() or QApplication([])

    def enter():
        dialog = QApplication.activeModalWidget()
        dialog.field.setText("  历史复盘  ")
        dialog.pick("accept")

    QTimer.singleShot(10, enter)
    assert ask_text(None, "重命名会话", "名称：", value="历史复盘") == ("  历史复盘  ", True)

    QTimer.singleShot(10, lambda: QApplication.activeModalWidget().reject())
    assert ask_text(None, "重命名会话", "名称：") == ("", False)

    def pick_choice():
        dialog = QApplication.activeModalWidget()
        dialog.field.setCurrentText("b")
        dialog.pick("accept")

    QTimer.singleShot(10, pick_choice)
    assert ask_choice(None, "技能", "已启用技能", ["a", "b"]) == ("b", True)


def test_file_prompt_embeds_the_qt_chooser_in_the_sico_chrome(tmp_path):
    """文件选择也是无边框 SiCo 窗口：Qt 选择器嵌在正文里，按钮只留自绘那一排。"""
    app = QApplication.instance() or QApplication([])
    picture = tmp_path / "picture.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n")
    dialog = SiFilePrompt("选择附件", ["Images (*.png *.jpg)"], None, directory=str(tmp_path))
    try:
        dialog.show()
        view = next(view for view in dialog.picker.findChildren(QTreeView)
                    if isinstance(view.model(), QFileSystemModel))
        model = view.model()
        root = model.index(str(tmp_path))
        wait_until(lambda: model.rowCount(root) > 0)
        assert dialog.windowFlags() & Qt.FramelessWindowHint
        assert dialog.windowTitle() == "选择附件"
        assert dialog.title_bar.title.text() == "选择附件"
        assert dialog.picker.isVisible() and dialog.picker.window() is dialog
        assert all(not button.isVisible()
                   for button in dialog.picker.findChildren(QDialogButtonBox))
        # 选中列表里的文件再点“确定”，返回的就是这个文件。
        view.setCurrentIndex(model.index(0, 0, root))
        for _ in range(3):
            app.processEvents()
        click_when_open(dialog, "accept")
        assert dialog.ask() == (str(picture), True)
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_file_prompt_without_a_selection_is_not_an_acceptance(tmp_path):
    """没选出文件时按下“确定”不算确认，取消和关闭标题栏也一样。"""
    app = QApplication.instance() or QApplication([])
    for key in ("accept", "cancel"):
        dialog = SiFilePrompt("选择附件", ["Images (*.png)"], None, directory=str(tmp_path))
        click_when_open(dialog, key)
        assert dialog.ask() == ("", False)
        dialog.deleteLater()
    dialog = SiFilePrompt("选择附件", ["Images (*.png)"], None, directory=str(tmp_path))
    QTimer.singleShot(10, lambda: dialog.title_bar.buttons["close"].click())
    assert dialog.ask() == ("", False)
    dialog.deleteLater()
    app.processEvents()
