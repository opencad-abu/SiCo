"""Read and acknowledge the visible notification through its Qt widgets."""

from PyQt5.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton


def notice_dialog(window):
    dialog = window.findChild(QDialog, "infoDialog")
    return dialog if dialog is not None and dialog.isVisible() else None


def notice_text(window):
    dialog = notice_dialog(window)
    if dialog is None:
        return ""
    return (dialog.findChild(QLabel, "infoHeading").text() + "\n"
            + dialog.findChild(QPlainTextEdit, "infoMessage").toPlainText())


def dismiss_notice(window):
    dialog = notice_dialog(window)
    assert dialog is not None
    dialog.findChild(QPushButton, "infoConfirm").click()
