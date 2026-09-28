"""Asynchronous bidirectional history pages around the user's reading position."""

from copy import deepcopy

from sico.service.published import thaw
from PyQt5.QtCore import QEvent, QObject, Qt, QTimer
from PyQt5.QtWidgets import QLabel

from .receipts import DataReceipt
from .transcript import Transcript


class ChatScroll(QObject):
    CACHE_ROWS = 240
    CACHE_BYTES = 1048576
    CACHE_CHARS = 262144

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.receipt = DataReceipt(window)
        self.source = None
        self.history = None
        self.older = self.newer = False
        self.end = 0
        self.error = ''
        self.applying = False
        self.hint = QLabel(window.display.viewport())
        self.hint.setTextFormat(Qt.RichText)
        self.hint.setStyleSheet('background: #fff; padding: 4px; color: #666;')
        self.direction = 'before'
        self.hint.linkActivated.connect(lambda _url: self.load(self.direction))
        self.hint.hide()
        view = window.display
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().installEventFilter(self)
        view.installEventFilter(self)
        view.verticalScrollBar().valueChanged.connect(self.scrolled)
        window.page.invalidated.connect(self.reset)

    def reset(self):
        self.receipt.clear()
        if self.history is not None:
            self.history.cancel_render()
        self.history = self.source = None
        self.older = self.newer = False
        self.error = ''
        self.hint.hide()

    def delivery(self):
        window = self.window
        return (window.page_presentation.delivery if window.page.reviewing is not None
                else window.binding.delivery)

    def current(self):
        return self.window.presentation.current_transcript(self.window.page.reviewing)

    def shown(self):
        return self.history if self.history is not None else self.current()

    def eventFilter(self, _obj, event):
        if event.type() == QEvent.Wheel and event.angleDelta().y() > 0:
            self.freeze()
            QTimer.singleShot(0, self.scrolled)
        elif event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Up, Qt.Key_PageUp,
                                                               Qt.Key_Home):
            self.freeze()
            QTimer.singleShot(0, self.scrolled)
        return False

    def freeze(self):
        if self.history is not None or self.applying or self.window.page.opening:
            return
        source, delivery = self.current(), self.delivery()
        if source is None or delivery is None or not source._rendered:
            return
        keys = {row[0] for row in source._rendered}
        messages = [m for m in source.messages if m['message_id'] in keys]
        if len(messages) != len(keys):
            # A coalesced completion can replace the streamed row before its
            # render tick. Wait for that tick instead of adopting stale offsets.
            QTimer.singleShot(25, self.scrolled)
            return
        self.source = source
        self.history = Transcript(read_only=self.window.page.reviewing is not None)
        self.history.messages = deepcopy(messages)
        self.history._document = source._document
        self.history._positions = list(source._positions)
        self.history._rendered = list(source._rendered)
        self.history.RENDER_MESSAGES = self.CACHE_ROWS
        self.history.RENDER_CHARS = self.CACHE_CHARS
        self.history.dirty = False
        source.cancel_render()
        self.window.display._transcript_renderer = self.history
        self.older = bool(self.history.messages)
        self.newer = any(m['message_id'] > self.history.messages[-1]['message_id']
                         for m in source.messages)
        # Pin a consistent snapshot while the independent live tail keeps advancing.
        self.end = max(delivery.cursor.sequence, source._source_sequence)
        self.window.update_latest()

    def scrolled(self, *_args):
        if (self.applying or self.window.page.opening
                or getattr(self.window.display, '_transcript_rendering', False)):
            return
        bar = self.window.display.verticalScrollBar()
        if self.history is None and bar.value() < bar.maximum():
            self.freeze()
        if self.history is None:
            return
        if self.history.dirty:
            return  # A partial render is not a user scroll to its temporary end.
        if bar.value() <= 120 and self.older and not self.error:
            self.load('before')
        elif bar.maximum() - bar.value() <= 120 and not self.error:
            if self.newer:
                self.load('after')
        self.show_hint()

    def load(self, direction):
        self.freeze()
        if self.history is None or self.receipt.pending or not self.history.messages:
            return
        delivery = self.delivery()
        if delivery is None:
            return
        self.error = ''
        self.direction = direction
        history, source = self.history, self.source
        activation = self.window.page.activation
        rows = history.messages
        values = dict(before=rows[0]['message_id'] if direction == 'before' else 0,
                      after=rows[-1]['message_id'] if direction == 'after' else 0,
                      end=self.end)

        def current():
            return (self.history is history and self.source is source
                    and self.window.page.activation == activation
                    and self.current() is source)

        def received(result):
            if current():
                self.apply_page(result, direction, values)

        def failed(_exc):
            if current():
                self.error = '历史消息读取失败，<a href="retry">重试</a>'
                self.show_hint()

        try:
            future = self.window.api.chat_page(delivery.stream, **values)
            self.receipt.watch(future, received, failed)
            self.show_hint()
        except (ValueError, RuntimeError, OSError):
            failed(None)

    def apply_page(self, result, direction, request):
        from .chat_scroll_page import merge_page, validate_page

        validate_page(result, request)
        previous = tuple(row['message_id'] for row in self.history.messages)
        self.applying = True
        try:
            merge_page(self, thaw(result), direction)
        finally:
            self.applying = False
        self.show_hint()
        self.window.update_latest()
        # Short pages may leave the viewport near the same edge. Fetch another
        # only while more rows exist, with at most one outstanding receipt.
        if tuple(row['message_id'] for row in self.history.messages) != previous:
            QTimer.singleShot(0, self.scrolled)

    def show_hint(self):
        bar = self.window.display.verticalScrollBar()
        text = self.error
        if self.receipt.pending:
            text = '正在加载历史消息…'
        elif not text and not self.older:
            text = '已到会话开始'
        self.hint.setText(text)
        self.hint.adjustSize()
        self.hint.move(6, 3)
        near_edge = bar.value() <= 120 or (self.direction == 'after'
                                          and bar.maximum() - bar.value() <= 120)
        self.hint.setVisible(bool(text) and near_edge)
        self.hint.raise_()

    def render(self, *, force=False):
        self.applying = True
        try:
            self.shown().render(self.window.display, force=force)
        finally:
            self.applying = False
        self.window.update_latest()

    def has_updates(self):
        source = self.current()
        return (self.history is not None and source is self.source
                and source._source_sequence > self.end)

    def latest(self):
        if self.history is None:
            return False
        self.reset()
        self.applying = True
        try:
            source = self.current()
            source._document = None
            source.render(self.window.display, force=True)
            bar = self.window.display.verticalScrollBar()
            bar.setValue(bar.maximum())
        finally:
            self.applying = False
        self.window.update_latest()
        return True
