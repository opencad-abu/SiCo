"""Passive Qt document with shared bounded display formatters."""

from PyQt5.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextTable,
    QTextTableFormat,
)
from PyQt5.QtWidgets import QTextBrowser

from cadgui.fonts import mono_family

from sico.service.display import (
    FIELD_LABELS,
    data_html,
    event_clock,
    event_time,
    is_recommended,
    plain_recommendation,
    recommended_label,
    target_label,
    token_amount,
)
from sico.service.event_fields import DOCUMENT_SURFACE as LEGACY_DOCUMENT_SURFACE
from .code_highlight import highlight, split_fences, table_safe_markdown
from .theme import active_palette

__all__ = (
    "BusyCursor", "DocumentView", "FIELD_LABELS", "data_html", "event_clock",
    "event_time", "insert_code_block", "insert_markdown_segments",
    "is_recommended", "plain_recommendation", "recommended_label",
    "markdown_document", "secondary_point_size", "target_label", "token_amount",
    "document_tables",
    "document_link_stylesheet", "style_document", "style_document_surfaces",
    "style_links", "style_tables", "transcript_link_format",
)


def secondary_point_size(base_point):
    """Half-size point shared by the tool counter and the full-content link."""
    return min(base_point, max(9, base_point // 2))


def transcript_link_format(url, *, point_size=None):
    """Accent-coloured anchor used by transcript reference links."""
    fmt = QTextCharFormat()
    fmt.setAnchor(True)
    fmt.setAnchorHref(url)
    fmt.setForeground(QColor(active_palette().accent))
    fmt.setFontUnderline(True)
    if point_size:
        fmt.setFontPointSize(point_size)
    return fmt


def document_link_stylesheet(palette=None):
    """Hyperlink colour for Qt rich text; Qt's default blue fails on dark."""
    palette = active_palette() if palette is None else palette
    return "a { color: %s; }" % palette.link


def style_links(document, palette=None):
    """Underline real anchors, leaving inline-code and other text highlights plain."""
    palette = active_palette() if palette is None else palette
    link = QTextCharFormat()
    link.setForeground(QColor(palette.link))
    link.setFontUnderline(True)
    block = document.begin()
    while block.isValid():
        fragment = block.begin()
        while not fragment.atEnd():
            run = fragment.fragment()
            if run.isValid():
                fmt = run.charFormat()
                if (fmt.isAnchor() and not fmt.isImageFormat()
                        and (not fmt.fontUnderline()
                             or fmt.foreground().color().name().lower()
                             != palette.link.lower())):
                    cursor = QTextCursor(document)
                    cursor.setPosition(run.position())
                    cursor.setPosition(run.position() + run.length(),
                                       QTextCursor.KeepAnchor)
                    cursor.mergeCharFormat(link)
            fragment += 1
        block = block.next()


def markdown_document(markdown):
    """Parse bounded markdown into a safe document with real links underlined."""
    from .transcript import SafeDocument

    document = SafeDocument()
    document.setMarkdown(table_safe_markdown(markdown), QTextDocument.MarkdownFeatures(
        QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML))
    style_links(document)
    return document


def document_tables(document, *, nested=False):
    """收集文档里的表格；``nested=True`` 只取嵌在别的框架/表格里的那些。

    会话里的消息气泡本身也是表格，气泡里的 markdown/工具结果表格才是内容表格。
    """
    found = []

    def walk(frame, depth):
        for child in frame.childFrames():
            if isinstance(child, QTextTable) and (not nested or depth > 0):
                found.append(child)
            walk(child, depth + 1)

    walk(document.rootFrame(), 0)
    return found


def style_tables(document, *, nested=False):
    """表格统一边框与表头：按当前调色板绘制实线格线，表头红棕色加粗。

    详情页、会话、工具结果、记录详情共用；字段表（data_html）没有表头，
    首行保持正文样式。
    """
    palette = active_palette()
    brush = QBrush(QColor(palette.table_border))
    for table in document_tables(document, nested=nested):
        fmt = table.format().toTableFormat()
        if (fmt.border() == 1.0
                and fmt.borderStyle() == QTextTableFormat.BorderStyle_Solid
                and fmt.borderBrush().color().name().lower() == palette.table_border.lower()
                and fmt.cellSpacing() == 0.0
                and fmt.cellPadding() == 5.0):
            # Already styled by an earlier render pass: avoid forcing another
            # layout of every table on each continuation tick.
            style_table_header(document, table)
            continue
        # Qt 会把表格边与相邻单元格边各取整后叠在一起，线厚只能是偶数：
        # 1 → 2px、2 → 4px、3 → 6px；按用户反馈取 1.0（实测 2px）。
        fmt.setBorder(1.0)
        fmt.setBorderStyle(QTextTableFormat.BorderStyle_Solid)
        fmt.setBorderBrush(brush)
        # markdown 表格默认 cellSpacing=2、cellPadding=0：分别归零/补齐，
        # 格线才不会变成“两条 1px 夹白缝”，文字也不会贴着框线。
        fmt.setCellSpacing(0.0)
        fmt.setCellPadding(5.0)
        table.setFormat(fmt)
        style_table_header(document, table)


def style_table_header(document, table):
    """首行本来就是粗体（markdown 表 / 工具表）的表格，表头改红棕色加粗。"""

    if table.rows() < 1 or table.columns() < 1:
        return False
    formats = [table.cellAt(0, column).firstCursorPosition().charFormat()
               for column in range(table.columns())]
    weights = [char_format.fontWeight() for char_format in formats]
    if min(weights) < QFont.Bold:
        return False
    accent = QColor(active_palette().accent).name().lower()
    if all(char_format.fontWeight() >= QFont.Bold
           and char_format.foreground().color().name().lower() == accent
           for char_format in formats):
        return True
    header = QTextCharFormat()
    header.setForeground(QBrush(QColor(active_palette().accent)))
    header.setFontWeight(QFont.Bold)
    for column in range(table.columns()):
        cell = table.cellAt(0, column)
        cursor = QTextCursor(document)
        cursor.setPosition(cell.firstCursorPosition().position())
        cursor.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(header)
    return True


def style_document_surfaces(document):
    """Resolve service-emitted light surfaces against the active palette.

    Event detail HTML is produced by the data worker, which cannot know which
    appearance the desktop selected. Field-label cells and output blocks carry
    the legacy light surface until the popup renders them here.
    """
    palette = active_palette()
    brush = QBrush(QColor(palette.code_block_background))

    def legacy_background(fmt):
        background = fmt.background()
        return (background.style()
                and background.color().name().lower()
                == LEGACY_DOCUMENT_SURFACE.lower())

    for table in document_tables(document):
        for row in range(table.rows()):
            for column in range(table.columns()):
                cell = table.cellAt(row, column)
                fmt = cell.format()
                if legacy_background(fmt):
                    fmt.setBackground(brush)
                    cell.setFormat(fmt)
    block = document.begin()
    while block.isValid():
        fmt = block.blockFormat()
        if legacy_background(fmt):
            fmt.setBackground(brush)
            cursor = QTextCursor(block)
            cursor.setBlockFormat(fmt)
        fragment = block.begin()
        while not fragment.atEnd():
            run = fragment.fragment()
            if run.isValid():
                fmt = run.charFormat()
                if legacy_background(fmt):
                    fmt.setBackground(brush)
                    cursor = QTextCursor(block)
                    cursor.setPosition(run.position())
                    cursor.setPosition(run.position() + run.length(),
                                       QTextCursor.KeepAnchor)
                    cursor.setCharFormat(fmt)
            fragment += 1
        block = block.next()


def style_document(document, *, nested=False):
    """Apply every active-palette rule shared by rendered service documents."""
    document.setDefaultStyleSheet(document_link_stylesheet())
    style_links(document)
    style_tables(document, nested=nested)
    style_document_surfaces(document)


def insert_code_block(cursor, code, language):
    """程序化代码块：块底色 + 等宽 + 关键字着色。

    ``insertHtml`` 会丢掉 ``<pre>`` 的块级样式（底色、边距），所以底色与等宽
    用 QTextBlockFormat/QTextCharFormat 设置，只有 token 颜色走 span。会话详情、
    工作台详情与审阅卡片共用这一处渲染，代码块外观保持一致。
    """

    block = QTextBlockFormat()
    block.setBackground(QColor(active_palette().code_block_background))
    block.setTopMargin(6)
    block.setBottomMargin(6)
    code_format = QTextCharFormat()
    code_format.setFontFamily(mono_family())
    cursor.insertBlock(block, code_format)
    cursor.insertHtml(f'<span style="font-family: \'{mono_family()}\', monospace">'
                      + highlight(code, language) + "</span>")
    # insertBlock() 会继承当前块格式，必须显式回到默认格式，
    # 否则后续段落会带上代码块的浅底。
    cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())


def insert_markdown_segments(cursor, markdown):
    """文本段交给 Qt 的 markdown 渲染，围栏代码段走高亮块。"""

    first = True
    for part in split_fences(markdown):
        if part[0] == "code":
            insert_code_block(cursor, part[2], part[1])
            first = False
            continue
        if not part[1].strip():
            continue
        document = markdown_document(part[1])
        if not first:
            cursor.insertBlock()
        cursor.insertFragment(QTextDocumentFragment(document))
        first = False


class BusyCursor(QObject):
    """Busy pointer that only follows the widget currently loading data.

    加载数据、读取或继续历史记录期间 busy 只出现在对应的控件里；模型思考与
    工具执行不改变光标，气泡里的工具结果始终可以点击。文字控件悬停时会自己
    改写光标（链接小手、I 形光标），所以进入/移动后要重新压上 busy；
    ``owns_cursor`` 让控件保留自己的光标（例如气泡里的链接仍是小手）。
    """

    def __init__(self, widget, owns_cursor=None):
        super().__init__(widget)
        self.owns_cursor = owns_cursor
        self.target = widget.viewport() if hasattr(widget, "viewport") else widget
        self.busy = False
        self._restore = None
        self.target.installEventFilter(self)

    def set(self, busy):
        """Apply or drop the busy pointer for this widget."""
        busy = bool(busy)
        if busy == self.busy:
            return
        self.busy = busy
        if busy:
            self._restore = self.target.cursor()
            if self.target.underMouse():
                self.target.setCursor(Qt.BusyCursor)
        elif self._restore is not None:
            self.target.setCursor(self._restore)
            self._restore = None

    def eventFilter(self, _obj, event):
        if self.busy and event.type() in (QEvent.Enter, QEvent.MouseMove):
            if self.owns_cursor is None or not self.owns_cursor(event):
                self.target.setCursor(Qt.BusyCursor)
        return False


class DocumentView(QTextBrowser):
    # Double-click is a separate gesture from the single-click anchorClicked.
    linkDoubleClicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.document().setDefaultStyleSheet(document_link_stylesheet())
        # Links are routed through anchorClicked; a failed navigation would
        # otherwise blank the whole transcript.
        self.setOpenLinks(False)
        # 链接保留小手光标；其余区域在页面加载时才显示 busy。
        self._busy = BusyCursor(
            self, owns_cursor=lambda event: bool(self.anchor_text_at(event.pos())),
        )

    def set_busy(self, busy):
        """Busy pointer for this view only; other widgets keep their own cursors."""
        self._busy.set(busy)

    def anchor_text_at(self, position):
        """Anchor href under a point, including the empty tail of its line."""
        cursor = self.cursorForPosition(position)
        href = cursor.charFormat().anchorHref()
        block = cursor.block()
        if href or not block.text() or cursor.position() != block.position() + block.length() - 1:
            return href
        tail = QTextCursor(cursor)
        tail.setPosition(cursor.position() - 1)
        return tail.charFormat().anchorHref()

    def mouseDoubleClickEvent(self, event):
        href = self.anchor_text_at(event.pos())
        if href:
            self.linkDoubleClicked.emit(href)
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        href = self.anchor_text_at(event.pos())
        self.setToolTip("回退到这条消息" if href.startswith("rewind:") else "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.setToolTip("")
        super().leaveEvent(event)

    def loadResource(self, resource_type, url):
        # Rich text may contain model/tool-controlled local or remote image URLs.
        return None
