"""Compact workbench indexes and passive central report/evidence navigation."""

from __future__ import annotations

import re
from html import unescape

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cadgui.fonts import mono_font

from .code_highlight import code_block_html, guess_language
from .glyphs import ACTION_GLYPH_SIZE, back_icon, refresh_icon
from .presentation import (
    DocumentView,
    insert_code_block,
    insert_markdown_segments,
    style_document,
)
from .preview_gallery import DocumentImages, insert_images, preview_artifact_name
from .receipts import DataReceipt
from .workbench_list import ObjectList as ObjectList
from .workbench_navigation import DetailNavigation


class WorkbenchDetail(DetailNavigation, QWidget):
    returnRequested = pyqtSignal()
    objectShown = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.index = None
        self.current = None
        self.history = []
        self.external_urls = set()
        self.image_sizes = {}
        self.review = None
        self._detail_receipt = DataReceipt(self)
        self._detail_generation = 0
        self._active = True
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.back = QToolButton()
        self.back.setIcon(back_icon(ACTION_GLYPH_SIZE))
        self.back.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
        self.back.setToolTip("返回上一页")
        self.back.clicked.connect(lambda _checked=False: self.go_back())
        header.addWidget(self.back)
        self.heading = QLabel()
        self.heading.setTextFormat(Qt.PlainText)
        self.heading.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.heading.setFixedHeight(self.fontMetrics().height() + 6)
        header.addWidget(self.heading, 1)
        self.images_gallery = DocumentImages(self)
        header.addWidget(self.images_gallery.button)
        self.refresh = QToolButton()
        self.refresh.setIcon(refresh_icon(ACTION_GLYPH_SIZE))
        self.refresh.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
        self.refresh.setToolTip("重新核验来源数据")
        self.refresh.clicked.connect(lambda _checked=False: self.reload())
        header.addWidget(self.refresh)
        layout.addLayout(header)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.review_bar = QWidget()
        review_layout = QHBoxLayout(self.review_bar)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.setSpacing(6)
        review_layout.addWidget(QLabel("分页审阅"))
        self.review_pages = QComboBox()
        self.review_pages.setMinimumWidth(180)
        review_layout.addWidget(self.review_pages)
        self.review_search = QLineEdit()
        self.review_search.setPlaceholderText("搜索文件、字段或内容")
        review_layout.addWidget(self.review_search, 1)
        self.review_prev = QToolButton()
        self.review_prev.setText("上一页")
        self.review_prev.setToolTip("上一页审阅")
        review_layout.addWidget(self.review_prev)
        self.review_next = QToolButton()
        self.review_next.setText("下一页")
        self.review_next.setToolTip("下一页审阅")
        review_layout.addWidget(self.review_next)
        self.review_status = QLabel()
        self.review_status.setTextFormat(Qt.PlainText)
        review_layout.addWidget(self.review_status)
        self.review_bar.hide()
        layout.addWidget(self.review_bar)
        self.document = DocumentView()
        self.document.setOpenLinks(False)
        self.document.setOpenExternalLinks(False)
        self.document.anchorClicked.connect(lambda url: self.open_url(url.toString()))
        # 输出独立成区：上文档、下只读文本；没有输出时整块隐藏、文档占满。
        self.output_header = QLabel("输出")
        self.output_header.setObjectName("sessionSection")
        self.output_header.setTextFormat(Qt.PlainText)
        self.output_header.setContentsMargins(8, 5, 8, 3)
        self.output = QPlainTextEdit()
        self.output.setObjectName("detailOutput")
        self.output.setReadOnly(True)
        self.output.setFont(mono_font())
        self.output.setPlaceholderText("暂无输出")
        self.output_area = QWidget()
        area_layout = QVBoxLayout(self.output_area)
        area_layout.setContentsMargins(0, 0, 0, 0)
        area_layout.setSpacing(0)
        area_layout.addWidget(self.output_header)
        area_layout.addWidget(self.output, 1)
        self.output_area.setVisible(False)
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setObjectName("detailSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.document)
        self.splitter.addWidget(self.output_area)
        # 默认 2:1（输出首次出现时分配），之后由用户拖动决定。
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)
        self._split_ready = False
        layout.addWidget(self.splitter, 1)
        self.process_timer = QTimer(self)
        self.process_timer.setInterval(1000)
        self.process_timer.timeout.connect(self.refresh_process)
        self.process_timer.start()
        self._process_html = None
        self._review_result = None
        self._review_index = 0
        self.review_pages.currentIndexChanged.connect(self.show_review_index)
        self.review_search.textChanged.connect(self.search_review)
        self.review_prev.clicked.connect(lambda _checked=False: self.step_review(-1))
        self.review_next.clicked.connect(lambda _checked=False: self.step_review(1))

    def refresh_process(self):
        if (not self._active or not self.isVisible() or not self.current
                or self.current[0] != "process" or self._detail_receipt.pending
                or not self.index.source.live_processes
                or self.document.textCursor().hasSelection()):
            return
        source, current, generation = self.index.source, self.current, self._detail_generation

        def ready(result):
            if (self._active and self.current == current and self.index.source is source
                    and generation == self._detail_generation):
                source.validate_current()
                if self._process_signature(result) == self._process_html:
                    self.show_notice(result.get("notice", ""))
                    return
                bar = self.document.verticalScrollBar()
                follow = bar.maximum() > 0 and bar.value() >= bar.maximum() - 4
                self._render_detail(generation, result, bar.value())
                if follow:
                    bar.setValue(bar.maximum())

        def failed(_exc):
            if (self._active and self.current == current and self.index.source is source
                    and generation == self._detail_generation):
                self.show_notice("进程数据暂不可读，稍后自动重试。")

        try:
            self._detail_receipt.watch(source.detail(*current), ready, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    @staticmethod
    def _process_signature(result):
        """进程刷新去重：结构化字段（输出/命令/字段表）任一变化都要重绘。"""

        return (str(result.get("html") or ""), str(result.get("prefix") or ""),
                str(result.get("script") or ""), len(str(result.get("output") or "")),
                bool(result.get("output_truncated")), str(result.get("notice") or ""))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_labels()
        self.fit_images()

    def fit_images(self):
        block = self.document.document().begin()
        while block.isValid():
            fragment = block.begin()
            while not fragment.atEnd():
                value = fragment.fragment()
                fmt = value.charFormat()
                if fmt.isImageFormat():
                    image = fmt.toImageFormat()
                    original = self.image_sizes.get(image.name())
                    if original is not None:
                        shown = self.image_size(original)
                        image.setWidth(shown.width())
                        image.setHeight(shown.height())
                        cursor = QTextCursor(self.document.document())
                        cursor.setPosition(value.position())
                        cursor.setPosition(
                            value.position() + value.length(), QTextCursor.KeepAnchor,
                        )
                        cursor.setCharFormat(image)
                fragment += 1
            block = block.next()

    def image_size(self, original):
        width = min(original.width(), 640, max(1, self.document.viewport().width() - 48))
        return original.scaled(width, min(original.height(), 480), Qt.KeepAspectRatio)

    def closeEvent(self, event):
        self.invalidate()
        self._detail_receipt.clear()
        self.document.set_busy(False)
        self.output.clear()
        super().closeEvent(event)

    def fit_labels(self):
        self.heading.setText(
            self.heading.fontMetrics().elidedText(
                self.heading.toolTip(),
                Qt.ElideRight,
                max(1, self.heading.width()),
            )
        )
        if self.notice.text():
            self.notice.setFixedHeight(self.notice.heightForWidth(max(1, self.document.width())))

    def show_notice(self, text):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))
        self.fit_labels()

    # -- 代码块与输出 -----------------------------------------------------

    # 旧进程详情把输出拍平在 html 里：过渡期从该小节兜底提取。
    _OUTPUT_SECTION = re.compile(
        r"<h3>\s*输出\s*</h3>\s*(?:<pre[^>]*>(?P<body>.*?)</pre>|<p>(?P<empty>[^<]*)</p>)",
        re.DOTALL,
    )
    _SCRIPT_SECTION = re.compile(
        r"(<h3>\s*启动命令\s*</h3>\s*)<pre[^>]*>(?P<body>.*?)</pre>", re.DOTALL,
    )

    def _take_output(self, result):
        """拆出输出文本；返回 (去掉输出小节的结果, 输出文本)。"""

        result = dict(result)
        output = result.get("output")
        html = result.get("html")
        if isinstance(html, str):
            stripped, extracted = self._strip_output_section(html)
            if stripped != html:
                result["html"] = stripped
            if not (isinstance(output, str) and output.strip()):
                output = extracted
        return result, output if isinstance(output, str) else ""

    def _strip_output_section(self, html):
        match = self._OUTPUT_SECTION.search(html)
        if match is None:
            return html, ""
        body = match.group("body")
        if body is None:
            # “暂无已捕获的输出。”这类占位段落留在正文里，不进输出区。
            return html, ""
        text = unescape(re.sub(r"<[^>]+>", "", body))
        return html[: match.start()] + html[match.end():], text

    def _highlight_script_section(self, html):
        """html 详情里的“启动命令”小节顺带按识别结果着色。"""

        match = self._SCRIPT_SECTION.search(html)
        if match is None:
            return html
        script = unescape(re.sub(r"<[^>]+>", "", match.group("body")))
        if not script.strip():
            return html
        block = code_block_html(script, guess_language(script))
        return html[: match.start()] + match.group(1) + block + html[match.end():]

    @staticmethod
    def _insert_script_block(cursor, script, language):
        cursor.insertHtml("<h3>启动命令</h3>")
        insert_code_block(cursor, script, language)

    def _set_output(self, text, *, position=None):
        """刷新输出区：仅有输出时显示，跟随滚动但尊重用户的上滚。"""

        text = str(text or "")
        visible = bool(text.strip())
        bar = self.output.verticalScrollBar()
        follow = None if position is None else position
        if follow is None:
            follow = (not self.output.isVisible()) or bar.maximum() == 0 \
                or bar.value() >= bar.maximum() - 4
        if self.output.toPlainText() != text:
            keep = bar.value()
            self.output.setPlainText(text)
            bar.setValue(bar.maximum() if follow else min(keep, bar.maximum()))
        self.output_area.setVisible(visible)
        if visible and not self._split_ready:
            self._split_ready = True
            total = self.splitter.height()
            if total > 1:
                upper = max(1, total * 2 // 3)
                self.splitter.setSizes([upper, max(1, total - upper)])

    def bind(self, index):
        self._active = True
        if self.index is None or self.index.source is not index.source:
            self._detail_receipt.clear()
            self._detail_generation += 1
            self.index, self.current, self.history = index, None, []
            self.external_urls.clear()
            self.image_sizes.clear()
            self.review = None
            self.review_bar.hide()
            self.images_gallery.clear()
            self.document.set_busy(False)
            self.document.clear()
            self._set_output("")
            self.heading.clear()
            self.heading.setToolTip("")
            self.show_notice("")
        else:
            self.index = index

    def _render_detail(self, generation, result, position=0):
        if generation != self._detail_generation or self.current is None:
            return
        result, output = self._take_output(result)
        self._process_html = (self._process_signature(result)
                              if self.current[0] == "process" else None)
        self.document.set_busy(False)
        self._review_source = result
        self._configure_review(result.get("review"))
        self.review = result.get("review")
        self._set_output(output)
        self.heading.setToolTip(result.get("title", ""))
        self.fit_labels()
        self.show_notice(result.get("notice", ""))
        self.external_urls = set(result.get("external_urls", []))
        script = str(result.get("script") or "")
        language = str(result.get("script_language") or "") or guess_language(script)
        if "markdown" in result:
            self.document.clear()
            cursor = QTextCursor(self.document.document())
            cursor.insertHtml(result.get("prefix", ""))
            if script:
                self._insert_script_block(cursor, script, language)
            cursor.insertBlock()
            insert_markdown_segments(cursor, result.get("markdown", ""))
            cursor.insertBlock()
            cursor.insertHtml(result.get("suffix", ""))
        else:
            # prefix 承载结构化字段表（process 详情 M3 起走这里），html/suffix 兼容旧负载。
            html = (result.get("prefix", "")
                    + self._highlight_script_section(result.get("html", ""))
                    + result.get("suffix", ""))
            self.document.clear()
            cursor = QTextCursor(self.document.document())
            cursor.insertHtml(html)
            if script:
                self._insert_script_block(cursor, script, language)
        # 正文渲染完再统一表格边框：markdown 与 html 两个分支都覆盖。
        style_document(self.document.document())
        self.image_sizes.clear()
        images = result.get("images", [])
        self.image_sizes, image_notice = insert_images(self.document.document(), images)
        self.fit_images()
        self.images_gallery.refresh(images, *preview_artifact_name(result))
        if image_notice:
            self.show_notice(image_notice)
        cursor = self.document.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.document.setTextCursor(cursor)
        self.document.verticalScrollBar().setValue(position)

    def _configure_review(self, review):
        if not isinstance(review, dict) or review.get("page_count", 0) <= 1:
            self.review_bar.hide()
            self._review_source = None
            self._review_result = None
            self._review_index = 0
            return
        self._review_result = review
        self._review_index = 0
        self.review_pages.blockSignals(True)
        self.review_pages.clear()
        for page in review.get("pages", []):
            files = ", ".join(page.get("files", []))
            self.review_pages.addItem(
                f"第 {page['page']}/{review['page_count']} 页 · {files}"
            )
        self.review_pages.setCurrentIndex(0)
        self.review_pages.blockSignals(False)
        self.review_search.clear()
        self.review_bar.show()
        self._update_review_controls()

    def _update_review_controls(self):
        count = self.review_pages.count()
        self.review_prev.setEnabled(self._review_index > 0)
        self.review_next.setEnabled(self._review_index + 1 < count)
        self.review_status.setText(
            f"第 {self._review_index + 1}/{count} 页" if count else ""
        )

    def show_review_index(self, index):
        review = self._review_result
        if (not isinstance(review, dict) or not isinstance(self._review_source, dict)
                or not 0 <= index < len(review.get("pages", []))):
            return
        self._review_index = index
        page = review["pages"][index]
        result = dict(self._review_source)
        result["markdown"] = page["markdown"]
        self._render_review_document(result)
        self._update_review_controls()

    def step_review(self, delta):
        target = self._review_index + delta
        if 0 <= target < self.review_pages.count():
            self.review_pages.setCurrentIndex(target)

    def search_review(self, text):
        query = str(text).strip().lower()
        if not query or not isinstance(self._review_result, dict):
            return
        for index, page in enumerate(self._review_result.get("pages", [])):
            searchable = (page.get("markdown", "") + " "
                          + " ".join(page.get("files", []))).lower()
            if query in searchable:
                self.review_pages.setCurrentIndex(index)
                return
        self.review_status.setText("无匹配审阅页")

    def _render_review_document(self, result):
        self.document.clear()
        cursor = QTextCursor(self.document.document())
        cursor.insertHtml(result.get("prefix", ""))
        cursor.insertBlock()
        insert_markdown_segments(cursor, result.get("markdown", ""))
        cursor.insertBlock()
        cursor.insertHtml(result.get("suffix", ""))
        style_document(self.document.document())
        cursor = self.document.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.document.setTextCursor(cursor)
        self.document.verticalScrollBar().setValue(0)

    def reload(self):
        if self.current:
            position = self.document.verticalScrollBar().value()
            self.open(*self.current, remember=False, position=position)

    def restyle(self):
        """Re-fit labels and redraw the current detail in the active appearance."""
        self.heading.setFixedHeight(self.fontMetrics().height() + 6)
        self.fit_images()
        self.reload()

    def go_back(self):
        if self.history:
            kind, key, position = self.history.pop()
            self.open(kind, key, remember=False, position=position)
        else:
            self._detail_receipt.clear()
            self._detail_generation += 1
            self.current = None
            self.returnRequested.emit()
