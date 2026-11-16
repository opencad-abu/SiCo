"""会话气泡里的表格：紧贴段落的写法也要成表，并与详情页统一 #888888 灰边。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from sico_ui.presentation import DocumentView, document_tables, style_tables
from sico_ui.theme import ACCENT, COPILOT, TABLE_BORDER
from sico_ui.transcript import Transcript


def render(text, role="assistant"):
    app = QApplication.instance() or QApplication([])
    view = DocumentView()
    view.resize(900, 700)
    view.show()
    transcript = Transcript(read_only=True)
    transcript.append(role, text)
    transcript.render(view, force=True)
    app.processEvents()
    return view


def render_many(texts):
    app = QApplication.instance() or QApplication([])
    view = DocumentView()
    view.resize(900, 700)
    view.show()
    transcript = Transcript(read_only=True)
    for text in texts:
        transcript.append("assistant", text)
    transcript.render(view, force=True)
    assert transcript.dirty
    transcript.render(view, force=True)
    app.processEvents()
    assert not transcript.dirty
    return view


def test_paragraph_adjacent_table_renders_inside_the_bubble():
    """模型常见的“标题\\n| 列 |”写法要补空行成表，表格边框统一灰。"""

    view = render("器件概览\n| 类别 | 数量 |\n|---|---|\n| PMOS | 3 |\n结束")
    tables = document_tables(view.document())
    nested = document_tables(view.document(), nested=True)
    assert len(tables) == 2 and len(nested) == 1, (len(tables), len(nested))
    assert "|---" not in view.toPlainText()
    fmt = nested[0].format().toTableFormat()
    assert fmt.border() == 1.0
    assert fmt.borderBrush().color().name() == TABLE_BORDER
    assert fmt.cellSpacing() == 0.0
    # 表头（首行）红棕色加粗，正文行保持原样。
    header = nested[0].cellAt(0, 0).firstCursorPosition().charFormat()
    assert header.foreground().color().name().upper() == ACCENT
    assert header.fontWeight() >= QFont.Bold
    body = nested[0].cellAt(1, 0).firstCursorPosition().charFormat()
    assert body.foreground().style() == 0 and body.fontWeight() < QFont.Bold


def test_last_table_in_a_long_transcript_keeps_the_shared_style():
    """续渲染插入的最后一张表也要在 edit block 结束后补上灰边。"""

    view = render_many([f"消息 {index}" for index in range(9)] + [
        "最后一页\n\n| 项目 | 结果 |\n|---|---|\n| 库路径 | AnalogTest |\n| 状态 | 就绪 |\n结束"
    ])
    nested = document_tables(view.document(), nested=True)
    assert len(nested) == 1
    fmt = nested[0].format().toTableFormat()
    assert fmt.border() == 1.0
    assert fmt.borderBrush().color().name() == TABLE_BORDER
    assert fmt.cellPadding() == 5.0
    assert fmt.cellSpacing() == 0.0
    header = nested[0].cellAt(0, 0).firstCursorPosition().charFormat()
    assert header.foreground().color().name().upper() == ACCENT


def test_bubble_keeps_its_own_border_colour():
    """气泡边框仍是红棕 / 浅灰，不被表格样式覆盖。"""

    view = render("普通回复，没有表格")
    bubbles = document_tables(view.document())
    assert len(bubbles) == 1
    assert bubbles[0].format().toTableFormat().borderBrush().color().name().upper() == COPILOT
    style_tables(view.document(), nested=True)
    assert bubbles[0].format().toTableFormat().borderBrush().color().name().upper() == COPILOT

    view = render("用户消息，没有表格", role="user")
    bubbles = document_tables(view.document())
    assert bubbles[0].format().toTableFormat().borderBrush().color().name().upper() == ACCENT
