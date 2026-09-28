"""Family window composition: the shared frameless chrome every CAD window installs.

SiMainWindow, SiWidget and SiDialog install the title bar, the red-brown
stylesheet and the frameless drag/resize interaction. The mark (title-bar icon)
and the default title come from the caller, so SiCo shows its product logo while
the standalone flows show the site logo or nothing.
"""

from __future__ import annotations

from PyQt5.QtGui import QColor, QCursor, QIcon, QPalette
from PyQt5.QtWidgets import QApplication, QDialog, QMainWindow, QMenuBar, QVBoxLayout, QWidget

from .branding import logo_path, logo_text
from .frameless import EdgeGrip as EdgeGrip
from .frameless import FramelessWindowMixin as FramelessWindowMixin
from .frameless import apply_dialog_frameless as apply_dialog_frameless
from .frameless import apply_main_window_frameless as apply_main_window_frameless
from .frameless import dialog_frameless_flags as dialog_frameless_flags
from .frameless import half_screen_rect as half_screen_rect
from .frameless import main_window_frameless_flags as main_window_frameless_flags
from .theme import ACCENT as ACCENT  # noqa: F401  (兼容导出)
from .theme import BACKGROUND as BACKGROUND  # noqa: F401  (兼容导出)
from .theme import CHROME_STYLESHEET as CHROME_STYLESHEET  # noqa: F401  (兼容导出)
from .theme import WINDOW_STYLESHEET
from .titlebar import WINDOW_BUTTONS as WINDOW_BUTTONS  # noqa: F401  (兼容导出)
from .titlebar import WINDOW_CONTROLS, SiTitleBar


def brand_mark():
    """标题栏左上角的品牌图标：站点/产品 logo，取不到就留空。"""

    path = logo_path()
    return QIcon(str(path)) if path is not None else QIcon()


def apply_family_style(application):
    """Standalone flows take the family look: light ground, red-brown accent."""

    palette = application.palette()
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor(BACKGROUND))
    application.setPalette(palette)
    application.setStyleSheet(WINDOW_STYLESHEET)


class SiWindowMixin(FramelessWindowMixin):
    """Frameless chrome that every family window installs."""

    controls = WINDOW_CONTROLS
    # 标题栏图标：调用方给 QIcon；独立流程可以不设。
    mark = None
    closes_with_reject = False
    # 摆位钩子：主窗口可占半屏，对话框贴主窗口中心；由窗口自己选。
    launch_half = None
    launch_center = False

    def place_on_launch_half(self, anchor=None):
        """Put this window on its half of the working screen."""
        rect = half_screen_rect(self.launch_half, anchor) if self.launch_half else None
        if rect is None:
            return None
        self.setGeometry(rect)
        return rect

    def center_on_anchor(self, anchor=None):
        """Open centred on the anchor window, else on its screen half.

        A dialog opened by another process cannot see the main window, so it
        falls back to the left half the main window uses.
        """
        target = anchor if isinstance(anchor, QWidget) and anchor.isVisible() else None
        rect = target.frameGeometry() if target is not None else None
        if rect is None:
            rect = half_screen_rect("left")
            if rect is None:
                screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
                if screen is None:
                    return None
                rect = screen.availableGeometry()
        size = self.size()
        self.move(rect.center().x() - size.width() // 2,
                  max(rect.top(), rect.center().y() - size.height() // 2))
        return self.pos()

    def showEvent(self, event):
        # 首次显示时才摆位：对话框此前可能刚 resize 过，尺寸才算定型。
        super().showEvent(event)
        if self.launch_center and not getattr(self, "_launch_centered", False):
            self._launch_centered = True
            self.center_on_anchor(self.parentWidget())

    def install_chrome(self, title=None, controls=None, mark=None):
        mark = self.mark if mark is None else mark
        if mark is None:
            # 家族默认：窗口左上角带站点/产品 logo。
            mark = brand_mark()
        if mark is not None and not mark.isNull():
            self.setWindowIcon(mark)
        title = logo_text() if title is None else title
        self.setWindowTitle(title)
        self.setStyleSheet(WINDOW_STYLESHEET)
        self.title_bar = SiTitleBar(self, title, controls or self.controls, mark=mark)
        buttons = self.title_bar.buttons
        if "minimize" in buttons:
            buttons["minimize"].clicked.connect(self.showMinimized)
        if "maximize" in buttons:
            buttons["maximize"].clicked.connect(
                lambda *_checked: self.toggle_maximized())
        if "close" in buttons:
            buttons["close"].clicked.connect(
                lambda *_checked: self.close_from_chrome())
        self.init_frameless(self.title_bar)
        return self.title_bar

    def close_from_chrome(self):
        """Close the window the way its own chrome should: reject for dialogs."""
        if self.closes_with_reject:
            self.reject()
        else:
            self.close()


class SiMainWindow(SiWindowMixin, QMainWindow):
    """Main window: the title bar sits above the menu bar."""

    def __init__(self, title=None, parent=None, controls=None, mark=None):
        super().__init__(parent)
        apply_main_window_frameless(self)
        self.install_chrome(title, controls, mark)
        self.menu_bar = QMenuBar(self)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.menu_bar)
        self.setMenuWidget(container)

    def menuBar(self):
        return self.menu_bar


class SiWidget(SiWindowMixin, QWidget):
    """Top-level widget window: title bar, chrome and a content area."""

    def __init__(self, title=None, parent=None, controls=None, mark=None):
        super().__init__(parent)
        apply_dialog_frameless(self)
        self.install_chrome(title, controls, mark)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body, 1)

    def content_layout(self):
        return self.body_layout


class SiDialog(SiWindowMixin, QDialog):
    """Dialog: the title bar sits above the dialog content."""

    closes_with_reject = True
    # 对话框跟主窗口中心打开；没有锚窗口时用屏幕半屏中心。
    launch_center = True

    def __init__(self, title=None, parent=None, controls=None, mark=None):
        super().__init__(parent)
        apply_dialog_frameless(self)
        self.install_chrome(title, controls, mark)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body, 1)

    def content_layout(self):
        return self.body_layout
