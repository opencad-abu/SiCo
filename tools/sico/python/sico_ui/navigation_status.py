"""Session activity labels and cached Qt status icons."""

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap

# Session activity dot drawn in front of every session name. Painting the dot
# with Qt keeps the runtime free of image assets and emoji rendering.
ACTIVITY = {
    "active": ("活跃", QColor(46, 158, 79)),
    "pending": ("挂起", QColor(224, 134, 0)),
    # 本桌面已打开、当前没有任务在跑：可用待命。
    "free": ("空闲", QColor(45, 127, 249)),
    # 未打开、历史或来源未知：只有记录，不算待命。
    "idle": ("静默", QColor(154, 160, 166)),
}
CHILD_ACTIVITY = {"started": "active", "interrupted": "pending"}
STATUS_DOT = 16
_dots = {}


def _session_time(modified):
    try:
        return datetime.fromtimestamp(modified).strftime("%m-%d %H:%M")
    except (OverflowError, OSError, TypeError, ValueError):
        return "时间未知"


def activity_label(activity):
    return ACTIVITY.get(activity, ACTIVITY["idle"])[0]


def session_activity(activity):
    """Session row state: an owned idle session is free, not silent."""
    return "free" if activity == "idle" else activity


def activity_icon(activity, size=STATUS_DOT):
    """One cached Qt-painted status dot: green active, orange pending, blue free, gray idle."""

    label, color = ACTIVITY.get(activity, ACTIVITY["idle"])
    cached = _dots.get(activity)
    if cached is None:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        inset = max(2, size // 5)
        painter.drawEllipse(inset, inset, size - 2 * inset, size - 2 * inset)
        painter.end()
        # Explicit modes keep the selected row's dot at its true color instead
        # of Qt's generated highlight tint.
        cached = QIcon()
        for mode in (QIcon.Normal, QIcon.Selected, QIcon.Active):
            cached.addPixmap(pixmap, mode)
        _dots[activity] = cached
    return cached



def unavailable_live_text(row, *, ended=False):
    """Name the real reason; deleted/unreadable wording is only one of them."""

    reason = str(row.get("unavailable") or "")
    if not reason and ended:
        text = "当前会话已结束，记录已归档到下方历史栏；可双击浏览或右键“继续”。"
    elif reason == "已删除":
        text = "当前会话已删除，请切换会话或新建会话。"
    elif reason == "记录不可用":
        text = ("当前会话记录暂不可读，恢复后可继续操作；"
                "也可切换会话或重新打开 Silicon Copilot。")
    elif not reason:
        text = "当前会话暂不可用，请切换会话或重新打开 Silicon Copilot。"
    else:
        text = "当前会话" + reason + "，请切换会话或重新打开 Silicon Copilot。"
    detail = str(row.get("unavailable_detail") or "").strip()
    return text + "（" + detail + "）" if detail else text
