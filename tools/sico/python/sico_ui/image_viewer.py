"""One image in its own window: fit, 1:1 and stepped zoom; bytes, no file access."""

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PyQt5.QtGui import QImageReader, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .chrome import SiDialog

MAX_IMAGE_PIXELS = 32_000_000
MAX_IMAGE_SIDE = 16384
MAX_SVG_BYTES = 2 * 1024 * 1024
ZOOM_STEP = 1.25
MIN_ZOOM, MAX_ZOOM = 0.05, 8.0

# A dialog Qt does not own through a parent still needs one live reference here.
_orphans = set()


def dialog_parent(value):
    """The window a preview window belongs to.

    The conversation composition hands over its widget holder, not the window
    itself; handing a non-widget to Qt aborts the process from inside its own
    slot, so resolve the holder's window, else the window the user is in.
    """
    if isinstance(value, QWidget):
        return value
    parent = getattr(value, "parent", None)
    if isinstance(parent, QWidget):
        return parent
    return QApplication.activeWindow()


def retain_without_parent(dialog, parent):
    """Keep an unowned dialog alive until Qt destroys it on close."""
    if parent is not None:
        return
    _orphans.add(dialog)
    dialog.destroyed.connect(lambda *_args, item=dialog: _orphans.discard(item))


class PreviewImage:
    """One viewable image: verified bytes plus the names the window shows."""

    def __init__(self, data, *, name="", sha256="", notice=""):
        self.data, self.name, self.sha256, self.notice = bytes(data), name, sha256, notice

    @property
    def digest(self):
        return self.sha256[:12] if self.sha256 else ""

    def caption(self):
        return self.name or "预览图片"


