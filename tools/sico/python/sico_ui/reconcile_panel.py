"""Native Qt controls for the current journal-derived reconciliation card."""

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class ReconcilePanel(QGroupBox):
    def __init__(self, window):
        super().__init__("待核对", window)
        self.window = window
        self.card = None
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.calls = QComboBox()
        self.calls.currentIndexChanged.connect(self.refresh_buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        self.reconnect = QPushButton("重试连接项目服务…")
        self.reconnect.clicked.connect(self.retry_connection)
        layout.addWidget(self.reconnect)
        layout.addWidget(self.calls)
        buttons = QHBoxLayout()
        self.buttons = {}
        for action, label in (("query", "查询原请求"), ("completed", "已完成…"),
                              ("not_executed", "未执行…"),
                              ("no_calls", "结束中断任务并继续（不重放）"),
                              ("uncertain", "仍不确定，保持暂停")):
            button = QPushButton(label)
            button.setProperty("secondaryAction", True)
            button.setObjectName("reconcile_" + action)
            button.clicked.connect(lambda _checked=False, action=action: self.decide(action))
            buttons.addWidget(button)
            self.buttons[action] = button
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)
        self.refresh()

    def refresh(self):
        window = self.window
        transcript = window.presentation.current_transcript(window.page.reviewing)
        if transcript is None:  # 回放尚未建好：本拍保留上一张卡片，等下一拍
            return
        cards = [row["result"] for row in transcript.messages
                 if row["role"] == "reconcile" and row["result"].get("active")]
        card = cards[-1] if cards else None
        if card != self.card:
            from copy import deepcopy

            selected = self.calls.currentData()
            self.card = deepcopy(card)
            self.calls.blockSignals(True)
            self.calls.clear()
            for row in (card or {}).get("operations", []):
                self.calls.addItem(row["id"] + " · " + row["name"]
                                   + ("（已核对）" if row.get("decision") else ""), row["id"])
            index = self.calls.findData(selected)
            if index >= 0:
                self.calls.setCurrentIndex(index)
            self.calls.blockSignals(False)
        self.setVisible(card is not None)
        self.refresh_buttons()

    def refresh_buttons(self, *_args):
        if self.card is None:
            return
        window = self.window
        connected = getattr(window.api, "connection_state", "connected") == "connected"
        readonly = window.reconcile_actions.read_only
        enabled = connected and not readonly and not window.page.opening
        controlled = getattr(window.api, "can_control", lambda _token: True)(window.page.session)
        rows = self.card["operations"]
        chosen = next((row for row in rows if row["id"] == self.calls.currentData()), {})
        self.calls.setVisible(bool(rows))
        hint = "请先查询原请求，再填写依据并确认结论；原调用不会重放。"
        if not connected:
            hint = "项目服务未连接，核对按钮已停用。请点击“重试连接项目服务…”，或使用 sico reconcile。"
        elif readonly:
            hint = "历史记录只读；请在左侧会话右键选择“继续”打开核对，记录会保留。"
        elif not controlled:
            hint = "当前可查询；提交决定前请在会话控制菜单获取控制权。"
        elif not rows:
            hint = "本轮没有待核对的已派发调用。点击下方按钮结束中断任务，决定会记入记录。"
        self.hint.setText(hint)
        self.reconnect.setVisible(not connected)
        for action, button in self.buttons.items():
            button.setVisible((not rows) if action == "no_calls" else
                              bool(rows) if action != "uncertain" else True)
            # Repeating the same durable decision retries cleanup if its receipt failed.
            # Disabling both decisions here strands an already checked task forever.
            button.setEnabled(enabled and (action == "query" or controlled)
                              and not (action in {"completed", "not_executed"}
                                       and chosen.get("decision") not in {None, action}))
            button.setToolTip(hint)

    def retry_connection(self):
        connection = getattr(self.window, "service_connection", None)
        if connection is not None:
            connection.reconnect()
        else:
            self.window.info_app.warning("项目服务未连接", "请重新打开 SiCo 窗口后核对，原调用不会重放")

    def decide(self, action):
        card = self.card
        if card is not None:
            call_id = (self.calls.currentData()
                       if action not in {"no_calls", "uncertain"} else "")
            operation = next((row for row in card.get("operations", [])
                              if row.get("id") == call_id), None)
            self.window.reconcile_actions.decide(
                card["session_id"], card["task_id"], action, call_id, operation=operation)
