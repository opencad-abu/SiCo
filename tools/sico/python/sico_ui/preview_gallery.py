"""报告正文里的图片：统一尺寸护栏，并提供“在窗口打开”的入口。"""

from pathlib import Path

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt, QUrl
from PyQt5.QtGui import QImageReader, QTextCursor, QTextDocument, QTextImageFormat
from PyQt5.QtWidgets import QPushButton

from .image_viewer import PreviewImage, show_image

MAX_IMAGE_PIXELS = 32_000_000
MAX_IMAGE_SIDE = 16384
IMAGE_NOTICE = "图像尺寸无效或超出显示限制，原始证据保留。"
UNREADABLE_NOTICE = "图像无法解码，原始证据保留。"


def preview_artifact_name(value):
    """``(name, digest)`` of the verified preview in a payload, when it has one.

    The digest is the service's; the UI never derives it from file bytes.
    """
    pending = [value]
    for _ in range(32):
        if not pending:
            break
        row = pending.pop(0)
        if not isinstance(row, dict):
            continue
        preview = row.get("svg_preview")
        if isinstance(preview, dict) and preview.get("schema") == "cad.schematic.svg-preview.v1":
            artifact = preview.get("artifact")
            if isinstance(artifact, dict) and artifact.get("path"):
                return Path(str(artifact["path"])).name, str(artifact.get("sha256", ""))
        pending.extend(row[key] for key in ("data", "last_response", "receipt", "created")
                       if key in row)
    return "", ""


def insert_images(document, images, *, height=480, width=640):
    """Embed report images at the top of the document; return their original sizes."""
    sizes = {}
    for index, data in enumerate(images):
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if (not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS
                or size.width() > MAX_IMAGE_SIDE or size.height() > MAX_IMAGE_SIDE):
            return sizes, IMAGE_NOTICE
        image = reader.read()
        if image.isNull():
            return sizes, UNREADABLE_NOTICE
        name = "copilot-image-" + str(index)
        document.addResource(QTextDocument.ImageResource, QUrl(name), image)
        sizes[name] = size
        shown = size.scaled(width, height, Qt.KeepAspectRatio)
        image_format = QTextImageFormat()
        image_format.setName(name)
        image_format.setWidth(shown.width())
        image_format.setHeight(shown.height())
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.Start)
        cursor.insertImage(image_format)
        cursor.insertBlock()
    return sizes, ""


class DocumentImages:
    """One report page's images: the last bytes plus the open-in-window button."""

    def __init__(self, parent, show=show_image):
        self.show = show
        self.data = b""
        self.name = ""
        self.sha256 = ""
        self.button = QPushButton("在窗口打开图片")
        self.button.setObjectName("openDocumentImage")
        self.button.setParent(parent)
        self.button.setToolTip("用图片查看器打开本页图片，不影响会话")
        self.button.setEnabled(False)
        self.button.clicked.connect(lambda _checked=False: self.open())

    def clear(self):
        self.data = b""
        self.name = self.sha256 = ""
        self.button.setEnabled(False)

    def refresh(self, images, name="", sha256=""):
        self.data = bytes(images[0]) if images else b""
        self.name, self.sha256 = name, sha256
        self.button.setEnabled(bool(self.data))

    def open(self):
        if not self.data:
            return None
        return self.show(self.button.window(),
                         PreviewImage(self.data, name=self.name, sha256=self.sha256))
