"""Typed MCP interaction editor and asynchronous, activation-bound host actions."""

import json
import uuid

from PyQt5.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from sico.codex.elicitation_schema import options, response, web_url
from sico.core.contracts import json_copy

from .input import SendOnReturnLineEdit


class InteractionEditors(QStackedWidget):
    """A hidden legacy editor must not impose its width on a narrow MCP form."""

    def minimumSizeHint(self):
        current = self.currentWidget()
        if isinstance(current, ElicitationForm):
            return QSize(0, current.minimumSizeHint().height())
        return current.minimumSizeHint() if current else QSize(0, 0)

    def hasHeightForWidth(self):
        return bool(self.currentWidget() and self.currentWidget().hasHeightForWidth())

    def heightForWidth(self, width):
        current = self.currentWidget()
        return current.heightForWidth(width) if current else -1


class WrappedLabel(QLabel):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(max(self.fontMetrics().height(), self.heightForWidth(self.width())))


def wrapped_label(value):
    label = WrappedLabel(value)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    return label


class ElicitationForm(QWidget):
    requested = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignTop)
        self.row, self.fields = None, {}
        self.buttons = []

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.layout.totalHeightForWidth(width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumHeight(max(self.layout.minimumSize().height(),
                                  self.heightForWidth(self.width())))

    def label(self, value):
        label = wrapped_label(value)
        self.layout.addWidget(label)
        return label

    def show_row(self, row):
        if self.row and self.row["id"] == row["id"]:
            self.row = json_copy(row)
            return
        while self.layout.count():
            child = self.layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.row, self.fields, self.buttons = json_copy(row), {}, []
        spec = self.row["elicitation"]
        self.label(row["title"])
        self.label(spec["message"])
        if spec["mode"] == "url":
            self.label(web_url(spec["url"]))
            self.button("打开网页", "open")
        else:
            required = set(spec["schema"].get("required") or [])
            for key, field in spec["schema"]["properties"].items():
                self.field(key, field, key in required)
        self.validation = self.label("")
        self.validation.hide()
        accept = self.button("提交答复" if spec["mode"] == "form" else "已完成，继续", "accept")
        # 主操作（提交答复/打开网页）和发送按钮同一强调色填充（bottomAction）。
        accept.setProperty("bottomAction", True)
        self.button("拒绝", "decline")
        self.button("取消本次交互", "cancel")

    def button(self, title, action):
        button = QPushButton(title)
        button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        button.setToolTip(title)
        button.clicked.connect(lambda _checked=False: self.submit(action))
        self.layout.addWidget(button)
        self.buttons.append(button)
        return button

    def field(self, key, field, required):
        title = field.get("title") or key
        self.label(title + ("（必填）" if required else "（可选）"))
        if field.get("description"):
            self.label(field["description"])
        include = None
        if not required:
            include = QCheckBox("提供此字段")
            include.setChecked(field.get("default") is not None)
            self.layout.addWidget(include)
        kind, default = field["type"], field.get("default")
        if kind == "boolean" or "enum" in field or "oneOf" in field:
            widget = QComboBox()
            widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(8)
            widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            widget.addItem("请选择…", None)
            values = [(True, "是"), (False, "否")] if kind == "boolean" else options(field)
            for value, label in values:
                widget.addItem(label, value)
                if default is not None and value == default:
                    widget.setCurrentIndex(widget.count() - 1)
            self.layout.addWidget(widget)
        elif kind == "array":
            widget = QWidget()
            layout = QVBoxLayout(widget)
            for value, label in options(field):
                line = QWidget()
                choices = QHBoxLayout(line)
                choices.setContentsMargins(0, 0, 0, 0)
                choice = QCheckBox()
                choice.setAccessibleName(label)
                choice.setToolTip(label)
                choice.setProperty("value", value)
                choice.setChecked(value in (default or []))
                caption = wrapped_label(label)
                caption.setBuddy(choice)
                choices.addWidget(choice, 0, Qt.AlignTop)
                choices.addWidget(caption, 1)
                layout.addWidget(line)
            self.layout.addWidget(widget)
        else:
            widget = SendOnReturnLineEdit()
            widget.setMaxLength(4000)
            if default is not None:
                widget.setText(str(default))
            widget.submitted.connect(self.accept_on_return)
            if kind in {"number", "integer"}:
                widget.setPlaceholderText("整数" if kind == "integer" else "数值")
            self.layout.addWidget(widget)
        if include is not None:
            widget.setEnabled(include.isChecked())
            include.toggled.connect(widget.setEnabled)
        self.fields[key] = (field, include, widget)

    def values(self):
        values = {}
        for key, (field, include, widget) in self.fields.items():
            if include is not None and not include.isChecked():
                continue
            kind = field["type"]
            if isinstance(widget, QComboBox):
                value = widget.currentData()
                if value is None:
                    raise ValueError("请选择：" + (field.get("title") or key))
            elif kind == "array":
                value = [c.property("value") for c in widget.findChildren(QCheckBox)
                         if c.isChecked()]
            else:
                value = widget.text()
                if kind in {"integer", "number"}:
                    try:
                        value = json.loads(value)
                    except ValueError:
                        raise ValueError("请输入有效数值：" + (field.get("title") or key)) from None
            values[key] = value
        return values

    def submit(self, action):
        if not self.row or self.row["status"] != "pending" or not self.isEnabled():
            return
        try:
            result = None
            if action != "open":
                value = {"action": action}
                if action == "accept" and self.row["elicitation"]["mode"] == "form":
                    value["content"] = self.values()
                result = response(self.row["elicitation"], value)
            self.validation.hide()
            self.requested.emit(json_copy(self.row), result)
        except (ValueError, TypeError, OverflowError) as exc:
            self.error(str(exc))

    def accept_on_return(self):
        """回车等同于点击“提交答复”，输入法预编辑已由控件先行上屏。"""
        self.submit("accept")

    def error(self, message):
        self.validation.setText(message)
        self.validation.show()


class ElicitationActions:
    def __init__(self, window):
        self.window, self.pending = window, set()
        window.centers.audit.elicitationRequested.connect(self.request)

    def request(self, row, result):
        window = self.window
        if (window.page.reviewing is not None or window.page.opening or window.lifecycle.closing
                or window.presentation.state is None or window.binding.failed
                or window.presentation.state.get("cancelling") or window.presentation.state.get("closing")):
            return
        controller, activation = window.page.session, window.page.activation
        if row["task_id"] != window.presentation.state["task"].get("id"):
            return
        key = (controller.runtime_id, row["id"])
        if key in self.pending:
            return
        scope = {"session_id": controller.session_id, "runtime_id": controller.runtime_id,
                 "task_id": row["task_id"], "context": row["context"], "binding": row["binding"]}
        self.pending.add(key)

        def visible():
            return (window.page.session == controller and activation == window.page.activation
                    and window.page.reviewing is None and not window.page.opening
                    and not window.lifecycle.closing)

        def current():
            live = window.centers.audit.audit_rows.get(row["id"], {})
            state = window.presentation.state or {}
            return (visible() and not window.binding.failed
                    and state.get("busy") and not state.get("cancelling")
                    and not state.get("closing") and not state.get("fault")
                    and state.get("task", {}).get("id") == row["task_id"]
                    and live.get("status") == "pending"
                    and json_copy(live.get("binding")) == row["binding"])

        def failed(exc):
            if visible():
                window.centers.audit.elicitation_form.error(str(exc))
                window.info_app.error("交互未提交", str(exc))
            return False

        def done(value):
            if not visible():
                return
            if result is None:
                if not current():
                    return
                if not QDesktopServices.openUrl(QUrl(web_url(value))):
                    failed(ValueError("网页未能打开，可核对链接后重新点击"))
            else:
                window.status.setText("答复已提交，等待交付")
                window.binding.poll()

        window.run_command("elicitation", row["id"], controller=controller, scope=scope,
                           response=result, reply_id=uuid.uuid4().hex, success=done, failure=failed,
                           settled=lambda _result, _error: self.pending.discard(key))
