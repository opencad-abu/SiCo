"""Choose the current or another project instance when locating a CAD link."""

from __future__ import annotations

from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QMenu

from .cad_object_open import confirm_current, confirm_instance_open


def _action(menu, label, callback):
    action = menu.addAction(label)
    action.triggered.connect(lambda _checked=False: callback())


def offer_location(owner, ref, confirm):
    """Offer current-instance open or explicit cross-instance lookup."""
    actions = getattr(owner.window, "actions", None)
    finder = getattr(actions, "object_instances", None)
    if not callable(finder):
        confirm_current(owner, ref, confirm)
        return
    menu = QMenu(owner.window)
    owner._instance_menu = menu
    _action(menu, "在当前实例只读打开并定位",
            lambda: confirm_current(owner, ref, confirm))
    _action(menu, "查找项目内其他 Virtuoso 实例…",
            lambda: _find_instances(owner, ref, confirm))
    _action(menu, "取消", lambda: owner._status("已取消定位对象"))
    menu.popup(QCursor.pos())


def _find_instances(owner, ref, confirm):
    actions = getattr(owner.window, "actions", None)
    finder = getattr(actions, "object_instances", None)
    if not callable(finder):
        confirm_current(owner, ref, confirm)
        return
    owner._status("正在查找其他 Virtuoso 实例…")
    request = owner._request + 1
    owner._request = request
    try:
        future = finder(ref)
    except Exception as exc:
        owner._warn("无法查找其他实例", str(exc))
        confirm_current(owner, ref, confirm)
        return
    if future is None:
        owner._warn("无法查找其他实例", "实例查找请求未发送。")
        confirm_current(owner, ref, confirm)
        return
    scope = owner._scope()
    timer = owner._start_timer(
        request, None,
        "其他实例查找超时",
        "项目内其他 Virtuoso 实例未及时响应。请重新点击链接或选择当前实例打开。",
        owner.INSTANCE_TIMEOUT_MS,
    )

    def loaded(result):
        if request != owner._request or owner._closing():
            return
        owner._request += 1
        owner._stop_timer(timer)
        rows = result.get("instances") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            owner._warn("无法查找其他实例", "Virtuoso 返回了无法识别的实例列表。")
            confirm_current(owner, ref, confirm)
            return
        rows = [row for row in rows
                if isinstance(row, dict) and row.get("candidate")]
        if not rows:
            owner._notice(
                "没有找到其他实例",
                "项目内没有可用的其他 Virtuoso 实例；将继续询问当前实例。",
            )
            confirm_current(owner, ref, confirm)
            return
        _show_instance_menu(owner, ref, rows, confirm)

    def failed(exc):
        if request != owner._request or owner._closing():
            return False
        owner._request += 1
        owner._stop_timer(timer)
        detail = str(exc).strip() or "其他实例查找失败"
        owner._notice("无法查找其他实例", detail[:300])
        confirm_current(owner, ref, confirm)
        return False

    def settled(*_args):
        owner._stop_timer(timer)

    try:
        owner.window.commands.watch(
            future, success=loaded, failure=failed, settled=settled, scope=scope
        )
    except Exception as exc:
        owner._request += 1
        owner._stop_timer(timer)
        owner._warn("无法查找其他实例", str(exc))
        confirm_current(owner, ref, confirm)


def _show_instance_menu(owner, ref, rows, confirm):
    menu = QMenu(owner.window)
    owner._instance_menu = menu
    for row in rows:
        instance = str(row.get("instance", ""))[-8:]
        windows = row.get("windows") or []
        if not windows:
            label = "其他实例 " + instance + " · 目标未打开，只读打开并定位…"
            _action(
                menu, label,
                lambda candidate=row["candidate"], target=label, prompt=confirm:
                    confirm_instance_open(
                        owner, ref, candidate, target, prompt, _locate_elsewhere,
                    ),
            )
            continue
        for window in windows:
            title = window.get("title") or window.get("window") or window["window_ref"]
            label = "其他实例 " + instance + " · " + str(title)[:120]
            _action(
                menu, label,
                lambda candidate=row["candidate"],
                       window_ref=window["window_ref"],
                       target=label: _locate_elsewhere(
                           owner, ref, candidate, window_ref, target,
                       ),
            )
    menu.addSeparator()
    _action(menu, "在当前实例只读打开并定位",
            lambda: confirm_current(owner, ref, confirm))
    _action(menu, "取消", lambda: owner._status("已取消定位对象"))
    menu.popup(QCursor.pos())


def _locate_elsewhere(owner, ref, candidate, window_ref, label):
    actions = getattr(owner.window, "actions", None)
    locator = getattr(actions, "highlight_object_instance", None)
    if not callable(locator):
        owner._warn("无法在其他实例定位", "当前连接不支持跨实例对象定位。")
        return
    owner._status("正在其他实例定位 " + label)
    request = owner._request + 1
    owner._request = request
    try:
        future = locator(ref, candidate, window_ref)
    except Exception as exc:
        owner._warn("无法在其他实例定位", str(exc))
        return
    if future is None:
        owner._warn("无法在其他实例定位", "跨实例定位请求未发送。")
        return
    scope = owner._scope()
    timer = owner._start_timer(
        request, None,
        "其他实例定位超时",
        "目标实例未及时响应。请重新点击链接；不会自动重试。",
        owner.INSTANCE_TIMEOUT_MS,
    )

    def loaded(result):
        if request != owner._request or owner._closing():
            return
        owner._request += 1
        owner._stop_timer(timer)
        highlighted = result.get("result") if isinstance(result, dict) else None
        if not isinstance(highlighted, dict):
            owner._warn("无法在其他实例定位", "Virtuoso 返回了无法识别的定位结果。")
            return
        owner._show_loaded(ref, highlighted)

    def failed(exc):
        if request != owner._request or owner._closing():
            return False
        owner._request += 1
        owner._stop_timer(timer)
        detail = str(exc).strip() or "跨实例定位失败"
        owner._warn("无法在其他实例定位", detail[:300])
        return False

    def settled(*_args):
        owner._stop_timer(timer)

    try:
        owner.window.commands.watch(
            future, success=loaded, failure=failed, settled=settled, scope=scope
        )
    except Exception as exc:
        owner._request += 1
        owner._stop_timer(timer)
        owner._warn("无法在其他实例定位", str(exc))

__all__ = ["offer_location"]
