"""详情页：代码块高亮与输出分区（M2/M4，契约见 COPILOT_DETAIL_CODE_OUTPUT_PLAN.md）。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QTextFrameFormat
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from sico.service.display import data_html
from sico_ui.presentation import document_tables
from sico_ui.theme import (
    ACCENT,
    CODE_BLOCK_BACKGROUND,
    CODE_COMMENT,
    CODE_KEYWORD,
    TABLE_BORDER,
)
from sico_ui.workbench_view import WorkbenchDetail


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def detail(app):
    widget = WorkbenchDetail()
    widget.resize(700, 600)
    widget.show()
    app.processEvents()
    widget.current = ("data", "row-1")
    widget._detail_generation = 1
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def render(detail, result, *, generation=1):
    detail._detail_generation = generation
    detail._render_detail(generation, result)
    QApplication.instance().processEvents()


def test_markdown_fences_are_highlighted_and_output_area_stays_hidden(detail):
    render(detail, {
        "title": "示例",
        "markdown": "说明\n\n```python\ndef f(x):\n    return x  # note\n```\n\n结束\n",
    })
    html = detail.document.toHtml().lower()
    assert CODE_KEYWORD.lower() in html and CODE_COMMENT.lower() in html
    assert CODE_BLOCK_BACKGROUND.lower() in html
    # 代码块浅底只出现在代码块本身，后续段落不继承。
    assert html.count(CODE_BLOCK_BACKGROUND.lower()) == 1
    text = detail.document.toPlainText()
    assert "def f(x)" in text and "结束" in text
    assert not detail.output_area.isVisible()


def test_html_output_section_moves_to_the_own_text_area(detail):
    detail.current = ("process", "p1")
    render(detail, {
        "title": "进程",
        "html": ("<h3>启动命令</h3><pre>bash -lc 'ls -l'</pre>"
                 "<h3>输出</h3><pre>hello out\nline2</pre>"),
    })
    assert detail.output.toPlainText() == "hello out\nline2"
    assert "hello out" not in detail.document.toPlainText()
    assert detail.output_area.isVisible()
    # 上文档、下输出，默认 2:1。
    upper, lower = detail.splitter.sizes()
    assert 0 < lower < upper
    # 启动命令小节按识别结果着色（浅底块）。
    assert CODE_BLOCK_BACKGROUND.lower() in detail.document.toHtml().lower()


def test_empty_output_placeholder_stays_in_the_document(detail):
    detail.current = ("process", "p2")
    render(detail, {
        "title": "进程",
        "html": "<h3>启动命令</h3><pre>ls -l</pre><h3>输出</h3><p>暂无已捕获的输出。</p>",
    })
    assert not detail.output_area.isVisible()
    assert detail.output.toPlainText() == ""
    assert "暂无已捕获的输出" in detail.document.toPlainText()


def test_structured_script_and_output_fields_use_the_same_layout(detail):
    detail.current = ("process", "p3")
    render(detail, {
        "title": "进程",
        "prefix": "<table><tr><td>PID</td><td>4242</td></tr></table>",
        "script": "if [ -f x ]; then echo ok; fi",
        "script_language": "shell",
        "output": "out1\nout2\nout3",
    })
    html = detail.document.toHtml().lower()
    text = detail.document.toPlainText()
    # 字段表（prefix）与启动命令都在文档区；命令按结构化语言着色。
    assert "PID" in text and "4242" in text and "启动命令" in text
    assert CODE_KEYWORD.lower() in html and CODE_BLOCK_BACKGROUND.lower() in html
    assert detail.output.toPlainText() == "out1\nout2\nout3"
    assert detail.output_area.isVisible()


def test_switching_back_to_a_plain_row_hides_and_clears_the_output(detail):
    detail.current = ("process", "p4")
    render(detail, {"title": "进程", "script": "ls", "output": "some output"})
    assert detail.output_area.isVisible()
    render(detail, {"title": "数据", "html": "<p>只有正文</p>"}, generation=2)
    assert not detail.output_area.isVisible()
    assert detail.output.toPlainText() == ""
    assert "只有正文" in detail.document.toPlainText()


def test_detail_tables_share_one_gray_border(detail):
    """详情页表格统一 1px #888888 边框：字段表、工具表与 markdown 表格都覆盖。"""

    render(detail, {
        "title": "数据",
        "html": (data_html({"库": "AnalogWork", "单元": "amp"})
                 + "<table border='1' cellspacing='0' cellpadding='5'>"
                   "<tr><td>甲</td><td>乙</td></tr></table>"),
    })
    document = detail.document.document()
    tables = document_tables(document)
    assert len(tables) == 2
    for table in tables:
        fmt = table.format().toTableFormat()
        # Qt 把表格边与单元格边叠在一起渲染，1.0 实测为 2px 实线。
        assert fmt.border() == 1.0
        assert fmt.borderStyle() == QTextFrameFormat.BorderStyle_Solid
        assert fmt.borderBrush().color().name() == TABLE_BORDER
        # Qt 的 markdown 表格默认带 2px 间距，会被渲染成两条线；这里必须归零。
        assert fmt.cellSpacing() == 0.0
        assert fmt.cellPadding() == 5.0
    render(detail, {"title": "报告",
                    "markdown": "结论\n\n| 项目 | 结果 |\n| --- | --- |\n| 绑定 | 有效 |\n"},
           generation=2)
    tables = document_tables(detail.document.document())
    assert len(tables) == 1
    assert tables[0].format().toTableFormat().borderBrush().color().name() == TABLE_BORDER
    # 没有表格时也不应报错。
    render(detail, {"title": "纯文本", "html": "<p>没有表格</p>"}, generation=3)
    assert document_tables(detail.document.document()) == []


def test_table_header_is_accent_and_bold_only_when_it_has_one(detail):
    """有表头的表格（markdown/工具表）首行红棕色加粗；字段表首行不动。"""

    render(detail, {"title": "报告",
                    "markdown": "| 项目 | 结果 |\n| --- | --- |\n| 绑定 | 有效 |\n"})
    table = document_tables(detail.document.document())[0]
    header = table.cellAt(0, 0).firstCursorPosition().charFormat()
    assert header.foreground().color().name().upper() == ACCENT
    assert header.fontWeight() >= QFont.Bold
    body = table.cellAt(1, 0).firstCursorPosition().charFormat()
    assert body.fontWeight() < QFont.Bold

    render(detail, {"title": "数据", "html": data_html({"库": "AnalogWork"})}, generation=2)
    fields = document_tables(detail.document.document())[0]
    first = fields.cellAt(0, 0).firstCursorPosition().charFormat()
    assert first.foreground().style() == 0 and first.fontWeight() < QFont.Bold
