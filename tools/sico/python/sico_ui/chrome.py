"""SiCo 窗口组装：家族 chrome（cadgui.chrome）加产品标识。

无边框窗口、标题栏和拖拽缩放由 cadgui.chrome 提供；这里只补 SiCo 自己的两点：
标题栏用产品 logo，默认标题用 PRODUCT_NAME。
"""

from __future__ import annotations

from cadgui.chrome import CHROME_STYLESHEET as CHROME_STYLESHEET
from cadgui.chrome import EdgeGrip as EdgeGrip
from cadgui.chrome import FramelessWindowMixin as FramelessWindowMixin
from cadgui.chrome import SiDialog as _SiDialog
from cadgui.chrome import SiMainWindow as _SiMainWindow
from cadgui.chrome import SiWidget as _SiWidget
from cadgui.chrome import SiWindowMixin as _SiWindowMixin
from cadgui.chrome import WINDOW_BUTTONS as WINDOW_BUTTONS
from cadgui.chrome import WINDOW_CONTROLS as WINDOW_CONTROLS
from cadgui.chrome import WINDOW_STYLESHEET as WINDOW_STYLESHEET
from cadgui.chrome import apply_dialog_frameless as apply_dialog_frameless
from cadgui.chrome import apply_main_window_frameless as apply_main_window_frameless
from cadgui.chrome import dialog_frameless_flags as dialog_frameless_flags
from cadgui.chrome import half_screen_rect as half_screen_rect
from cadgui.chrome import main_window_frameless_flags as main_window_frameless_flags

from sico import PRODUCT_NAME

from .branding import logo_icon
from .window_titlebar import SiTitleBar as SiTitleBar


class SiWindowMixin(_SiWindowMixin):
    """SiCo 桌面 chrome：标题栏带产品 logo，默认标题是产品名。"""

    def install_chrome(self, title=PRODUCT_NAME, controls=None, mark=None):
        if title is None:
            title = PRODUCT_NAME
        if mark is None:
            mark = logo_icon()
        return super().install_chrome(title, controls, mark=mark)


class SiMainWindow(SiWindowMixin, _SiMainWindow):
    """Main window: the title bar sits above the menu bar."""


class SiWidget(SiWindowMixin, _SiWidget):
    """Top-level widget window: title bar, chrome and a content area."""


class SiDialog(SiWindowMixin, _SiDialog):
    """Dialog: the title bar sits above the dialog content."""
