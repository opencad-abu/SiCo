"""Qt's built-in dialogs and text menus follow SiCo's Chinese interface."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QTextBrowser,
    QToolButton,
)
from sico_ui.qt_translation import install_chinese_qt_translations


def _labels(widget):
    menu = widget.createStandardContextMenu()
    try:
        return [
            action.text().split("\t", 1)[0]
            for action in menu.actions()
            if not action.isSeparator()
        ]
    finally:
        menu.deleteLater()


def test_standard_text_context_menus_are_chinese(qt_application_lifetime):
    translator = install_chinese_qt_translations(qt_application_lifetime)

    assert install_chinese_qt_translations(qt_application_lifetime) is translator

    editable = [
        "撤消(&U)",
        "重做(&R)",
        "剪切(&T)",
        "复制(&C)",
        "粘贴(&P)",
        "删除",
        "全选",
    ]
    assert _labels(QLineEdit()) == editable
    assert _labels(QPlainTextEdit()) == editable
    assert _labels(QTextBrowser()) == ["复制(&C)", "复制链接地址(&L)", "全选"]


def test_standard_dialog_buttons_are_chinese(qt_application_lifetime):
    install_chinese_qt_translations(qt_application_lifetime)

    buttons = QDialogButtonBox(
        QDialogButtonBox.Ok
        | QDialogButtonBox.Cancel
        | QDialogButtonBox.Close
        | QDialogButtonBox.Yes
        | QDialogButtonBox.No
    )
    assert [button.text() for button in buttons.buttons()] == [
        "确定",
        "关闭",
        "取消",
        "是(&Y)",
        "否(&N)",
    ]
    message = QMessageBox()
    message.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    assert [button.text() for button in message.buttons()] == ["是(&Y)", "否(&N)"]


def test_standard_file_dialog_is_chinese(qt_application_lifetime):
    install_chinese_qt_translations(qt_application_lifetime)

    dialog = QFileDialog()
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.show()
    qt_application_lifetime.processEvents()
    try:
        assert dialog.windowTitle() == "打开"
        labels = {label.text() for label in dialog.findChildren(QLabel)}
        assert {"查找范围：", "文件名称(&N)：", "文件类型："} <= labels
        tips = {button.toolTip() for button in dialog.findChildren(QToolButton)}
        assert {"后退", "前进", "父目录", "新建文件夹", "列表视图", "详情视图"} <= tips
    finally:
        dialog.close()
        dialog.deleteLater()
