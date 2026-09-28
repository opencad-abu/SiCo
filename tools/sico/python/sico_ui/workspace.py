"""Assistant workspace pane lifecycle with compatibility tab/menu class exports."""

from __future__ import annotations

import os

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWIDGETSIZE_MAX,
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .chrome import SiMainWindow, half_screen_rect
from .glyphs import triangle_icon
from .tools import install_tools_menu
from .workspace_settings import open_settings

# Compatibility imports retain the historical module entry; migrate imports to workspace_closing.
from .workspace_closing import (
    ClosingMenuEntry as ClosingMenuEntry,
)
from .workspace_menus import WorkspaceMenus

# Compatibility imports retain the historical module entry; migrate imports to workspace_tabs.
from .workspace_tabs import (
    EqualTabBar as EqualTabBar,
)
from .workspace_tabs import (
    EvenTabPanel as EvenTabPanel,
)
from .workspace_tabs import (
    EvenTabWidget as EvenTabWidget,
)

# 面板收成条状后留下的窄条宽度；中心聊天区被挤到 CHAT_MIN_WIDTH 以下时，
# 先把左侧导航、再把右侧中心收成条状；展回同样按这个宽度判断，
# 中间再留 COLLAPSE_MARGIN 的迟滞，避免在边界上反复收放。
RAIL_WIDTH = 26
# 条状手柄上的红棕色三角形：画布 14px，三角本体约 8px。
RAIL_GLYPH_SIZE = 14
CHAT_MIN_WIDTH = 320
CHAT_COLLAPSE_MARGIN = 24
# 收条后窗口还能继续缩小，但保留一个可用下限（中心区最小 280 + 两条 + 边框）。
MINIMUM_WINDOW_WIDTH = 480


def rail_direction(area):
    """条状手柄的三角形指向窗口中心：左侧停靠向右，右侧停靠向左。"""
    return Qt.LeftArrow if area == Qt.RightDockWidgetArea else Qt.RightArrow


class PanelRail(QWidget):
    """面板收起后的条状手柄：点一下或双击都能展开（分割条双击同样可切换）。"""

    expandRequested = pyqtSignal()

    def __init__(self, title, direction, parent=None):
        super().__init__(parent)
        self.setObjectName("panelRail")
        self.setFixedWidth(RAIL_WIDTH)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("双击分割条可收起/展开" + title)
        self.button = QToolButton(self)
        self.button.setObjectName("panelRailButton")
        # 红棕色小三角由 Qt 自绘，条状手柄不再依赖 share 目录里的左右 png。
        self.button.setIcon(triangle_icon(direction, RAIL_GLYPH_SIZE))
        self.button.setIconSize(QSize(RAIL_GLYPH_SIZE, RAIL_GLYPH_SIZE))
        self.button.setFixedSize(18, 18)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setToolTip("展开" + title)
        self.button.setStyleSheet("QToolButton { padding: 0px; }")
        self.button.clicked.connect(self.expandRequested.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 6, 3, 6)
        layout.setSpacing(0)
        layout.addStretch(1)
        layout.addWidget(self.button, 0, Qt.AlignHCenter)
        layout.addStretch(1)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.expandRequested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.expandRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class CollapsiblePanel:
    """一个可收成条状的面板：内容、条状手柄、上次展开宽度与收起来源。"""

    def __init__(self, panel, content, rail, restore_width):
        self.panel, self.content, self.rail = panel, content, rail
        self.restore_width = restore_width
        self.collapsed = False
        self.auto = False
        self.hold = False










