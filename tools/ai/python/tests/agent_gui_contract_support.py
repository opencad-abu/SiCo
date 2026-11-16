# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""Shared agent gui contract fixtures and helpers."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QTextCursor

from sico_ui.transcript import COUNTER_ICON, TRIANGLE_COLLAPSED, TRIANGLE_EXPANDED

COLLAPSED_ICON = f'src="{TRIANGLE_COLLAPSED}"'


EXPANDED_ICON = f'src="{TRIANGLE_EXPANDED}"'


COUNTER_MARK = f'src="{COUNTER_ICON}"'


TRIANGLE_NAMES = (TRIANGLE_COLLAPSED, TRIANGLE_EXPANDED)


def link_point(view, text):
    """Viewport point inside a rendered link, for mouse-driven link tests."""
    found = view.document().find(text)
    assert not found.isNull(), text
    cursor = QTextCursor(view.document())
    cursor.setPosition(found.selectionStart() + 1)
    return view.cursorRect(cursor).center()


def icon_point(view):
    """Viewport point inside the counter's fold marker, located by its image name.

    计数行现在带两张图片：开头的锤子扳手和末尾的折叠三角。点击点取图片字符
    左右两个光标矩形的中点：既不能拿行里的第一张图片代替，也不能落在图片
    右缘之外（那样点不中链接）。
    """
    document = view.document()
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if (fragment.isValid() and fragment.charFormat().isImageFormat()
                    and fragment.charFormat().toImageFormat().name() in TRIANGLE_NAMES):
                cursor = QTextCursor(document)
                cursor.setPosition(fragment.position())
                left = view.cursorRect(cursor)
                cursor.setPosition(fragment.position() + 1)
                right = view.cursorRect(cursor)
                return QPoint(left.x() + (right.x() - left.x()) // 2, left.center().y())
            iterator += 1
        block = block.next()
    raise AssertionError("counter fold marker is missing")