class ImageView(QWidget):
    """Renders one SVG or raster image at a requested scale inside a scroll area."""

    def __init__(self, image):
        super().__init__()
        self.image = image
        self.source = None
        self.source_size = QSize()
        self.scaled_source = False
        self._zoom = 1.0
        self._provider = self._detect_provider()
        self._pixmap = None
        self._svg = QSvgRenderer(QByteArray(self.image.data))
        self.zoomChanged = None
        self.viewport = QScrollArea()
        self.viewport.setWidgetResizable(False)
        self.viewport.setAlignment(Qt.AlignCenter)
        self.canvas = QLabel()
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.viewport.setWidget(self.canvas)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.viewport, 1)

    def _detect_provider(self):
        source = QSvgRenderer(QByteArray(self.image.data))
        if source.isValid():
            self.source_size = source.defaultSize()
            return "svg"
        reader = self._reader()
        size = reader.size()
        if not reader.canRead():
            return ""
        if (not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS
                or size.width() > MAX_IMAGE_SIDE or size.height() > MAX_IMAGE_SIDE):
            return "oversized"
        self.source_size = size
        return "raster"

    def load(self):
        """Decode at most once; a broken image keeps its notice instead of raising."""
        if self._provider == "svg":
            if self.source_size.isEmpty():
                self.source_size = QSize(640, 480)
        elif self._provider == "raster":
            image = self._reader().read()
            if image.isNull():
                self._provider = ""
            else:
                self.source = QPixmap.fromImage(image)
        self.render_current()
        return bool(self._provider)

    def renderer(self):
        """The SVG renderer a caller may check; validity is the display contract."""
        return self._svg

    @property
    def provider(self):
        return self._provider

    def fit(self):
        self._zoom = None
        self.render_current()

    def actual(self):
        self._zoom = 1.0
        self.render_current()

    def zoom_in(self):
        self._step(ZOOM_STEP)

    def zoom_out(self):
        self._step(1 / ZOOM_STEP)

    def _step(self, factor):
        if self._zoom is None:
            shown = self.displayed_size().width()
            base = max(1, self.source_size.width())
            self._zoom = min(MAX_ZOOM, max(MIN_ZOOM, shown / base))
        self._zoom = min(MAX_ZOOM, max(MIN_ZOOM, self._zoom * factor))
        self.render_current()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._zoom is None or self._provider == "oversized":
            self.render_current()

    def display_size(self):
        """The size a fit shows, so shrinking a window never enlarges the image."""
        if self.source_size.isEmpty():
            return QSize(1, 1)
        bounds = self.viewport.viewport().size()
        return self.source_size.scaled(bounds.boundedTo(self.source_size), Qt.KeepAspectRatio)

    def displayed_size(self):
        if self._zoom is None:
            return self.display_size()
        return self.source_size * self._zoom

    def render_current(self):
        size = self.displayed_size()
        if self._provider == "svg":
            size = QSize(max(1, size.width()), max(1, size.height()))
            pixmap = QPixmap(size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            self._svg.render(painter)
            painter.end()
            self.source = pixmap
        elif self._provider == "oversized" and not self.scaled_source:
            # A wire image may exceed the display budget even after a size guard.
            self._zoom = None
            self._read_oversized()
        if self.source is None:
            return
        self._pixmap = (self.source if self.source.size() == size
                        else self.source.scaled(size, Qt.IgnoreAspectRatio,
                                                Qt.SmoothTransformation))
        self.canvas.setPixmap(self._pixmap)
        self.canvas.setFixedSize(size)
        if self.zoomChanged is not None:
            self.zoomChanged()

    def _read_oversized(self):
        """Decode an out-of-guard image at a display size, or keep the notice."""
        reader = self._reader()
        if not (reader.canRead() and reader.size().isValid()):
            self._provider = ""
            return
        target = reader.size().scaled(QSize(2048, 2048), Qt.KeepAspectRatio)
        reader.setScaledSize(target)
        image = reader.read()
        if image.isNull():
            self._provider = ""
            return
        self.source = QPixmap.fromImage(image)
        self.source_size = self.source.size()
        self.scaled_source = True

    def _reader(self):
        """One image reader over an owned buffer; QImageReader has no setData."""
        buffer = QBuffer()
        buffer.setData(QByteArray(self.image.data))
        buffer.open(QIODevice.ReadOnly)
        reader = QImageReader(buffer)
        reader.buffer = buffer
        return reader

    def zoom_label(self):
        return "适应窗口" if self._zoom is None else f"{round(self._zoom * 100)}%"


class SiImageViewer(SiDialog):
    """A viewed image: SiCo 摆位——工具窗口放右半屏，主窗口占左半屏。"""

    launch_half = "right"
    launch_center = False

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_placed", False):
            self._placed = True
            self.place_on_launch_half(self.parentWidget())


def title_for(image):
    return image.caption() + ("  ·  " + image.digest if image.digest else "")


def show_image(window, image):
    """Open one already-verified image in its own window; never touches the disk."""
    parent = dialog_parent(window)
    dialog = SiImageViewer(title_for(image), parent)
    retain_without_parent(dialog, parent)
    dialog.setObjectName("imageViewer")
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.setWindowModality(Qt.NonModal)
    dialog.resize(900, 700)
    toolbar = QHBoxLayout()
    # 工具栏让开标题下的粗线：按钮边框不与该线重合。
    toolbar.setContentsMargins(0, 3, 0, 0)
    notice = QLabel(image.notice or "")
    notice.setObjectName("imageViewerNotice")
    notice.setWordWrap(True)
    view = ImageView(image)
    status = QLabel()
    status.setObjectName("imageViewerStatus")
    buttons = [("适应窗口", view.fit), ("1:1", view.actual),
               ("放大", view.zoom_in), ("缩小", view.zoom_out)]
    for text, action in buttons:
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False, run=action: run())
        toolbar.addWidget(button)
    toolbar.addStretch(1)
    toolbar.addWidget(status)

    def refresh_status():
        status.setText(view.zoom_label())

    view.zoomChanged = refresh_status
    dialog.body_layout.addLayout(toolbar)
    dialog.body_layout.addWidget(notice)
    dialog.body_layout.addWidget(view, 1)
    dialog.view = view
    dialog.notice = notice
    dialog.status = status
    view.load()
    view.fit()
    if not view.provider and not notice.text():
        notice.setText("图片无法显示，原始文件保留。")
    dialog.show()
    return dialog
