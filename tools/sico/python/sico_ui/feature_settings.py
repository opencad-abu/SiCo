"""Session and project Codex built-in feature settings."""

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from sico.codex.feature_groups import INDEX

from .chrome import SiDialog
from .form_styles import checkbox_stylesheet
from .receipts import DataReceipt
from .turn_dialog import TurnDialog


class FeatureGroupList(QWidget):
    """The shared per-group editor used by both settings scopes."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(checkbox_stylesheet())
        self.layout = QVBoxLayout(self)
        self.checks = {}

    def _clear(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def render(self, groups):
        self._clear()
        self.checks = {}
        for group in groups:
            box = QGroupBox(group["id"] + " · " + group["purpose"])
            layout = QVBoxLayout(box)
            note = QLabel(
                "必要性：" + group["necessity"] + "；恢复：" + group["recovery"])
            note.setWordWrap(True)
            layout.addWidget(note)
            if group["configurable"]:
                for key, value in group["options"].items():
                    if type(value) is bool:
                        check = QCheckBox(key)
                        check.setChecked(value)
                        layout.addWidget(check)
                        self.checks[(group["id"], key)] = check
                    else:
                        layout.addWidget(QLabel(
                            key + " = " + repr(value) + "（非布尔选项，暂只读）"))
            else:
                layout.addWidget(QLabel(
                    "固定选项：" + ", ".join(
                        key + "=" + repr(value) for key, value in group["options"].items())))
            self.layout.addWidget(box)
        self.layout.addStretch(1)

    def overrides(self):
        groups = {}
        for (group_id, key), check in self.checks.items():
            default = INDEX[group_id]["options"][key]
            if check.isChecked() != default:
                groups.setdefault(group_id, {})[key] = check.isChecked()
        return groups


class FeatureSettingsDialog(TurnDialog):
    """Read and replace the journal-backed feature overrides of one session."""

    def __init__(self, window):
        super().__init__("Codex 特性", window)
        self.resize(760, 620)
        self.status = None
        self.editor = FeatureGroupList()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.editor)
        self.body_layout.addWidget(self.scroll, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        self.body_layout.addWidget(self.buttons)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("保存")
        self.ok.setEnabled(False)
        self.load()

    def load(self):
        self.hint.setText("正在读取 Codex 特性…")
        try:
            self.receipt.watch(
                self.window.api.command(self.scope.handle, "feature_settings"),
                self.loaded,
                self.failed,
            )
        except (ValueError, RuntimeError) as exc:
            self.failed(exc)

    def loaded(self, status):
        if not self.current():
            self.reject()
            return
        self.status = status
        self.editor.render(status["groups"])
        bound = bool(status.get("bound"))
        self.ok.setEnabled(not bound)
        self.hint.setText(
            "当前会话已绑定运行时；请新建会话后修改。" if bound else
            "修改会保存到本会话；新建运行时后生效。")

    def apply(self):
        if not self.current() or self.status is None:
            self.reject()
            return
        if self.status.get("bound"):
            self.hint.setText("当前会话已绑定运行时；请新建会话后修改。")
            return
        self.ok.setEnabled(False)
        self.hint.setText("正在保存 Codex 特性…")
        try:
            self.receipt.watch(
                self.window.api.command(
                    self.scope.handle, "configure_features", self.editor.overrides()),
                self.saved,
                self.failed,
            )
        except (ValueError, RuntimeError) as exc:
            self.failed(exc)

    def saved(self, status):
        self.loaded(status)
        self.hint.setText("已保存；下次启动运行时生效（已绑定运行时的会话请新建会话）。")

    def failed(self, exc):
        if self.current():
            self.hint.setText(str(exc))
            self.ok.setEnabled(self.status is not None and not self.status.get("bound"))
        else:
            self.reject()
        return False


class ProjectFeatureSettingsDialog(SiDialog):
    """Project default applied to future sessions, including their startup task."""

    def __init__(self, window):
        super().__init__("Codex 特性（工程默认）", window)
        self.window = window
        self.api = window.api
        self.resize(760, 620)
        self.closed = False
        self.hint = QLabel("正在读取工程默认 Codex 特性…")
        self.hint.setWordWrap(True)
        self.body_layout.addWidget(self.hint)
        self.editor = FeatureGroupList()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.editor)
        self.body_layout.addWidget(self.scroll, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.body_layout.addWidget(self.buttons)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("保存")
        self.ok.setEnabled(False)
        self.receipt = DataReceipt(self)
        self.finished.connect(self.cleanup)
        self.load()

    def load(self):
        self.receipt.watch(self.api.feature_defaults.read(), self.loaded, self.failed)

    def loaded(self, status):
        self.editor.render(status["groups"])
        self.ok.setEnabled(True)
        self.hint.setText("保存后用于新会话（含启动任务）。")

    def save(self):
        self.ok.setEnabled(False)
        self.hint.setText("正在保存工程默认…")
        self.receipt.watch(
            self.api.feature_defaults.save(self.editor.overrides()), self.saved, self.failed)

    def saved(self, status):
        self.editor.render(status["groups"])
        self.ok.setEnabled(True)
        self.hint.setText("工程默认已保存；新会话生效。")

    def failed(self, exc):
        self.hint.setText(str(exc))
        self.ok.setEnabled(True)
        return False

    def cleanup(self, _result=None):
        self.closed = True
        self.receipt.clear()


def show_feature_settings(window):
    if window.api is None or getattr(window, "page", None) is None:
        window.info_app.notice("Codex 特性暂不可用", "请先打开一个会话。")
        return
    previous = getattr(window, "feature_settings_dialog", None)
    if previous is not None and previous.isVisible() and previous.current():
        previous.raise_()
        return
    window.feature_settings_dialog = FeatureSettingsDialog(window)
    window.feature_settings_dialog.show()


def show_project_feature_settings(window):
    if window.api is None or not hasattr(window.api, "feature_defaults"):
        window.info_app.notice("Codex 特性暂不可用", "请先连接支持设置管理的工程服务。")
        return
    previous = getattr(window, "project_feature_settings_dialog", None)
    if previous is not None and previous.isVisible() and not previous.closed:
        previous.raise_()
        return
    window.project_feature_settings_dialog = ProjectFeatureSettingsDialog(window)
    window.project_feature_settings_dialog.show()
