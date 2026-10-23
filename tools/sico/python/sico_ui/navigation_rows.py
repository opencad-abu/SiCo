"""One rendering of a session row shared by the history and label views."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import QTreeWidgetItem

from .navigation_status import CHILD_ACTIVITY, _session_time, activity_icon, activity_label
from .theme import active_palette

KIND_ROLE = Qt.UserRole + 4
CHILD_KIND = "child"


def session_item(row, *, live, browsing, opened_here, ended, activity, line_spacing,
                 item_factory=QTreeWidgetItem):
    key = row["id"]
    reason = row.get("unavailable", "")
    stamp = _session_time(row.get("modified"))
    title = (
        "当前会话\n" + key[:12]
        if key == live and opened_here
        else stamp + "\n" + key[:12]
    )
    name = row.get("name") or row.get("title") or title
    if key == browsing:
        name += "\n（浏览中 · 只读）"
    status = reason or ("已结束" if key in ended else "")
    if status:
        name += "\n（" + status + "）"
    item = item_factory([name])
    lines = name.count("\n") + 1
    item.setSizeHint(0, QSize(140, line_spacing * lines + 4))
    if reason or not opened_here:
        activity = "idle"
    item.setIcon(0, activity_icon(activity))
    item.setData(0, Qt.UserRole, key)
    item.setData(0, Qt.UserRole + 3, bool(row.get("damaged")))
    tooltip = activity_label(activity) + ("\n" + status if status else "") + "\n" + key
    if not reason:
        tooltip += "\n双击浏览（只读）；右键“继续”切换或恢复该会话"
    item.setToolTip(0, tooltip)
    if reason:
        item.setData(0, Qt.UserRole + 2, reason)
        item.setForeground(0, QBrush(QColor(active_palette().muted)))
        if row.get("damaged"):
            item.setToolTip(0, tooltip + "\n内容不完整，右键仅可删除残留记录")
        else:
            item.setFlags(item.flags() & ~(Qt.ItemIsSelectable | Qt.ItemIsEnabled))

    def add_child(parent_item, child):
        child_id = child.get("thread_id", "")
        if not child_id:
            return
        child_name = child.get("name") or "子任务"
        child_item = item_factory([child_name + "\n" + child_id[:12]])
        child_activity = CHILD_ACTIVITY.get(child.get("status", ""), "idle")
        child_item.setIcon(0, activity_icon(child_activity))
        child_item.setToolTip(0, activity_label(child_activity) + "\n" + child_id)
        child_item.setData(0, Qt.UserRole, child_id)
        child_item.setData(0, Qt.UserRole + 1, key)
        child_item.setData(0, KIND_ROLE, CHILD_KIND)
        child_item.setFlags(
            child_item.flags() & ~(Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        )
        parent_item.addChild(child_item)
        for nested in child.get("children", []):
            add_child(child_item, nested)

    for child in row.get("children", []):
        add_child(item, child)
    item.setExpanded(bool(row.get("children")))
    return item
