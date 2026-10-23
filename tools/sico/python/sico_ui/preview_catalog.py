"""Browse the workspace preview products and open one in the image viewer."""

from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .chrome import SiDialog
from .image_viewer import (
    PreviewImage,
    dialog_parent,
    retain_without_parent,
    show_image,
)
from .receipts import DataReceipt

PREVIEW_LINK_PREFIX = "preview:"
IMAGE_SUFFIXES = frozenset({".svg", ".png"})
SLOW_REQUEST_MS = 1000
SLOW_HINT = "会话数据正在载入，稍候"


def entry_text(row):
    """登记状态 + notice：清单里的每张图要么有 manifest，要么明确标注未登记。"""
    if row.registered:
        return "已登记"
    return row.notice or "未登记"


def stamp_text(value):
    """``MM-DD HH:MM`` for a catalog file's nanosecond mtime; empty when unknown."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return datetime.fromtimestamp(value / 1_000_000_000).strftime("%m-%d %H:%M")


def preview_link(href):
    """The preview file name a chat link names, or "" for any other link.

    A record points at a preview product either with an explicit ``preview:``
    link or with the plain path inside the preview directory that a creation
    receipt printed; every other link keeps its own handler.
    """
    if not isinstance(href, str):
        return ""
    explicit = href.startswith(PREVIEW_LINK_PREFIX)
    value = href[len(PREVIEW_LINK_PREFIX):] if explicit else href
    value = value.split("#", 1)[0].split("?", 1)[0]
    if not value or "://" in value or Path(value).suffix.lower() not in IMAGE_SUFFIXES:
        return ""
    path = Path(value)
    if not explicit and path.parent.name != "circuit_previews":
        return ""
    return path.name


class PreviewCatalogDialog(SiDialog):
    def __init__(self, window, source, initial=""):
        super().__init__("预览产物", window)
        self.setObjectName("previewCatalog")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.NonModal)
        self.resize(680, 520)
        self.source = source
        self.initial = str(initial or "")
        self.receipt = DataReceipt(self)
        self.rows = {}
        caption = QLabel("双击打开；未登记的文件也可以查看。")
        caption.setWordWrap(True)
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels(["产物", "登记状态", "大小", "修改时间"])
        header = self.list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        # Double-click and the platform's activate gesture both open the row.
        self.list.itemActivated.connect(self.open)
        self.list.itemDoubleClicked.connect(self.open)
        self.notice = QLabel("正在读取预览产物…")
        self.notice.setWordWrap(True)
        # 冷启动时会话数据还在载入，请求可能排在它后面；超时后说明原因。
        self.wait_hint = QLabel(SLOW_HINT)
        self.wait_hint.setObjectName("previewWaitHint")
        self.wait_hint.setWordWrap(True)
        self.wait_hint.hide()
        self._slow_timer = QTimer(self)
        self._slow_timer.setSingleShot(True)
        self._slow_timer.setInterval(SLOW_REQUEST_MS)
        self._slow_timer.timeout.connect(self.wait_hint.show)
        layout = QVBoxLayout()
        layout.addWidget(caption)
        layout.addWidget(self.notice)
        layout.addWidget(self.wait_hint)
        layout.addWidget(self.list, 1)
        self.body_layout.addLayout(layout, 1)
        try:
            future = source.preview_artifact("")
        except (ValueError, RuntimeError) as exc:
            self.failed(exc)
            return
        self.watch(future, self.loaded)

    def watch(self, future, success):
        """One pending request: explain a long wait, hide the hint when it lands."""
        self._slow_timer.stop()
        self.wait_hint.hide()
        self._slow_timer.start()
        self.receipt.watch(future, lambda value: self.finish(success, value), self.failed)

    def finish(self, success, value):
        self._slow_timer.stop()
        self.wait_hint.hide()
        success(value)

    def loaded(self, value):
        rows = value.rows
        self.notice.setText(value.notice or (
            "" if rows else "预览目录里还没有图片。"))
        for row in rows:
            item = QTreeWidgetItem([row.name, entry_text(row), str(row.size),
                                    stamp_text(row.mtime)])
            item.setData(0, Qt.UserRole, row.name)
            item.setData(0, Qt.UserRole + 1, row.sha256)
            self.list.addTopLevelItem(item)
            self.rows[row.name] = item
        self.list.setCurrentItem(self.list.topLevelItem(0) if self.list.topLevelItemCount()
                                 else None)
        if self.initial and self.initial in self.rows:
            item = self.rows[self.initial]
            self.list.setCurrentItem(item)
            self.open(item)

    def failed(self, _exc):
        self._slow_timer.stop()
        self.wait_hint.hide()
        self.notice.setText("预览产物清单暂不可读；文件保留在预览目录。")

    def open(self, item, _column=0):
        if item is None:
            return
        name = item.data(0, Qt.UserRole)
        if not name:
            return
        try:
            future = self.source.preview_artifact(name)
        except (ValueError, RuntimeError) as exc:
            self.failed(exc)
            return
        self.watch(future, self.opened)

    def opened(self, value):
        if not value.data:
            self.notice.setText(value.notice or "该预览产物暂不可读或校验失败；文件保留在预览目录。")
            return
        self.notice.setText(value.notice)
        show_image(self, PreviewImage(value.data, name=value.name, sha256=value.sha256,
                                      notice=value.notice))


def open_preview_catalog(window, source, initial=""):
    """Open the preview list for one already-authorized workbench source."""
    if source is None:
        return None
    parent = dialog_parent(window)
    dialog = PreviewCatalogDialog(parent, source, initial)
    retain_without_parent(dialog, parent)
    dialog.show()
    return dialog


def open_preview_link(window, source, name):
    """One named product from a chat link; False when the viewer cannot open it."""
    return open_preview_catalog(window, source, initial=name) is not None
