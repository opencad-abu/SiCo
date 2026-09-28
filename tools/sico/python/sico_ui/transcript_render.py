"""Role-aware native Qt transcript shared by live events and read-only replay."""

from __future__ import annotations

import time
from html import escape

from PyQt5.QtCore import QPoint, QPointF, Qt, QTimer, QUrl
from PyQt5.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPolygonF,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextImageFormat,
    QTextLength,
    QTextTableFormat,
)

from .code_highlight import table_safe_markdown
from .presentation import style_tables
from .reconcile_card import actions_html
from .theme import ACCENT, COPILOT, ERROR, MUTED, TEXT
from .tool_results import result_html


class SafeDocument(QTextDocument):
    def loadResource(self, resource_type, url):
        return None


# The counter's fold marker is drawn here: the field terminals have neither an
# emoji font nor a symbol font, so it cannot be a character.
TRIANGLE_COLLAPSED = "si-triangle:right"
TRIANGLE_EXPANDED = "si-triangle:down"
TRIANGLE_SIZE = (5, 7)
TRIANGLE_SCALE = 4
_triangles = {}


def triangle_image(direction, color=ACCENT):
    """Antialiased solid triangle, cached per direction and colour."""
    key = (direction, color, TRIANGLE_SIZE)
    image = _triangles.get(key)
    if image is None:
        width = TRIANGLE_SIZE[0] * TRIANGLE_SCALE
        height = TRIANGLE_SIZE[1] * TRIANGLE_SCALE
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        corners = ((0, 0), (width, height / 2), (0, height)) if direction == "right" else (
            (0, 0), (width, 0), (width / 2, height))
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in corners]))
        painter.end()
        _triangles[key] = image
    return image


