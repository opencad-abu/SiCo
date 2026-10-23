"""Family palettes and the Qt stylesheet shared by SiCo and standalone CAD flows."""

from __future__ import annotations

from textwrap import dedent

from .palette import LIGHT, active as active_palette

BACKGROUND = LIGHT.background
TEXT = LIGHT.text
SELECTION = LIGHT.selection
COPILOT = LIGHT.copilot
# Muted readouts (tool counters) stay readable but clearly secondary.
MUTED = LIGHT.muted
# Failed tool receipts are called out in red inside the transcript.
ERROR = LIGHT.error
# 停止与关闭这类需要立刻看见的动作：主界面上唯一保留的红色提醒。
STOP = LIGHT.stop
# Matches AIVI's light/dark foreground accent token (_RUST).
ACCENT = LIGHT.accent
# Slightly darker accent for the hovering/pressed state of filled buttons.
ACCENT_DARK = LIGHT.accent_dark
# 详情页代码块：四种脚本语言展示层着色（不改动源码，也不参与 preflight 策略）。
CODE_KEYWORD = LIGHT.code_keyword
CODE_STRING = LIGHT.code_string
CODE_COMMENT = LIGHT.code_comment
CODE_NUMBER = LIGHT.code_number
CODE_BUILTIN = LIGHT.code_builtin
CODE_BLOCK_BACKGROUND = LIGHT.code_block_background
# 详情页表格：统一 1px 灰边框（markdown 与 html 两种来源都覆盖）。
TABLE_BORDER = LIGHT.table_border
# 输入框边框：粗细表示焦点，颜色表示能不能打字（绿=可输入，灰白=不可输入）。
INPUT_READY = LIGHT.input_ready
INPUT_BLOCKED = LIGHT.input_blocked
# 状态色：脚本流程和桌面状态点共用一套（就绪/进行中/注意/过期/故障）。
STATE_READY = LIGHT.state_ready
STATE_BUSY = LIGHT.state_busy
STATE_WARN = LIGHT.state_warn
STATE_STALE = LIGHT.state_stale
STATE_ERROR = LIGHT.state_error
# Qt 原生滚动条宽/高。轨道两端为三角按钮留出等长空间，滑块才不会盖住三角；
# 槽边框会让按钮实际占位多 1px，因此留白再补 1px。
SCROLLBAR_EXTENT = 14
SCROLLBAR_ARROW_CLEARANCE = SCROLLBAR_EXTENT + 1


