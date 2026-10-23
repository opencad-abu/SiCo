"""Chinese translations for Qt's built-in controls and dialogs.

Qt draws standard buttons, file dialogs and text context menus from its own
translation catalog. Load the official Simplified Chinese catalog whenever
the platform provides it, then keep a small fallback for standard text
actions so context menus stay Chinese if that catalog is unavailable.
"""

from __future__ import annotations

from PyQt5.QtCore import QLibraryInfo, QTranslator


_TEXT_CONTROL_CONTEXTS = frozenset(
    ("QLineEdit", "QTextEdit", "QWidgetTextControl")
)
_STANDARD_ACTION_LABELS = {
    "&Undo": "撤消(&U)",
    "&Redo": "重做(&R)",
    "Cu&t": "剪切(&T)",
    "&Copy": "复制(&C)",
    "&Paste": "粘贴(&P)",
    "Delete": "删除",
    "&Delete": "删除",
    "Select All": "全选",
    "&Select All": "全选",
    "Copy &Link Location": "复制链接地址(&L)",
    "Copy Link Location": "复制链接地址",
}


class ChineseContextMenuTranslator(QTranslator):
    """Translate only Qt's built-in text-control context actions."""

    def translate(self, context, source_text, _disambiguation, _n):
        if context not in _TEXT_CONTROL_CONTEXTS:
            return ""
        return _STANDARD_ACTION_LABELS.get(source_text, "")


def _load_standard_translator(application):
    translator = QTranslator(application)
    directory = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
    if directory and translator.load("qt_zh_CN", directory):
        application.installTranslator(translator)
        return translator
    return None


def install_chinese_qt_translations(application):
    """Install persistent Chinese translators on this QApplication."""

    if getattr(application, "_sico_chinese_qt_installed", False):
        return getattr(application, "_sico_chinese_qt_translator", None)
    fallback = ChineseContextMenuTranslator(application)
    application.installTranslator(fallback)
    standard = _load_standard_translator(application)
    application._sico_chinese_qt_translator = standard
    application._sico_chinese_context_menu_translator = fallback
    application._sico_chinese_qt_installed = True
    return standard


__all__ = (
    "ChineseContextMenuTranslator",
    "install_chinese_qt_translations",
)