class TranscriptRenderer:
    RENDER_ITEMS = 8
    RENDER_BUDGET = 0.008

    def _message_size(self, message):
        """Budget displayed content, not the payload behind an evidence link."""
        size = len(message["text"])
        if message["role"] == "context":
            # Context results render bounded inline tables rather than links.
            size += 24000
        elif message["role"] == "native":
            size += len(message["result"]["status"])
        elif message["role"] == "tools" and message.get("expanded"):
            size = len(self._tools_head(message)) + sum(
                len(line["text"]) for line in self._tool_lines(message, expanded=True))
        return size

    def _trim_document_prefix(self, cursor, signatures):
        """Evict old bubbles while retaining the already rendered overlap."""
        if not signatures or not self._rendered:
            return
        try:
            offset = self._rendered.index(signatures[0])
        except ValueError:
            return
        if not offset:
            return
        document = cursor.document()
        before = document.characterCount()
        cursor.setPosition(0)
        cursor.setPosition(self._positions[offset], QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        removed = before - document.characterCount()
        self._positions = [position - removed for position in self._positions[offset:]]
        self._rendered = self._rendered[offset:]

    def cancel_render(self):
        timer = getattr(self, "_render_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass  # The view can already have been destroyed.

    def _continue_render(self, view):
        owner = getattr(view, "_transcript_renderer", None)
        if owner is not self:
            if owner is not None:
                owner.cancel_render()
            view._transcript_renderer = self
        if not hasattr(self, "_render_timer"):
            self._render_timer = QTimer(view)
            self._render_timer.setSingleShot(True)
            self._render_timer.timeout.connect(lambda: self.render(view, force=True))
        self._render_timer.start(10)

    def render(self, view, *, force=False):
        view._transcript_rendering = True
        try:
            return self._render(view, force=force)
        finally:
            view._transcript_rendering = False

    def _render(self, view, *, force=False):
        owner = getattr(view, "_transcript_renderer", None)
        if owner is not self:
            if owner is not None:
                owner.cancel_render()
            view._transcript_renderer = self
        if not self.dirty and not force:
            return False
        now = time.monotonic()
        if not force and self.streaming is not None and now - self._last_render < 0.2:
            return False
        bar = view.verticalScrollBar()
        follow = bar.value() >= bar.maximum()
        anchor = view.cursorForPosition(QPoint(0, 0))
        anchor_top = view.cursorRect(anchor).top()
        document = view.document()
        remaining, visible, signatures = self.RENDER_CHARS, [], []
        for message in reversed(self.messages):
            if len(visible) >= self.RENDER_MESSAGES:
                break
            size = self._message_size(message)
            # Evict whole older bubbles so each delta cannot trim the first
            # visible bubble and invalidate the entire document prefix.
            if visible and size > remaining:
                break
            budget = max(1, remaining)
            original = message["text"]
            if len(original) > budget:
                # Keep the heading/question as well as the newest tail. A
                # clipped answer remains identifiable and its full body stays
                # available through the journal detail link.
                head = min(512, max(1, budget // 3))
                tail = max(1, budget - head - 1)
                text = original[:head] + "…" + original[-tail:]
            else:
                text = original
            text += message.get("suffix", "")
            visible.append({**message, "text": text})
            signatures.append((message["message_id"], message["revision"], len(text)))
            remaining -= size
        visible.reverse()
        signatures.reverse()
        if self._rendered and document.characterCount() <= 1:
            # The view was emptied behind our back (a failed link navigation,
            # for example); rebuild instead of trimming a stale prefix.
            self._document = None
        base_point = view.document().defaultFont().pointSize()
        if base_point <= 0:
            base_point = view.font().pointSize()
        if base_point <= 0:
            base_point = 10
        cursor = QTextCursor(document)
        # One document transaction per frame: deletion and replacement must not
        # publish an empty tail or clamp the scrollbar between the two steps.
        cursor.beginEditBlock()
        try:
            common, count = self._render_frame(cursor, visible, signatures, base_point)
        finally:
            cursor.endEditBlock()
        if follow:
            bar.setValue(bar.maximum())
        else:
            # QTextCursor follows retained text through prefix eviction. Keep
            # the reader's line at its previous viewport offset, not old pixels.
            bar.setValue(bar.value() + view.cursorRect(anchor).top() - anchor_top)
        self._document = document
        self._rendered = signatures[:common + count]
        self.dirty = common + count < len(signatures)
        if not self.dirty:
            self._last_render = now
        if self.dirty:
            self._continue_render(view)
        else:
            self.cancel_render()
        return True

    def _render_frame(self, cursor, visible, signatures, base_point):
        document = cursor.document()
        common = 0
        if document is self._document:
            self._trim_document_prefix(cursor, signatures)
            for old, new in zip(self._rendered, signatures):
                if old != new:
                    break
                common += 1
            if self._replace_changed(cursor, visible, signatures, common, base_point):
                style_tables(document, nested=True)
                return len(signatures), 0
        if not common:
            # Keep the edit transaction (QTextDocument.clear() would reset it).
            cursor.select(QTextCursor.Document)
            cursor.removeSelectedText()
            document.setDocumentMargin(16)
            root = document.rootFrame()
            root_format = root.frameFormat()
            # Qt 5 offsets right-aligned tables by the root's left margin again.
            root_format.setRightMargin(root_format.leftMargin() + document.documentMargin())
            root.setFrameFormat(root_format)
            self._positions = []
        else:
            # Preserve completed bubbles and replace only the changed suffix.
            start = (self._positions[common] if common < len(self._positions)
                     else document.characterCount() - 1)
            cursor.setPosition(start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            self._positions = self._positions[:common]
        count, deadline = 0, time.monotonic() + self.RENDER_BUDGET
        for message in visible[common:]:
            self._positions.append(cursor.position())
            cursor.beginEditBlock()
            self._insert(cursor, message, self.username, read_only=self.read_only,
                         base_point=base_point)
            cursor.endEditBlock()
            count += 1
            if count >= self.RENDER_ITEMS or time.monotonic() >= deadline:
                break
        # 气泡里的内容表格（markdown/工具结果）统一灰边；气泡本身保留自己的边框。
        style_tables(document, nested=True)
        return common, count

    def _replace_changed(self, cursor, visible, signatures, common, base_point):
        """Update a bubble/counter without deleting the reader's unchanged suffix."""
        if (common == len(signatures) or len(self._rendered) != len(signatures)
                or any(old[0] != new[0] for old, new in zip(self._rendered, signatures))):
            return False
        end = len(signatures)
        while end > common and self._rendered[end - 1] == signatures[end - 1]:
            end -= 1
        if end - common > self.RENDER_ITEMS:
            return False
        document = cursor.document()
        before = document.characterCount()
        cursor.setPosition(self._positions[common])
        cursor.setPosition(self._positions[end] if end < len(self._positions)
                           else document.characterCount() - 1, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        positions = []
        for message in visible[common:end]:
            positions.append(cursor.position())
            self._insert(cursor, message, self.username, read_only=self.read_only,
                         base_point=base_point)
        added = document.characterCount() - before
        self._positions = (self._positions[:common] + positions
                           + [p + added for p in self._positions[end:]])
        return True

    def _insert(self, cursor, message, username="你", *, read_only=False, base_point=10):
        role = message["role"]
        user = role == "user"
        if role == "native":
            result = message["result"]
            cursor.insertHtml("<a style='color: " + ACCENT + "' href='"
                              + escape(result["url"], quote=True) + "'>"
                              + escape(message["text"]) + "</a> · " + escape(result["status"]))
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
            return
        if role == "tools":
            # The counter is plain transcript text: no bubble, no table border.
            self._insert_tools(cursor, message, base_point=base_point)
            spacer = QTextBlockFormat()
            spacer.setLineHeight(100, QTextBlockFormat.ProportionalHeight)
            cursor.insertBlock(spacer, QTextCharFormat())
            cursor.insertBlock(spacer, QTextCharFormat())
            return
        table_format = QTextTableFormat()
        table_format.setWidth(QTextLength(QTextLength.PercentageLength, 90))
        table_format.setAlignment(Qt.AlignRight if user else Qt.AlignLeft)
        table_format.setBorder(1)
        table_format.setBorderStyle(QTextTableFormat.BorderStyle_Solid)
        table_format.setBorderBrush(QColor(ACCENT if user else COPILOT))
        table_format.setCellPadding(8)
        table_format.setCellSpacing(0)
        table = cursor.insertTable(1, 1, table_format)
        cell_cursor = table.cellAt(0, 0).firstCursorPosition()
        cell_block = QTextBlockFormat()
        cell_block.setAlignment(Qt.AlignLeft)
        cell_block.setTopMargin(0)
        cell_block.setBottomMargin(0)
        cell_cursor.setBlockFormat(cell_block)
        label = QTextCharFormat()
        label.setForeground(QColor(ACCENT if user else TEXT))
        if role in {"assistant", "activity", "result", "audit"}:
            label.setForeground(QColor(COPILOT))
        label.setFontWeight(600)
        cell_cursor.insertText(
            {"user": username, "context": "当前设计上下文", "notice": "任务状态",
             "activity": "Silicon Copilot", "result": "Silicon Copilot",
             "report": "已发布报告", "reconcile": "待核对操作"}.get(
                role, "Silicon Copilot"
            ),
            label,
        )
        cell_cursor.insertBlock(cell_block, QTextCharFormat())
        if user or role in {"notice", "result", "audit", "reconcile"}:
            cell_cursor.insertText(message["text"])
            if role == "reconcile":
                cell_cursor.insertBlock()
                cell_cursor.insertHtml(actions_html(message["result"], read_only=read_only))
            if role == "audit" and message["result"].get("pending"):
                cell_cursor.insertBlock()
                cell_cursor.insertHtml("<a href='" + escape(message["result"]["url"], quote=True)
                                       + "'>" + ("查看问题" if read_only else "答复这些问题")
                                       + "</a>")
        elif role == "context":
            cell_cursor.insertHtml(result_html(message["result"], context=True))
        elif role == "report":
            result = message["result"]
            cell_cursor.insertHtml("<a href='" + escape(result["url"], quote=True) + "'>"
                                   + escape(message["text"]) + f" · v{result['version']}</a>")
        else:
            fragment = SafeDocument()
            fragment.setMarkdown(
                table_safe_markdown(message["text"]),
                QTextDocument.MarkdownFeatures(
                    QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML
                ),
            )
            cell_cursor.insertFragment(QTextDocumentFragment(fragment))
        # Keep the next message outside this table with two character-line heights of gap.
        cursor.setPosition(table.lastPosition() + 1)
        spacer = QTextBlockFormat()
        spacer.setLineHeight(100, QTextBlockFormat.ProportionalHeight)
        cursor.insertBlock(spacer, QTextCharFormat())
        cursor.insertBlock(spacer, QTextCharFormat())

    def _insert_tools(self, cursor, message, *, base_point=10):
        """Halved-font counter, red failures, and double-click data references."""
        size = max(7, base_point // 2)
        prefix = QTextCharFormat()
        prefix.setForeground(QColor(ACCENT))
        prefix.setFontWeight(600)
        prefix.setFontPointSize(size)
        body = QTextCharFormat()
        body.setForeground(QColor(MUTED))
        body.setFontPointSize(size)
        issue = QTextCharFormat(body)
        issue.setForeground(QColor(ERROR))

        def reference(base, href):
            """Plain-looking link: an underline would shout in a quiet counter."""
            fmt = QTextCharFormat(base)
            fmt.setAnchor(True)
            fmt.setAnchorHref(href)
            fmt.setFontUnderline(False)
            return fmt

        message_id = str(message["message_id"])
        summary = reference(prefix, "tools-head:" + message_id)
        head = reference(body, "tools-head:" + message_id)
        expanded = bool(message.get("expanded"))
        document = cursor.document()
        for name, direction in ((TRIANGLE_COLLAPSED, "right"), (TRIANGLE_EXPANDED, "down")):
            document.addResource(QTextDocument.ImageResource, QUrl(name),
                                 triangle_image(direction))
        toggle = QTextImageFormat()
        toggle.setName(TRIANGLE_EXPANDED if expanded else TRIANGLE_COLLAPSED)
        toggle.setWidth(TRIANGLE_SIZE[0])
        toggle.setHeight(TRIANGLE_SIZE[1])
        toggle.setAnchor(True)
        toggle.setAnchorHref("tools:" + message_id)
        block = QTextBlockFormat()
        block.setAlignment(Qt.AlignLeft)
        block.setTopMargin(0)
        block.setBottomMargin(0)
        detail_block = QTextBlockFormat(block)
        detail_block.setLeftMargin(14)
        # Messages are inserted into the empty spacer block of their predecessor.
        if cursor.block().length() > 1:
            cursor.insertBlock(block, QTextCharFormat())
        # The whole summary line is the double-click target for open issues.
        for index, (segment, is_issue) in enumerate(self._tools_head_parts(message)):
            if index:
                cursor.insertText(" · ", head)
            if is_issue:
                cursor.insertText(segment, reference(issue, head.anchorHref()))
            else:
                cursor.insertText(segment, summary if index == 0 else head)
        # 执行中显示逐个出现的小三角形；结束后只留最后一个作展开/收拢。
        marks = max(1, int(message.get("marks") or 0))
        cursor.insertText("  ", body)
        for index in range(marks):
            if index:
                cursor.insertText(" ", head)
            cursor.insertImage(toggle)
        for line in self._tool_lines(message, expanded=expanded):
            cursor.insertBlock(detail_block, QTextCharFormat())
            plain = issue if line["issue"] else body
            cursor.insertText(line["text"], reference(plain, "tools-data:" + line["key"])
                              if line["key"] else plain)