def copilot_stylesheet(palette=None):
    """Build the transcript/workspace stylesheet for one palette."""
    p = active_palette() if palette is None else palette
    return f"""
QMainWindow, QWidget, QDialog, QDockWidget, QTabWidget::pane,
QToolBar, QStatusBar {{
    background-color: {p.background};
    color: {p.text};
}}
/* 窗口菜单与标签页标题：红棕色，和强调色一致 */
QMenuBar, QMenu, QMenuBar::item, QMenu::item {{
    background-color: {p.background};
    color: {p.accent};
}}
QToolButton#helpButton {{
    background-color: {p.background};
    color: {p.accent};
    border: none;
    padding: 1px 7px;
}}
QToolButton#helpButton:hover {{
    background-color: {p.selection};
}}
QToolButton#helpButton::menu-indicator {{
    image: none;
}}
QTabBar::tab {{
    background-color: {p.background};
    color: {p.accent};
}}
QTextBrowser, QPlainTextEdit, QTextEdit, QTreeWidget, QListWidget,
QLineEdit, QComboBox {{
    background-color: {p.background};
    color: {p.text};
    border: 1px solid {p.accent};
    selection-background-color: {p.selection};
    selection-color: {p.text};
}}
QAbstractScrollArea, QAbstractScrollArea::viewport,
QComboBox QAbstractItemView {{
    background-color: {p.background};
    color: {p.text};
    selection-background-color: {p.selection};
    selection-color: {p.text};
}}
/* 会话历史：明暗两条只差一点色深，便于逐行快速点击又不干扰选中态。 */
QTreeWidget[stripeRows="true"] {{
    alternate-background-color: {p.stripe};
}}
QPushButton, QToolButton {{
    background-color: {p.background};
    color: {p.text};
    border: 1px solid {p.accent};
    padding: 4px 8px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {p.selection};
    color: {p.text};
}}
QTabBar::tab:hover, QTabBar::tab:selected,
QMenuBar::item:selected, QMenu::item:selected {{
    background-color: {p.selection};
    color: {p.accent};
}}
QPushButton:pressed, QToolButton:pressed,
QPushButton:checked, QToolButton:checked {{
    background-color: {p.selection};
    color: {p.text};
}}
QPushButton:disabled, QToolButton:disabled {{
    background-color: {p.background};
    color: {p.muted};
    border-color: {p.input_blocked};
}}
QScrollBar::handle:vertical {{
    background-color: {p.selection};
    min-height: 24px;
}}
QScrollBar::handle:horizontal {{
    background-color: {p.selection};
    min-width: 24px;
}}
QScrollBar:vertical {{
    background-color: {p.background};
    border-left: 1px solid {p.accent};
    width: {SCROLLBAR_EXTENT}px;
    padding: {SCROLLBAR_ARROW_CLEARANCE}px 0px;
}}
QScrollBar:horizontal {{
    background-color: {p.background};
    border-top: 1px solid {p.accent};
    height: {SCROLLBAR_EXTENT}px;
    padding: 0px {SCROLLBAR_ARROW_CLEARANCE}px;
}}
QTextBrowser:focus, QPlainTextEdit:focus, QTextEdit:focus,
QTreeWidget:focus, QListWidget:focus, QLineEdit:focus, QComboBox:focus,
QPushButton:focus, QToolButton:focus {{
    border: 2px solid {p.accent};
}}
QDockWidget {{
    border: 1px solid {p.accent};
}}
QStatusBar {{
    border-top: 1px solid {p.accent};
}}
/* 当前目标、词元统计、状态与模型标识，以及全部下拉筛选（任务/阶段/执行任务、
   全部数据/全部来源等）：红棕色，和菜单、页签保持一致 */
QLabel#sessionTarget, QLabel#tokenUsage, QLabel#sessionStatus, QLabel#platformStatus, QLabel#modelStatus,
QComboBox, QComboBox QAbstractItemView {{
    color: {p.accent};
}}
/* 状态栏中间的项目服务状态（连接状态 + 控制权限）：与右侧标签同色 */
QWidget#serviceStatus QLabel {{
    color: {p.accent};
}}
/* 宿主通知：贴在输入框上方，红棕色提示文字 */
QLabel#hostNotice {{
    color: {p.accent};
}}
/* 发送与审阅页面“提交答复”：可用时红棕底浅色字，禁用时回到白底红棕框 */
QPushButton[bottomAction="true"], QToolButton[bottomAction="true"] {{
    background-color: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
}}
QPushButton[bottomAction="true"]:hover, QToolButton[bottomAction="true"]:hover,
QPushButton[bottomAction="true"]:pressed, QToolButton[bottomAction="true"]:pressed,
QPushButton[bottomAction="true"]:checked, QToolButton[bottomAction="true"]:checked {{
    background-color: {p.accent_dark};
    color: {p.on_accent};
    border: 1px solid {p.accent};
}}
QPushButton[bottomAction="true"]:disabled, QToolButton[bottomAction="true"]:disabled {{
    background-color: {p.background};
    color: {p.text};
    border: 1px solid {p.accent};
}}
/* 确认框里的破坏性动作（强制停止这类）：红字，和停止、关闭同一支红 */
QPushButton[dialogDanger="true"] {{
    color: {p.stop};
}}
/* 发送右侧的箭头与主按钮相连，菜单只在主动点击时展开。 */
QToolButton#sendOptions {{
    padding: 4px 5px;
}}
QToolButton#sendOptions::menu-indicator {{
    image: none;
}}
/* 审阅页面里被推荐的选项：红棕色，与强调色一致 */
QLabel[recommended="true"] {{
    color: {p.accent};
}}
/* 审阅页面的选项圆点：红棕描边的空心圆，选中时圆心补上同色实心点 */
QRadioButton::indicator {{
    width: 12px;
    height: 12px;
    border: 2px solid {p.accent};
    border-radius: 8px;
    background-color: {p.background};
}}
QRadioButton::indicator:checked {{
    background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                      stop:0 {p.accent}, stop:0.4 {p.accent},
                                      stop:0.46 transparent, stop:1 transparent);
}}
/* 会话菜单里的关闭项：红色粗体，一眼看出是退出动作 */
QLabel#closingEntryText {{
    color: {p.error};
    font-weight: 700;
}}
/* 启动进度条：红棕色填充，与强调色一致 */
QProgressBar {{
    background-color: {p.background};
    color: {p.text};
    border: 1px solid {p.accent};
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {p.accent};
}}
/* 输入框边框：粗细表示焦点，颜色表示能不能打字。
   聚焦且可输入＝粗绿线；未聚焦＝细灰白线；聚焦但不可输入（只读选中其它会话、
   发送中、禁用）＝粗灰白线，与红棕色强调控件区分 */
QPlainTextEdit#sessionInput, QPlainTextEdit#quickRequest {{
    border: 1px solid {p.input_blocked};
}}
QPlainTextEdit#sessionInput:focus, QPlainTextEdit#quickRequest:focus {{
    border: 2px solid {p.input_ready};
}}
QPlainTextEdit#sessionInput:focus:read-only, QPlainTextEdit#sessionInput:focus:disabled,
QPlainTextEdit#quickRequest:focus:read-only, QPlainTextEdit#quickRequest:focus:disabled {{
    border: 2px solid {p.input_blocked};
}}
/* 这一行和原生菜单项只差红字粗体：背景必须透明，让菜单自己画底 */
QWidget#closingEntry, QWidget#closingEntry QLabel {{
    background-color: transparent;
}}
"""


def chrome_stylesheet(palette=None):
    """Build the frameless title-bar stylesheet for one palette."""
    p = active_palette() if palette is None else palette
    return dedent(
        f"""
    #copilotTitleBar {{
        background-color: {p.background};
    }}
    #copilotTitleRule {{
        background-color: {p.accent};
    }}
    #copilotTitleText {{
        color: {p.text};
        font-weight: bold;
    }}
    QToolButton#copilotTitleButton, QToolButton#copilotTitleClose {{
        background-color: {p.background};
        border: none;
        padding: 0px;
    }}
    QToolButton#copilotTitleButton:hover, QToolButton#copilotTitleClose:hover {{
        background-color: {p.selection};
    }}
    """
    )


def window_stylesheet(palette=None):
    """Complete family stylesheet for one palette."""
    return copilot_stylesheet(palette) + chrome_stylesheet(palette)


# Historical light-theme constants remain import-compatible for callers and tests.
COPILOT_STYLESHEET = copilot_stylesheet(LIGHT)
CHROME_STYLESHEET = chrome_stylesheet(LIGHT)
WINDOW_STYLESHEET = COPILOT_STYLESHEET + CHROME_STYLESHEET
