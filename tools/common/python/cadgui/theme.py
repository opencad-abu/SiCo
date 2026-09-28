"""Family palette and Qt stylesheet shared by SiCo and the standalone CAD flows."""

from __future__ import annotations

from textwrap import dedent

BACKGROUND = "#FEFEFE"
TEXT = "#000000"
SELECTION = "#BCBCBC"
COPILOT = "#BCBCBC"
# Muted readouts (tool counters) stay readable but clearly secondary.
MUTED = "#6E6E6E"
# Failed tool receipts are called out in red inside the transcript.
ERROR = "#C0392B"
# 停止与关闭这类需要立刻看见的动作：主界面上唯一保留的红色提醒。
STOP = "#F44336"
# Matches AIVI's light/dark foreground accent token (_RUST).
ACCENT = "#B4543A"
# Slightly darker accent for the hovering/pressed state of filled buttons.
ACCENT_DARK = "#96442D"
# 详情页代码块：四种脚本语言展示层着色（不改动源码，也不参与 preflight 策略）。
CODE_KEYWORD = ACCENT
CODE_STRING = "#2E7D32"
CODE_COMMENT = MUTED
CODE_NUMBER = "#1565C0"
CODE_BUILTIN = "#000000"
CODE_BLOCK_BACKGROUND = "#F4F4F4"
# 详情页表格：统一 1px 灰边框（markdown 与 html 两种来源都覆盖）。
TABLE_BORDER = "#888888"
# 输入框边框：粗细表示焦点，颜色表示能不能打字（绿=可输入，灰白=不可输入）。
INPUT_READY = "#2E7D32"
INPUT_BLOCKED = "#BCBCBC"
# 状态色：脚本流程和桌面状态点共用一套（就绪/进行中/注意/过期/故障）。
STATE_READY = "#2E9E4F"
STATE_BUSY = "#2D7FF9"
STATE_WARN = "#E08600"
STATE_STALE = MUTED
STATE_ERROR = ERROR


