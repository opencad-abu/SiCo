"""Independent attachment, model, reasoning and settings controls for a draft."""

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget
from sico.codex.access_policy import MODES, SCOPE_NOTE, default_access, validate

from .context_usage import ContextUsage
from .glyphs import INPUT_GLYPH_SIZE, gear_icon, paperclip_icon
from .receipt_scope import ReceiptScope
from .receipts import DataReceipt
from .turn_attachments import AttachmentDialog
from .turn_draft_options import draft_options, model_row, store_options
from .turn_settings import TurnSettingsDialog


class TurnComposer(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.catalog = None
        self._key = None
        self._requested = False
        self._rendered = None
        self.receipt = DataReceipt(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.attachments = QToolButton()
        self.attachments.setIcon(paperclip_icon())
        self.attachments.setIconSize(QSize(INPUT_GLYPH_SIZE, INPUT_GLYPH_SIZE))
        self.attachments.setToolTip("添加图片、音频、技能或文件引用")
        self.attachments.setAccessibleName("附件")
        self.attachments.clicked.connect(self.open_attachments)
        layout.addWidget(self.attachments)
        self.access = self.combo("Codex 执行权限", 8)
        self.access.addItem("执行权限", None)
        self.access.setToolTip("Codex 执行权限\n" + SCOPE_NOTE)
        self.access.currentIndexChanged.connect(self.access_changed)
        layout.addWidget(self.access)
        self.caption = QLabel()
        self.caption.setTextFormat(Qt.PlainText)
        self.caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout.addWidget(self.caption, 1)
        self.model = self.combo("模型选择", 10)
        self.model.addItem("模型选择", None)
        self.model.currentIndexChanged.connect(self.model_changed)
        layout.addWidget(self.model)
        self.effort = self.combo("推理强度", 8)
        self.effort.addItem("推理强度", None)
        self.effort.currentIndexChanged.connect(self.effort_changed)
        layout.addWidget(self.effort)
        self.context_usage = ContextUsage(self)
        layout.addWidget(self.context_usage)
        self.settings = QToolButton()
        self.settings.setIcon(gear_icon())
        self.settings.setIconSize(QSize(INPUT_GLYPH_SIZE, INPUT_GLYPH_SIZE))
        self.settings.setToolTip("回合设置")
        self.settings.setAccessibleName("回合设置")
        self.settings.clicked.connect(self.open_settings)
        layout.addWidget(self.settings)
        for widget in (self.attachments, self.access, self.model, self.effort, self.settings):
            widget.setFixedHeight(30)
            widget.setEnabled(False)
        self.setFixedHeight(30)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)

    def align_actions(self, stop, latest, buttons):
        for widget in (self.context_usage, self.settings, stop, latest):
            widget.setFixedSize(self.height(), self.height())
        for widget in (self.settings, stop, latest):
            widget.setStyleSheet("QToolButton { padding: 0px; }")
        self.layout().setSpacing(6)
        buttons.setSpacing(6)
        # Transient resume/reconcile actions must not split the rightmost pair.
        buttons.removeWidget(stop)
        buttons.insertWidget(buttons.indexOf(latest), stop)

    def combo(self, title, length):
        widget = QComboBox()
        widget.setMinimumContentsLength(length)
        widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        widget.setToolTip(title)
        widget.setAccessibleName(title)
        return widget

    def scope_key(self):
        window = self.window
        state = window.presentation.state or {}
        return (ReceiptScope.capture(window.page, window.presentation.submission_context),
                state.get("busy", False), (state.get("task") or {}).get("id"))

    def available(self):
        available = getattr(self.window, "steering_input", None)
        return bool(available and available.available()
                    and (self.window.presentation.state or {}).get("steering_supported"))

    def refresh(self):
        state = self.window.presentation.state or {}
        current = self.window.page.reviewing is None and not self.window.page.opening
        self.context_usage.set_usage((state.get("task") or {}).get("context_usage")
                                     if current else None)
        key = self.scope_key()
        if key != self._key:
            self.receipt.clear()
            self._key, self._requested = key, False
            self.catalog, self._rendered = None, None
            for widget, title in ((self.access, "执行权限"), (self.model, "模型选择"),
                                  (self.effort, "推理强度")):
                widget.blockSignals(True)
                widget.clear()
                widget.addItem(title, None)
                widget.blockSignals(False)
        enabled = self.available()
        self.attachments.setEnabled(enabled)
        self.settings.setEnabled(enabled)
        self.model.setEnabled(enabled and self.catalog is not None)
        self.effort.setEnabled(enabled and self.catalog is not None)
        self.access.setEnabled(enabled and self.catalog is not None)
        if enabled and not self._requested:
            self.load_catalog()
        if self.catalog is not None:
            options = draft_options(self.window.input, self.catalog)
            if options != self._rendered:
                self.render_options(options)
        count = len(self.window.input.turn_inputs)
        text = f"{count} 项附件" if count else ""
        if self.window.input.text_attachment() is not None:
            text = " · ".join(filter(None, (text, "已附粘贴文本")))
        self.caption.setToolTip(text)
        self.caption.setText(self.caption.fontMetrics().elidedText(
            text, Qt.ElideRight, max(0, self.caption.width())))
        if self.window.lifecycle.closing:
            self.receipt.clear()
            self.timer.stop()

    def load_catalog(self):
        self._requested = True
        key = self._key

        def loaded(status):
            window = self.window
            if (key == self.scope_key() and window.page.reviewing is None
                    and key[0].current(window.page, window.presentation.submission_context,
                                       window.api, window.lifecycle.closing)):
                self.use_catalog(status)

        def failed(exc):
            if key == self.scope_key():
                self.model.setToolTip("模型清单读取失败；打开回合设置可重试：" + str(exc))
            return False

        try:
            self.receipt.watch(self.window.api.command(key[0].handle, "turn_input_status"),
                               loaded, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    def use_catalog(self, status):
        self.receipt.clear()
        self._key, self._requested = self.scope_key(), True
        self.catalog, self._rendered = status, None
        self.model.setToolTip("模型选择")
        self.refresh()

    def render_options(self, options):
        self._rendered = options
        self.model.blockSignals(True)
        self.effort.blockSignals(True)
        self.access.blockSignals(True)
        try:
            self.access.clear()
            for label, mode in MODES:
                self.access.addItem(label, mode)
            access = options.get("access", default_access())
            self.access.setCurrentIndex(self.access.findData(access["sandbox"]))
            names = [row["model"] for row in self.catalog["models"]]
            selected = options.get("model", "")
            if selected and selected not in names:
                names.insert(0, selected)
            self.model.clear()
            for name in names:
                self.model.addItem(name, name)
            if not names:
                self.model.addItem("模型选择", None)
            self.model.setCurrentIndex(max(0, self.model.findData(selected)))
            self.effort.clear()
            self.effort.addItem("模型默认", None)
            for row in model_row(self.catalog, options).get("supportedReasoningEfforts") or []:
                value = row["reasoningEffort"]
                self.effort.addItem(value, value)
            self.effort.setCurrentIndex(max(0, self.effort.findData(options.get("effort"))))
        finally:
            self.model.blockSignals(False)
            self.effort.blockSignals(False)
            self.access.blockSignals(False)

    def access_changed(self, _index=0):
        if (not self.available() or self.catalog is None or self._key != self.scope_key()
                or self.access.currentData() is None):
            return
        options = draft_options(self.window.input, self.catalog)
        access = options.get("access", default_access())
        if access["sandbox"] == "danger-full-access":
            access["network"] = False
        access["sandbox"] = self.access.currentData()
        options["access"] = validate(access)
        store_options(self.window.input, options, self.catalog)
        self.refresh()

    def model_changed(self, _index=0):
        if (not self.available() or self.catalog is None or self._key != self.scope_key()
                or self.model.currentData() is None):
            return
        options = draft_options(self.window.input, self.catalog)
        options["model"] = self.model.currentData()
        row = model_row(self.catalog, options)
        efforts = {item["reasoningEffort"] for item in row.get("supportedReasoningEfforts") or []}
        if options.get("effort") not in efforts:
            options.pop("effort", None)
        tiers = {"default", *(item["id"] for item in row.get("serviceTiers") or [])}
        for key in ("serviceTier", "serviceTierForTurn"):
            if options.get(key, "default") not in tiers:
                options.pop(key, None)
        store_options(self.window.input, options, self.catalog)
        self.refresh()

    def effort_changed(self, _index=0):
        if not self.available() or self.catalog is None or self._key != self.scope_key():
            return
        options = draft_options(self.window.input, self.catalog)
        options.pop("effort", None)
        if self.effort.currentData() is not None:
            options["effort"] = self.effort.currentData()
        store_options(self.window.input, options, self.catalog)
        self.refresh()

    def open_attachments(self, _checked=False):
        if self.available():
            self.show_dialog(AttachmentDialog)

    def open_settings(self, _checked=False):
        if self.available():
            self.show_dialog(TurnSettingsDialog)

    def show_dialog(self, kind):
        for dialog in self.window.findChildren(kind):
            if not dialog.closed:
                dialog.raise_()
                dialog.activateWindow()
                return
        dialog = kind(self.window)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
