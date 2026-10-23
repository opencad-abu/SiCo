"""Conversation-bottom recap presentation and away/idle scheduling."""

import time
import uuid

from PyQt5.QtCore import Qt, QTimer, QRect, QSize
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from .glyphs import INPUT_GLYPH_SIZE, pen_icon
from .receipts import DataReceipt
from .theme import active_palette

AUTO_DELAY = 180
FRAME_WIDTH = 1
PADDING_X = 7
PADDING_Y = 4


class RecapPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.key = None
        self.snapshot = {}
        self.full_text = ""
        self.away_since = None
        self.idle_since = time.monotonic()
        self.attempted = None
        self.visits = {}
        self.return_due = False
        self.receipt = DataReceipt(self)
        self.label = QLabel()
        self.label.setObjectName("conversationRecapText")
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.label.setAccessibleName("会话纪要")
        self.label.setStyleSheet(
            "QLabel { border: %dpx solid %s; padding: %dpx %dpx; }" % (
                FRAME_WIDTH, active_palette().accent, PADDING_Y, PADDING_X))
        self.button = QToolButton()
        self.button.setIcon(pen_icon())
        self.button.setIconSize(QSize(INPUT_GLYPH_SIZE, INPUT_GLYPH_SIZE))
        self.button.setFixedSize(window.turn_composer.settings.size())
        self.button.setStyleSheet("QToolButton { padding: 0px; }")
        self.describe_action("生成纪要")
        self.button.clicked.connect(lambda _checked=False: self.request())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.button, 0, Qt.AlignTop)
        self.setObjectName("conversationRecap")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.hide()

    def scope(self):
        page = self.window.page
        task = (self.window.presentation.state or {}).get("task", {})
        return page.session, page.activation, page.reviewing, task.get("id", ""), task.get("status")

    def available(self):
        window = self.window
        state = window.presentation.state or {}
        return (not window.lifecycle.closing and window.page.reviewing is None
                and not state.get("busy") and not state.get("pending")
                and state.get("task", {}).get("status") in {"completed", "failed", "cancelled"})

    def tick(self):
        now = time.monotonic()
        window = self.window
        if window.lifecycle.closing:
            self.receipt.clear()
            self.timer.stop()
            return
        key = self.scope()
        away = (not window.isVisible() or not window.isActiveWindow()
                or window.tabs.currentWidget() != window.pages["conversation"]
                or window.page.reviewing is not None)
        if key != self.key:
            idle, absent = now, self.away_since
            if self.key is not None and key[:3] != self.key[:3]:
                if self.key[2] is None:
                    self.visits[self.key[0]] = (self.key[3:], self.idle_since,
                                               self.away_since or now)
                saved = self.visits.pop(key[0], None) if key[2] is None else None
                if saved is not None and saved[0] == key[3:]:
                    _, idle, absent = saved
                else:
                    absent = now if away else None
                if len(self.visits) > 128:
                    self.visits.pop(next(iter(self.visits)))
            self.receipt.clear()
            self.key, self.snapshot, self.attempted = key, {}, None
            self.idle_since, self.away_since = idle, absent
            self.return_due = False
            self.full_text = ""
            self.fit_text()
        if away and self.away_since is None:
            self.away_since = now
        eligible = (self.away_since is not None
                    and now - max(self.away_since, self.idle_since) >= AUTO_DELAY)
        if not away:
            self.return_due = self.return_due or eligible
            self.away_since = None
        if not self.available():
            self.hide()
            return
        if self.receipt.pending:
            return
        if not self.snapshot or self.snapshot.get("busy"):
            self.query()
        elif ((eligible or self.return_due) and self.snapshot.get("completed", 0) >= 3
              and self.snapshot.get("new_completed", 0) >= 2 and self.attempted != key):
            self.request(automatic=True)

    def query(self):
        self.dispatch("recap_status", {})

    def dispatch(self, operation, params, *, from_input=False):
        key = self.scope()
        try:
            future = self.window.api.command(self.window.page.session, operation, **params)
        except (ValueError, RuntimeError):
            self.failed(key)
            return

        def received(result):
            if self.window.lifecycle.closing or self.scope() != key:
                return
            self.snapshot = result
            if from_input and self.window.input.toPlainText().strip() == "/recap":
                self.window.input.clear()
            self.render()

        self.receipt.watch(future, received, lambda _error: self.failed(key))

    def failed(self, key):
        if self.scope() == key and not self.window.lifecycle.closing:
            self.snapshot = {"supported": True, "error": "纪要暂不可用，可点击刷新重试。"}
            self.render()

    def request(self, *, automatic=False, from_input=False):
        if not self.available() or self.receipt.pending:
            return
        if not self.can_control():
            return
        self.attempted = self.scope()
        self.return_due = False
        self.button.setEnabled(False)
        self.full_text = "正在生成纪要…"
        self.fit_text()
        self.dispatch("recap_start", {"expected_task": self.scope()[3],
                      "request_id": uuid.uuid4().hex, "automatic": automatic},
                      from_input=from_input)

    def render(self):
        value = self.snapshot
        self.setVisible(value.get("supported", False) and self.available())
        busy = value.get("busy", False)
        text = value.get("text") or value.get("error")
        self.full_text = "正在生成纪要…" if busy else text or ""
        self.fit_text()
        self.describe_action("正在生成纪要…" if busy else "刷新纪要" if text else "生成纪要")
        self.button.setEnabled(not busy and self.can_control())

    def describe_action(self, action):
        self.button.setAccessibleName(action)
        self.button.setToolTip(action + "：回顾本次对话的目标、进展和下一步（/recap）")

    def can_control(self):
        check = getattr(self.window.api, "can_control", None)
        return check is None or check(self.window.page.session)

    def fit_text(self):
        metrics = self.label.fontMetrics()
        width = max(40, self.label.width() - 2 * (FRAME_WIDTH + PADDING_X))
        maximum = metrics.lineSpacing() * 3

        def height(text):
            return metrics.boundingRect(QRect(0, 0, width, 10000), Qt.TextWordWrap, text).height()

        text = self.full_text
        if height(text) > maximum:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if height(text[:middle] + "…") <= maximum:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low] + "…"
        self.label.setText(text)
        self.label.setToolTip(self.full_text)
        field_height = (
            max(metrics.lineSpacing(), height(text)) + 2 * (FRAME_WIDTH + PADDING_Y) + 4)
        self.label.setFixedHeight(field_height)
        self.button.setFixedHeight(field_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_text()
