"""Silicon Copilot workspace; session execution lives in the Qt-independent controller."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QKeySequence
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QApplication,
    QLabel,
    QPushButton,
    QShortcut,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sico import PRODUCT_NAME
from sico.service.frontend_session import FrontendSession

from .background import BackgroundPanel
from .bindings import BindingPanel
from .branding import filled_icon
from .centers import Centers
from .close_lifecycle import CloseLifecycle
from .draft_book import DraftBook
from .glyph_plates import plate_arrow_icon, stop_icon
from .glyphs import (
    ACTION_GLYPH_SIZE,
    back_icon,
    cross_icon,
    info_icon,
    refresh_icon,
    send_icon,
    triangle_icon,
)
from .info_app import InfoApp
from .input import SendOnReturnEdit
from .navigation import Navigation
from .passive_notices import PassiveNotices
from .presentation import DocumentView, target_label
from .receipts import DataReceipt
from .session_activation import NavigationState
from .session_presentation import SessionPresentation
from .theme import TEXT
from .wheel import WheelRouter
from .window_composition import bind_events, compose, connect_commands
from .window_presentation import UsageLabel
from .workbench_view import WorkbenchDetail
from .workspace import CopilotWorkspace

STOP_FIRST_RENAME = (
    "此会话正在执行任务，暂不能重命名。\n"
    "请先点击“停止”结束当前任务，再重命名会话。"
)
STOP_FIRST_DELETE = (
    "此会话正在执行任务，暂不能删除。\n"
    "请先点击“停止”结束当前任务，再删除会话。"
)


@connect_commands
class AssistantWindow(CopilotWorkspace):
    closed = pyqtSignal()

    def __init__(self, frontend, *, persistent=False, notice=lambda kind: None):
        if not isinstance(frontend, FrontendSession):
            raise TypeError("AssistantWindow requires a prepared FrontendSession")
        self.api = frontend.api
        super().__init__(self.api.layout_path)
        self.lifecycle = CloseLifecycle(self.api, persistent=persistent, notice=notice)
        self.info_app = InfoApp(self, closing=lambda: self.lifecycle.closing)
        self.page = NavigationState(self.api, frontend.session, receipt=DataReceipt(self),
                                    closing=lambda: self.lifecycle.closing)
        self.presentation = SessionPresentation(self.page.view.context)
        self.drafts = DraftBook()
        self.host_notice_status = QLabel()
        self.host_notice_status.setObjectName("hostNotice")
        self.host_notice_status.setTextFormat(Qt.PlainText)
        self.host_notice_status.setWordWrap(True)
        self.host_notice_status.hide()
        self.setWindowTitle(PRODUCT_NAME + " / " + self.page.view.label)
        self.refresh_model_badge()
        root = QWidget()
        root.setObjectName("centerDock")
        layout = QVBoxLayout(root)
        heading = QHBoxLayout()
        self.target = QLabel(target_label(self.page.view.context.snapshot))
        self.target.setObjectName("sessionTarget")
        self.target.setTextFormat(Qt.PlainText)
        self.target.setWordWrap(True)
        self.target.setFont(self.readout_font())
        heading.addWidget(self.target, 1)
        self.token_usage = UsageLabel()
        self.token_usage.setObjectName("tokenUsage")
        self.token_usage.setTextFormat(Qt.PlainText)
        self.token_usage.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.token_usage.setFont(self.readout_font())
        self.token_usage.setToolTip("当前任务的模型词元消耗；输入和输出分别统计")
        heading.addWidget(self.token_usage, 0, Qt.AlignRight | Qt.AlignTop)
        layout.addLayout(heading)
        self.session_notices = PassiveNotices(self.info_app)
        layout.addWidget(self.session_notices)
        self.activity_timer = QTimer(self)
        self.activity_timer.setInterval(450)
        self.activity_timer.timeout.connect(self._tick_activity)
        self.display = DocumentView()
        self.display.setOpenLinks(False)
        self.display.setOpenExternalLinks(False)
        layout.addWidget(self.display, 1)
        self.input = SendOnReturnEdit(attachments_enabled=True)
        self.input.setObjectName("sessionInput")
        self.input.setPlaceholderText(
            "输入任务或补充要求；Enter 发送，Shift+Enter 换行；后续任务可从发送菜单安排…"
        )
        self.input.setMaximumHeight(100)
        self.input.setToolTip("Ctrl+L 聚焦输入框；Enter 发送，Shift+Enter 换行")
        self.input.attachmentPasted.connect(self._attachment_pasted)
        # 宿主通知贴在输入框上方，状态栏右侧只保留“基座/模型”标签。
        layout.addWidget(self.host_notice_status)
        layout.addWidget(self.input)
        from .turn_composer import TurnComposer

        self.turn_composer = TurnComposer(self)
        layout.addWidget(self.turn_composer)
        buttons = QHBoxLayout()
        self.send = QPushButton("发送")
        # 纸飞机字形先在正文色上画一遍：禁用时是深色，可用时由 filled_icon 提亮。
        self.send.setIcon(send_icon(ACTION_GLYPH_SIZE, TEXT))
        self.send.setToolTip(
            "发送任务（Enter 发送，Shift+Enter 换行）；执行中可补充当前任务"
        )
        self.input.submitted.connect(self.submit)
        self.stop = QToolButton()
        self.stop.setIcon(stop_icon(ACTION_GLYPH_SIZE))
        self.stop.setToolTip("停止当前任务；尚未执行的请求会保留并暂停")
        self.resume = QPushButton("执行待办任务")
        self.resume.setIcon(triangle_icon(Qt.RightArrow, ACTION_GLYPH_SIZE))
        self.resume.setToolTip("按接收顺序执行待办请求")
        # Queue controls only appear when a session snapshot reports paused
        # pending work; the empty startup window has nothing to resume.
        self.resume.setVisible(False)
        self.abandon = QToolButton()
        self.abandon.setIcon(cross_icon(ACTION_GLYPH_SIZE))
        self.abandon.setToolTip("结束中断任务（不重放）")
        self.send.clicked.connect(self.submit)
        self.stop.clicked.connect(self.cancel_task)
        self.resume.clicked.connect(self.resume_queue)
        self.abandon.clicked.connect(self.abandon_interrupted)
        for button in (self.send, self.stop, self.resume, self.abandon):
            button.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
            buttons.addWidget(button)
        self.input_buttons = buttons
        # Only 发送 carries the accent fill (bottomAction in COPILOT_STYLESHEET);
        # the other task buttons keep the light outline and their own glyphs.
        self.send.setProperty("bottomAction", True)
        self.send.setIcon(filled_icon(self.send.icon()))
        self.latest = QToolButton()
        self.latest.setIcon(plate_arrow_icon(Qt.DownArrow, ACTION_GLYPH_SIZE))
        self.latest.setObjectName("scrollToBottom")
        self.latest.setAccessibleName("滚动到会话底部")
        self.latest.setToolTip("滚动到会话底部")
        self.latest.clicked.connect(self.scroll_to_bottom)
        self.latest.setIconSize(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE))
        buttons.addWidget(self.latest)
        self.turn_composer.align_actions(self.stop, self.latest, buttons)
        self.display.verticalScrollBar().valueChanged.connect(self.update_latest)
        self.display.verticalScrollBar().rangeChanged.connect(self.update_latest)
        layout.addLayout(buttons)
        self.add_workspace("conversation", "对话", root)
        self.centers = Centers(self.page.session, self.api)
        from .elicitation import ElicitationActions

        self.elicitation_actions = ElicitationActions(self)
        self.detail = WorkbenchDetail()
        self.detail.bind(self.centers.index)
        self.add_workspace("detail", "详情", self.detail)
        self.detail.returnRequested.connect(self.return_from_detail)
        self.centers.objectRequested.connect(self.open_object)
        self.centers.answerRequested.connect(self.answer_audit)
        self.centers.indexChanged.connect(self.detail.bind)
        self.detail.objectShown.connect(self.centers.reveal)
        self.display.anchorClicked.connect(self.open_object_link)
        self.display.linkDoubleClicked.connect(self.open_tool_reference)
        self.navigation = Navigation(self)
        self.binding_panel = BindingPanel(self)
        self.navigation.addTab(self.binding_panel, "窗口绑定")
        self.background_panel = BackgroundPanel(self)
        self.navigation.addTab(self.background_panel, "后台检查")
        # 页签内容不放大整窗的最小宽度：保持 800x600 布局可用，窄停靠时页内控件让位。
        for index in range(self.navigation.count()):
            self.navigation.widget(index).setMinimumWidth(300)
        self.task_panel = self.navigation.tasks
        self.task_panel.cancelRequested.connect(self.cancel_router_request)
        # 右侧中心面板（审阅/汇报/数据/进程）也保留最小宽度：四个页签各 90px
        # （标题完整不省略）加上边框正好 360，和左侧导航 300px 的做法一致。
        self.centers.setMinimumWidth(360)
        # 左右面板都能收成条状：双击分割条切换，窗口太窄时自动收起左边。
        self.add_panel("left", "导航", self.navigation, Qt.LeftDockWidgetArea, visible=True,
                       rail=True, rail_width=304)
        self.add_panel("right", "中心", self.centers, Qt.RightDockWidgetArea, visible=True,
                       rail=True, rail_width=360)
        self.panels["left"].setObjectName("leftDock")
        self.panels["right"].setObjectName("rightDock")
        self.return_action = self.session_action("返回当前会话", self.navigation.return_live)
        self.return_action.setIcon(back_icon(ACTION_GLYPH_SIZE))
        self.return_action.setEnabled(False)
        self.view_menu.addAction(
            "工具数据", lambda _checked=False: self.show_panel("tools"), "Ctrl+Shift+T"
        )
        self.view_menu.addAction(
            "任务队列", lambda _checked=False: self.show_panel("tasks"), "Ctrl+Shift+Q"
        )
        self.view_menu.addAction("后台检查", self.show_background)
        self.view_menu.addAction("进程", lambda _checked=False: self.show_panel("processes"))
        from .resources import show_resources
        from .thread_controls import show_thread_controls

        self.workspace_menu.addAction(
            "持续目标与会话操作…", lambda _checked=False: show_thread_controls(self),
        )

        self.workspace_menu.addAction(
            "外部技能与插件…", lambda _checked=False: show_resources(self),
        )
        # Frequent input and stop actions belong to the composer.
        self.resume_action = self.session_action("执行待办任务", self.resume_queue)
        self.resume_action.setVisible(False)
        self.resume_action.setToolTip(self.resume.toolTip())
        self.resume_action.setIcon(triangle_icon(Qt.RightArrow, ACTION_GLYPH_SIZE))
        focus_input = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_input.activated.connect(lambda: self.focus_input())
        if persistent:
            self.receipt_action = self.session_action("查询路由回执", self.query_router_receipt)
            self.receipt_action.setIcon(info_icon(ACTION_GLYPH_SIZE))
            self.reconnect_action = self.session_action("结束实例并重连", self.reconnect_instance)
            self.reconnect_action.setIcon(refresh_icon(ACTION_GLYPH_SIZE))
        # 关闭入口固定显示，并问清含义：关闭窗口（服务与会话继续）还是连项目服务一起停。
        # Ctrl+W 沿用原“隐藏窗口”的按键。
        self.session_tail_action("关闭SiCo", self.ask_close, "Ctrl+W")
        self.wheel_router = WheelRouter(
            self,
            [
                self.display,
                self.input,
                *self.centers.scroll_views,
                self.detail.document,
                self.navigation.current,
                self.navigation.list,
                self.task_panel,
            ],
        )
        self.restore_layout()
        compose(self)

    def replace_binding(self, prepared):
        binding = bind_events(self, prepared)
        self.binding.timer.stop()
        self.binding.dispose()
        self.binding.deleteLater()
        self.binding = binding
        return binding.poll

    def new_session(self):
        self.page.new_session(self.presentation.displayed_context)

    def show_background(self):
        self.show_panel("left")
        self.navigation.setCurrentWidget(self.background_panel)
        self.background_panel.sync()

    def focus_input(self):
        self.navigation.return_live()
        self.activateWindow()
        self.tabs.setCurrentWidget(self.pages["conversation"])
        self.input.setFocus()

    def show_panel(self, key):
        if key == "tools":
            self.centers.show_tools()
            key = "right"
        elif key == "processes":
            self.centers.tabs.setCurrentWidget(self.centers.processes)
            key = "right"
        elif key == "tasks":
            self.navigation.setCurrentWidget(self.task_panel)
            key = "left"
        super().show_panel(key)

    def model_badge(self):
        """Corner badge text: base engine and model, when the provider knows them."""
        base = self.page.view.base
        model = self.page.view.model
        if not base and not model:
            return self.page.view.label
        return f"基座：{base or '—'}\n模型：{model or '—'}"

    def refresh_model_badge(self):
        text = self.model_badge()
        font = self.model_status.font()
        font.setPointSize(self.readout_point_size() if "\n" in text else self.base_point_size())
        self.model_status.setFont(font)
        self.model_status.setText(text)
        line = self.model_status.fontMetrics().height() + 4
        self.model_status.setFixedHeight(line * (2 if "\n" in text else 1))

    def base_point_size(self):
        """Window font size, used as the reference for every readout."""
        size = self.font().pointSize()
        if size <= 0:
            size = self.model_status.font().pointSize()
        return size if size > 0 else 10

    def readout_point_size(self):
        """Small readout size shared by the corners and the top status row."""
        return max(7, self.base_point_size() - 2)

    def readout_font(self):
        font = QFont(self.font())
        font.setPointSize(self.readout_point_size())
        return font

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "renderer"):
            self.renderer._fit_token_usage()

    def restore(self):
        # Explicit menu/Ask action. Avoid unmaximizing an already visible window.
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        self.lifecycle.notice("shown")

    def showEvent(self, event):
        self.sync_activity_animation()
        super().showEvent(event)
        self.wheel_router.install()
        self.refresh_busy_cursor()

    def hideEvent(self, event):
        self.activity_timer.stop()
        self.wheel_router.remove()
        super().hideEvent(event)

    def request_quit(self):
        self.close_choice.blocked.close()
        self.info_app.close()
        self.lifecycle.request()
        self.navigation.timer.stop()
        self.clear_data_receipts()
        self.binding.poll()

    def ask_close(self):
        """Closing asks what it means; the choice owns the sequence."""
        self.close_choice.ask()

    def clear_data_receipts(self):
        self.page.invalidate()
        self.refresh_router_receipt()
        for receipt in self.findChildren(DataReceipt):
            receipt.clear()

    def finish_close(self):
        self.display.set_busy(False)
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if (not self.lifecycle.closing and
                getattr(self, "service_connection", None) is not None):
            event.ignore()
            self.ask_close()
            return
        try:
            self.save_layout()
        except OSError as exc:
            # Closing must still finish; a popup cannot outlive this window.
            from sico.service.diagnostics import diagnostics

            diagnostics.submit(__name__, "Window layout was not saved", exc)
            if not self.lifecycle.closing:
                self.session_notices.set("layout", "窗口布局暂未保存", str(exc))
        else:
            self.session_notices.clear("layout")
        if self.lifecycle.persistent and not self.lifecycle.closing:
            self.hide()
            self.lifecycle.notice("hidden")
            event.ignore()
            return
        self.info_app.close()
        self.lifecycle.request()
        self.navigation.timer.stop()
        self.clear_data_receipts()
        if not self.lifecycle.ready():
            self.hide()
            event.ignore()
        else:
            self.display.set_busy(False)
            self.api.wait_desktop(0)
            self.binding.timer.stop()
            self.navigation.timer.stop()
            self.commands.timer.stop()
            event.accept()
            self.closed.emit()