COPILOT_STYLESHEET = f"""
QMainWindow, QWidget, QDialog, QDockWidget, QTabWidget::pane,
QToolBar, QStatusBar {{
    background-color: {BACKGROUND};
    color: {TEXT};
}}
/* 窗口菜单与标签页标题：红棕色，和强调色一致 */
QMenuBar, QMenu, QMenuBar::item, QMenu::item {{
    background-color: {BACKGROUND};
    color: {ACCENT};
}}
QTabBar::tab {{
    background-color: {BACKGROUND};
    color: {ACCENT};
}}
QTextBrowser, QPlainTextEdit, QTextEdit, QTreeWidget, QListWidget,
QLineEdit, QComboBox {{
    background-color: {BACKGROUND};
    color: {TEXT};
    border: 1px solid {ACCENT};
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
}}
QAbstractScrollArea, QAbstractScrollArea::viewport,
QComboBox QAbstractItemView {{
    background-color: {BACKGROUND};
    color: {TEXT};
    selection-background-color: {SELECTION};
    selection-color: {TEXT};
}}
QPushButton, QToolButton {{
    background-color: {BACKGROUND};
    color: {TEXT};
    border: 1px solid {ACCENT};
    padding: 4px 8px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {SELECTION};
    color: {TEXT};
}}
QTabBar::tab:hover, QTabBar::tab:selected,
QMenuBar::item:selected, QMenu::item:selected {{
    background-color: {SELECTION};
    color: {ACCENT};
}}
QPushButton:pressed, QToolButton:pressed,
QPushButton:checked, QToolButton:checked {{
    background-color: {SELECTION};
    color: {TEXT};
}}
QScrollBar::handle:vertical {{
    background-color: {SELECTION};
    min-height: 24px;
}}
QScrollBar::handle:horizontal {{
    background-color: {SELECTION};
    min-width: 24px;
}}
QScrollBar:vertical {{
    background-color: {BACKGROUND};
    border-left: 1px solid {ACCENT};
}}
QScrollBar:horizontal {{
    background-color: {BACKGROUND};
    border-top: 1px solid {ACCENT};
}}
QTextBrowser:focus, QPlainTextEdit:focus, QTextEdit:focus,
QTreeWidget:focus, QListWidget:focus, QLineEdit:focus, QComboBox:focus,
QPushButton:focus, QToolButton:focus {{
    border: 2px solid {ACCENT};
}}
QDockWidget {{
    border: 1px solid {ACCENT};
}}
QStatusBar {{
    border-top: 1px solid {ACCENT};
}}
/* 当前目标、词元统计、状态与模型标识，以及全部下拉筛选（任务/阶段/执行任务、
   全部数据/全部来源等）：红棕色，和菜单、页签保持一致 */
QLabel#sessionTarget, QLabel#tokenUsage, QLabel#sessionStatus, QLabel#platformStatus, QLabel#modelStatus,
QComboBox, QComboBox QAbstractItemView {{
    color: {ACCENT};
}}
/* 状态栏中间的项目服务状态（连接状态 + 控制权限）：与右侧标签同色 */
QWidget#serviceStatus QLabel {{
    color: {ACCENT};
}}
/* 宿主通知：贴在输入框上方，红棕色提示文字 */
QLabel#hostNotice {{
    color: {ACCENT};
}}
/* 发送与审阅页面“提交答复”：可用时红棕底浅色字，禁用时回到白底红棕框 */
QPushButton[bottomAction="true"], QToolButton[bottomAction="true"] {{
    background-color: {ACCENT};
    color: {BACKGROUND};
    border: 1px solid {ACCENT};
}}
QPushButton[bottomAction="true"]:hover, QToolButton[bottomAction="true"]:hover,
QPushButton[bottomAction="true"]:pressed, QToolButton[bottomAction="true"]:pressed,
QPushButton[bottomAction="true"]:checked, QToolButton[bottomAction="true"]:checked {{
    background-color: {ACCENT_DARK};
    color: {BACKGROUND};
    border: 1px solid {ACCENT};
}}
QPushButton[bottomAction="true"]:disabled, QToolButton[bottomAction="true"]:disabled {{
    background-color: {BACKGROUND};
    color: {TEXT};
    border: 1px solid {ACCENT};
}}
/* 确认框里的破坏性动作（强制停止这类）：红字，和停止、关闭同一支红 */
QPushButton[dialogDanger="true"] {{
    color: {STOP};
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
    color: {ACCENT};
}}
/* 审阅页面的选项圆点：红棕描边的空心圆，选中时圆心补上同色实心点 */
QRadioButton::indicator {{
    width: 12px;
    height: 12px;
    border: 2px solid {ACCENT};
    border-radius: 8px;
    background-color: {BACKGROUND};
}}
QRadioButton::indicator:checked {{
    background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                      stop:0 {ACCENT}, stop:0.4 {ACCENT},
                                      stop:0.46 transparent, stop:1 transparent);
}}
/* 会话菜单里的关闭项：红色粗体，一眼看出是退出动作 */
QLabel#closingEntryText {{
    color: {ERROR};
    font-weight: 700;
}}
/* 启动进度条：红棕色填充，与强调色一致 */
QProgressBar {{
    background-color: {BACKGROUND};
    color: {TEXT};
    border: 1px solid {ACCENT};
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
}}
/* 输入框边框：粗细表示焦点，颜色表示能不能打字。
   聚焦且可输入＝粗绿线；未聚焦＝细灰白线；聚焦但不可输入（只读选中其它会话、
   发送中、禁用）＝粗灰白线，与红棕色强调控件区分 */
QPlainTextEdit#sessionInput, QPlainTextEdit#quickRequest {{
    border: 1px solid {INPUT_BLOCKED};
}}
QPlainTextEdit#sessionInput:focus, QPlainTextEdit#quickRequest:focus {{
    border: 2px solid {INPUT_READY};
}}
QPlainTextEdit#sessionInput:focus:read-only, QPlainTextEdit#sessionInput:focus:disabled,
QPlainTextEdit#quickRequest:focus:read-only, QPlainTextEdit#quickRequest:focus:disabled {{
    border: 2px solid {INPUT_BLOCKED};
}}
/* 这一行和原生菜单项只差红字粗体：背景必须透明，让菜单自己画底 */
QWidget#closingEntry, QWidget#closingEntry QLabel {{
    background-color: transparent;
}}
"""


CHROME_STYLESHEET = dedent(
    f"""
    #copilotTitleBar {{
        background-color: {BACKGROUND};
    }}
    #copilotTitleRule {{
        background-color: {ACCENT};
    }}
    #copilotTitleText {{
        color: #000000;
        font-weight: bold;
    }}
    QToolButton#copilotTitleButton, QToolButton#copilotTitleClose {{
        background-color: {BACKGROUND};
        border: none;
        padding: 0px;
    }}
    QToolButton#copilotTitleButton:hover, QToolButton#copilotTitleClose:hover {{
        background-color: {SELECTION};
    }}
    """
)

WINDOW_STYLESHEET = COPILOT_STYLESHEET + CHROME_STYLESHEET
