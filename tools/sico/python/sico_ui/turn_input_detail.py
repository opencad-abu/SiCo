"""History input viewer. Local media is digest-checked by the data worker."""

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt, QUrl
from PyQt5.QtGui import QDesktopServices, QImageReader, QPixmap
from PyQt5.QtWidgets import QLabel, QListWidget, QPlainTextEdit, QPushButton, QSizePolicy

from .chrome import SiDialog
from .receipts import DataReceipt


class InputImage(QLabel):
    def __init__(self):
        super().__init__()
        self.source = None
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.setMinimumHeight(80)

    def clear(self):
        self.source = None
        super().clear()

    def display(self, source):
        self.source = source
        self.setPixmap(source.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.source is not None:
            self.display(self.source)


def show_input_detail(window, value):
    if not value.isdecimal():
        return
    sequence = int(value)
    delivery = (window.page_presentation.delivery if window.page.reviewing is not None
                else window.binding.delivery)
    if delivery is None:
        return
    activation = window.page.activation
    dialog = SiDialog("输入资料与回合设置", window)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.resize(720, 620)
    layout = dialog.body_layout
    rows, text, picture = QListWidget(), QPlainTextEdit(), InputImage()
    picture.hide()
    rows.setMaximumHeight(130)
    text.setReadOnly(True)
    text.setMaximumHeight(190)
    open_media = QPushButton("打开媒体")
    open_media.hide()
    for widget in (rows, text, picture, open_media):
        layout.addWidget(widget)
    receipt, state = DataReceipt(dialog), {"url": None, "request": 0, "closed": False}

    def current():
        return (not state["closed"] and activation == window.page.activation
                and not window.lifecycle.closing)

    def load(index=None):
        if not current():
            dialog.close()
            return
        state["request"] += 1
        request = state["request"]
        state["url"] = None
        open_media.hide()
        picture.clear()
        picture.hide()
        text.setMaximumHeight(16777215)

        def ready(result):
            if not current() or state["request"] != request:
                return
            text.setPlainText(result["text"])
            if index is None:
                rows.blockSignals(True)
                rows.clear()
                for row in result["inputs"]:
                    rows.addItem(row["type"] + " · " + row.get("name", ""))
                rows.blockSignals(False)
            else:
                row = result["input"]
                if row["type"] in {"localImage", "image"}:
                    text.setMaximumHeight(190)
                    picture.show()
                    buffer = QBuffer()
                    buffer.setData(QByteArray(result["data"]))
                    buffer.open(QIODevice.ReadOnly)
                    reader = QImageReader(buffer)
                    size = reader.size()
                    if (not size.isValid() or size.width() * size.height() > 32_000_000
                            or size.width() > 16384 or size.height() > 16384):
                        picture.setText("图片尺寸超出显示限制")
                    else:
                        image = reader.read()
                        if image.isNull():
                            picture.setText("图片无法解码")
                        else:
                            picture.display(QPixmap.fromImage(image))
                if row["type"] in {"localAudio", "audio"}:
                    state["url"] = QUrl.fromLocalFile(result["path"])
                elif row["type"] in {"skill", "mention"}:
                    text.setPlainText(result["data"][:262144].decode("utf-8", errors="replace"))
                    if len(result["data"]) > 262144:
                        text.appendPlainText("\n[正文显示至 256 KiB，完整归档已保留]")
                    text.appendPlainText("\n" + result["text"])
                    text.verticalScrollBar().setValue(0)
                open_media.setVisible(state["url"] is not None)

        def failed(exc):
            if current() and state["request"] == request:
                text.setPlainText(str(exc))

        receipt.watch(window.api.input_detail(delivery.stream, sequence, index),
                      ready, failed)

    rows.currentRowChanged.connect(lambda index: load(index) if index >= 0 else None)
    open_media.clicked.connect(lambda: QDesktopServices.openUrl(state["url"])
                               if current() and state["url"] is not None else None)
    def finished(*_args):
        state["closed"] = True
        receipt.clear()

    dialog.finished.connect(finished)
    load()
    dialog.show()
    return dialog