class CopilotWorkspace(WorkspaceMenus, SiMainWindow):
    # SiCo 摆位：主窗口占工作屏幕左半屏，拉起的窗口默认右半屏（chrome 统一处理）。
    launch_half = "left"

    def __init__(self, settings_path):
        super().__init__()
        self.setObjectName("siliconCopilot")
        self.resize(1280, 820)
        self.settings = open_settings(settings_path)
        self.pages, self.panels, self.panel_defaults = {}, {}, {}
        self.collapsible = {}
        self._rail_sync = False
        self._minimum_pass = False
        self.setDockOptions(self.AllowNestedDocks | self.AllowTabbedDocks | self.AnimatedDocks)
        self.tabs = QTabWidget()
        self.tabs.setTabBar(EqualTabBar())
        self.tabs.setDocumentMode(True)
        self.tabs.setObjectName("workspaces")
        self.tabs.setUsesScrollButtons(False)
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.tabs.tabBar().setStyleSheet("QTabBar::tab { min-width: 0px; }")
        self.tabs.setMinimumWidth(280)
        self.setCentralWidget(self.tabs)
        self.session_menu = self.menuBar().addMenu("会话")
        self.view_menu = self.menuBar().addMenu("视图")
        self.workspace_menu = self.menuBar().addMenu("工作区")
        self.tools_menu = self.menuBar().addMenu("工具")
        install_tools_menu(self.tools_menu, self)
        self._session_tail = None
        self.view_menu.addAction("恢复默认布局", lambda _checked=False: self.reset_layout())
        status_height = self.fontMetrics().height() + 4
        self.status = QLabel()
        self.status.setObjectName("sessionStatus")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(False)
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.status.setFixedHeight(status_height)
        status_box = QWidget()
        status_box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._status_layout = QVBoxLayout(status_box)
        self._status_layout.setContentsMargins(0, 0, 0, 0)
        self._status_layout.setSpacing(0)
        self._status_layout.addWidget(self.status)
        self.statusBar().addWidget(status_box, 1)
        # 项目服务状态固定显示在底部中间：左侧状态文本与右侧伸缩位等宽，
        # 右侧永久区只保留基座/模型标签（服务标签由各 presenter 自行挂入）。
        self.service_status = QWidget()
        self.service_status.setObjectName("serviceStatus")
        self.service_status.setFixedHeight(status_height)
        self.service_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.service_status.hide()
        self.service_status_layout = QHBoxLayout(self.service_status)
        self.service_status_layout.setContentsMargins(0, 0, 0, 0)
        self.service_status_layout.setSpacing(8)
        self.statusBar().addWidget(self.service_status)
        self.statusBar().addWidget(QWidget(), 1)
        self.model_status = QLabel()
        self.model_status.setObjectName("modelStatus")
        self.model_status.setTextFormat(Qt.PlainText)
        self.model_status.setMaximumWidth(240)
        self.model_status.setWordWrap(False)
        self.model_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.model_status.setFixedHeight(status_height)
        self.statusBar().addPermanentWidget(self.model_status)
        self._default_state = None

    def add_platform_status(self, label):
        """Stack platform and session status using the right-hand model badge font."""
        font = self.model_status.font()
        for widget in (label, self.status):
            widget.setFont(font)
            widget.setWordWrap(False)
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            widget.setFixedHeight(widget.fontMetrics().height() + 4)
        self._status_layout.insertWidget(0, label)

    def add_service_status(self, widget):
        """Project-service status labels live in the middle of the status bar."""

        self.service_status_layout.addWidget(widget)
        self.service_status.show()

    def add_workspace(self, key, title, widget):
        if key in self.pages:
            raise ValueError("Workspace already registered: " + key)
        self.pages[key] = widget
        self.tabs.addTab(widget, title)
        self._equalize_tabs(self.tabs)
        self.workspace_menu.addAction(
            title, lambda _checked=False: self.tabs.setCurrentWidget(widget)
        )
        return widget

    @staticmethod
    def _equalize_tabs(tabs):
        """Make each visible tab occupy an equal share of the tab bar."""
        count = tabs.count()
        if count:
            tabs.tabBar().setExpanding(True)
            tabs.tabBar().setMinimumWidth(0)
            tabs.tabBar().setUsesScrollButtons(False)
            tabs.tabBar().setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            tabs.tabBar().setStyleSheet("QTabBar::tab { min-width: 0px; }")

    def add_panel(self, key, title, widget, area=Qt.RightDockWidgetArea, *, visible=False,
                  rail=False, rail_width=0):
        """注册一个停靠面板；``rail=True`` 就带条状收起能力（三角形指向中心区）。"""
        if key in self.panels:
            raise ValueError("Panel already registered: " + key)
        panel = QDockWidget(title, self)
        panel.setObjectName("panel_" + key)
        body = QWidget()
        body.setObjectName("panelBody_" + key)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(widget, 1)
        panel.setWidget(body)
        # Panels run edge to edge: the 视图 menu still names and toggles them.
        title_bar = QWidget()
        title_bar.setFixedHeight(0)
        panel.setTitleBarWidget(title_bar)
        if rail:
            handle = PanelRail(title, rail_direction(area))
            handle.hide()
            body_layout.addWidget(handle, 0)
            handle.expandRequested.connect(
                lambda key=key: self.set_panel_collapsed(key, False)
            )
            self.collapsible[key] = CollapsiblePanel(panel, widget, handle, rail_width)
            self._apply_window_minimum()
        self.addDockWidget(area, panel)
        panel.setVisible(visible)
        self.panels[key] = panel
        self.panel_defaults[key] = (area, visible)
        toggle = panel.toggleViewAction()
        if rail:
            # 视图菜单重新打开面板时直接展开，不要只亮出条状手柄。
            toggle.triggered.connect(lambda _checked=False, key=key:
                                     self.set_panel_collapsed(key, False))
        self.view_menu.addAction(toggle)
        return panel

    def show_panel(self, key):
        panel = self.panels[key]
        was_hidden = panel.isHidden()
        # 条状收起的面板重新打开时先展开，不然只会亮出手柄。
        self.set_panel_collapsed(key, False)
        panel.show()
        panel.raise_()
        if was_hidden and not panel.isFloating():
            self.resizeDocks([panel], [320], Qt.Horizontal)

    # -- 条状收起（双击分割条 / 窄窗口自动） --------------------------------

    def set_panel_collapsed(self, key, collapsed, *, auto=False):
        """收起或展开一个带条状手柄的面板；返回是否真的改变了状态。"""
        state = self.collapsible.get(key)
        if state is None:
            return False
        panel, collapsed = state.panel, bool(collapsed)
        if collapsed == state.collapsed:
            if not auto:
                # 用户显式操作过：收起保持收起；展开时挡住窄窗口的自动收起。
                state.auto = False
                state.hold = not collapsed
            return False
        if collapsed:
            if not panel.isHidden():
                state.restore_width = max(state.restore_width, panel.width())
            state.content.hide()
            state.rail.show()
            # 固定成窄条：窗口的最小宽度也跟着降下来，中心区拿到全部余量。
            panel.setMaximumWidth(RAIL_WIDTH + 4)
            state.hold = False
        else:
            state.rail.hide()
            state.content.show()
            panel.setMaximumWidth(QWIDGETSIZE_MAX)
            # 用户手动展开后先挡住窄窗口规则，窗口重新变宽（有余量）再解除；
            # 在宽松窗口里展开只是普通展开，之后仍由自动规则管理。
            roomy = self.projected_chat_width() - state.restore_width >= CHAT_MIN_WIDTH
            state.hold = not auto and not roomy
            if not panel.isHidden() and not panel.isFloating():
                self.resizeDocks([panel], [max(state.restore_width, RAIL_WIDTH * 2)],
                                 Qt.Horizontal)
        state.collapsed, state.auto = collapsed, bool(auto) and collapsed
        return True

    def panel_collapsed(self, key):
        state = self.collapsible.get(key)
        return bool(state and state.collapsed)

    def separator_panel(self, pos):
        """双击落在面板与中心区之间的分割条时，返回对应面板 key。"""
        for key in ("left", "right"):
            state = self.collapsible.get(key)
            if state is None:
                continue
            panel = state.panel
            if panel.isHidden() or panel.isFloating():
                continue
            rect = panel.geometry()
            if not rect.top() - 6 <= pos.y() <= rect.bottom() + 6:
                continue
            edge = rect.right() if key == "left" else rect.left()
            if abs(pos.x() - edge) <= 8:
                return key
        return ""

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            key = self.separator_panel(event.pos())
            if key and self.set_panel_collapsed(key, not self.panel_collapsed(key)):
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_window_minimum()
        self.sync_panel_rails()

    def collapsed_width(self):
        """面板全部收成条状、中心区取最小宽度时的窗口宽度。"""
        rails = sum(RAIL_WIDTH + 4 for _ in self.collapsible)
        central = max(self.tabs.minimumSizeHint().width(), self.tabs.minimumWidth())
        return central + rails + 12

    def _apply_window_minimum(self):
        """窗口最小尺寸按“面板收条 + 中心区”算，展开的面板不再顶住窄窗口。

        Qt 的默认约束会把布局自然最小宽（含展开面板）写进窗口最小尺寸，
        于是窄窗口只能先被顶住、收条后才缩得下去；这里改成收条后的宽度，
        配合 ``sync_panel_rails`` 的自动收条，800×600 这类小窗口也能一步到位。
        """
        if not self.collapsible or self._minimum_pass:
            return
        self._minimum_pass = True
        try:
            layout = self.layout()
            if layout is not None and layout.sizeConstraint() != QLayout.SetNoConstraint:
                layout.setSizeConstraint(QLayout.SetNoConstraint)
            wanted = QSize(max(MINIMUM_WINDOW_WIDTH, self.collapsed_width()),
                           super().minimumSizeHint().height())
            if self.minimumSize() != wanted:
                self.setMinimumSize(wanted)
        finally:
            self._minimum_pass = False

    def sync_panel_rails(self):
        """窄窗口自动收条：中心区被挤窄就先收左侧导航，再收右侧中心；宽回来再展回。

        只接管自动收起的面板；用户手动收起/展开过的面板交给用户自己（``hold``）。
        """
        if self._rail_sync or not self.collapsible:
            return
        self._rail_sync = True
        try:
            center = self.projected_chat_width()
            cramped = CHAT_MIN_WIDTH - CHAT_COLLAPSE_MARGIN
            for key in ("left", "right"):
                state = self.collapsible.get(key)
                if (state is None or state.collapsed or state.hold
                        or state.panel.isHidden() or state.panel.isFloating()):
                    continue
                if center >= cramped:
                    break
                freed = max(state.panel.width(), RAIL_WIDTH) - RAIL_WIDTH
                if self.set_panel_collapsed(key, True, auto=True):
                    center += freed
            projected = self.projected_chat_width()
            # 展回先给左侧导航（会话入口优先），再给右侧中心。
            for key in ("left", "right"):
                state = self.collapsible.get(key)
                if state is None or state.panel.isHidden() or state.panel.isFloating():
                    continue
                if projected - state.restore_width < CHAT_MIN_WIDTH:
                    continue
                # 展回后中心区还有富余：自动收起的面板恢复，手动展开的记录解锁。
                if state.collapsed and state.auto:
                    self.set_panel_collapsed(key, False, auto=True)
                    projected -= state.restore_width
                elif not state.collapsed:
                    state.hold = False
        finally:
            self._rail_sync = False

    def projected_chat_width(self):
        """按当前窗口宽度推算中心区宽度；不依赖尚未刷新的布局。"""
        used = 0
        for key in ("left", "right"):
            state = self.collapsible.get(key)
            if state is None or state.panel.isHidden() or state.panel.isFloating():
                continue
            used += (RAIL_WIDTH if state.collapsed
                     else max(state.panel.width(), state.restore_width))
        return max(0, self.width() - used)

    def restore_layout(self):
        if "left" in self.panels and "right" in self.panels:
            self.resizeDocks([self.panels["left"], self.panels["right"]], [210, 360], Qt.Horizontal)
        self._default_state = self.saveState(2)
        geometry = self.settings.value("workspace/geometry")
        state = self.settings.value("workspace/state")
        if geometry is None or not self.restoreGeometry(geometry):
            # 首次启动（或旧记录不可用）落在屏幕左半屏，与桌面的贴边手感一致。
            self.apply_default_geometry()
        if state is not None:
            self.restoreState(state, 2)
        self._clamp_floating_panels()

    def default_geometry(self):
        """Screen rect used when nothing is remembered: the left half."""
        return half_screen_rect("left", self)

    def apply_default_geometry(self):
        rect = self.default_geometry()
        if rect is not None:
            self.setGeometry(rect)

    def _clamp_floating_panels(self):
        screens = [screen.availableGeometry() for screen in QApplication.screens()]
        for panel in self.panels.values():
            if panel.isFloating() and not any(
                rect.contains(panel.frameGeometry().topLeft()) for rect in screens
            ):
                panel.move(screens[0].topLeft())

    def save_layout(self):
        self.settings.setValue("workspace/geometry", self.saveGeometry())
        self.settings.setValue("workspace/state", self.saveState(2))
        self.settings.sync()
        if os.path.isfile(self.settings.fileName()):
            os.chmod(self.settings.fileName(), 0o600)

    def reset_layout(self):
        if self._default_state is not None:
            self.restoreState(self._default_state, 2)
        for key in self.collapsible:
            # 恢复默认布局同时展开条状收起的面板。
            self.set_panel_collapsed(key, False)
        for key, panel in self.panels.items():
            area, visible = self.panel_defaults[key]
            panel.setFloating(False)
            self.addDockWidget(area, panel)
            panel.setVisible(visible)
        self.apply_default_geometry()
