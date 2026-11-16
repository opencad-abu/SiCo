"""Non-task actions for durable CAD object links with bounded recovery."""

from __future__ import annotations

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QMenu

from sico.core.cad_ref import normalize_ref, parse_object_link, signature_matches

from .cad_object_instances import offer_location
from .si_prompt import confirm

TIMEOUT_MS = 10000
WINDOW_TIMEOUT_MS = 6000
OPEN_TIMEOUT_MS = 30000
INSTANCE_TIMEOUT_MS = 30000

_ERRORS = {
    "stale_object": (
        "对象已不存在",
        "目标对象可能已在 Virtuoso 中删除、改名或被替换。请重新选择对象后再建立引用。",
    ),
    "stale_context": (
        "原窗口已变化",
        "窗口已关闭或 cellview/层级已变化。请重新打开窗口后重试。",
    ),
    "target_not_open": (
        "目标窗口未打开",
        "请先打开对应 cellview 的窗口，再点击对象链接。",
    ),
    "object_changed": (
        "对象已变化",
        "实例 master 与引用不一致，未执行定位。请重新选择对象。",
    ),
    "highlight_failed": (
        "对象无法高亮",
        "对象存在但没有可绘制图形，可能已删除；请重新确认对象。",
    ),
    "ambiguous_target": (
        "目标窗口不唯一",
        "有多个窗口显示该 cellview，请在窗口列表中选择。",
    ),
    "capability_not_available": (
        "当前 Virtuoso 不支持对象定位",
        "该版本缺少对象选择或高亮能力。",
    ),
    "invalid_arguments": (
        "对象引用无效",
        "链接字段不完整或已损坏；请重新插入对象引用。",
    ),
    "read_failed": (
        "对象读取失败",
        "Virtuoso 暂时无法读取目标；请确认窗口仍打开后重试。",
    ),
    "result_too_large": (
        "对象结果过大",
        "请重新选择单个对象或缩小范围。",
    ),
    "unsupported_object": (
        "对象类型不支持",
        "该对象类型暂不支持定位。",
    ),
}
_GENERIC = (
    "无法定位对象",
    "目标对象或窗口可能已删除、关闭或不可用。请重新打开窗口后重试。",
)


class CADObjectActions(QObject):
    """Resolve a link click through the captured target bridge, never a task."""

    TIMEOUT_MS = TIMEOUT_MS
    WINDOW_TIMEOUT_MS = WINDOW_TIMEOUT_MS
    OPEN_TIMEOUT_MS = OPEN_TIMEOUT_MS
    INSTANCE_TIMEOUT_MS = INSTANCE_TIMEOUT_MS

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._request = 0
        self._timer = None
        self._pending_provider = None
        self._window_menu = None
        self._instance_menu = None

    def open_link(self, value):
        try:
            session, ref = parse_object_link(value)
        except ValueError:
            self._warn("对象链接无效", "链接不完整或已损坏；请重新插入对象引用。")
            return False
        self.open(session, ref)
        return True

    def open(self, session, ref):
        try:
            ref = normalize_ref(ref)
        except ValueError as exc:
            self._warn("对象引用无效", str(exc))
            return
        page = getattr(self.window, "page", None)
        current = getattr(getattr(page, "session", None), "session_id", "")
        if session != current:
            self._warn(
                "对象引用不属于当前会话",
                "请先打开引用所属的会话，再定位 Virtuoso 对象。",
            )
            return
        if self._closing():
            return
        self._highlight(ref)

    def _closing(self):
        return bool(getattr(getattr(self.window, "lifecycle", None), "closing", False))

    def _warn(self, title, message):
        info = getattr(self.window, "info_app", None)
        if info is not None:
            try:
                info.warning(title, message)
            except Exception:
                pass

    def _notice(self, title, message):
        info = getattr(self.window, "info_app", None)
        if info is not None:
            try:
                info.notice(title, message)
            except Exception:
                pass

    def _status(self, text):
        status = getattr(self.window, "status", None)
        if status is not None:
            try:
                status.setText(text)
            except Exception:
                pass

    def _provider(self):
        actions = getattr(self.window, "actions", None)
        if actions is None:
            return None
        loader = getattr(actions, "object_picker_provider", None)
        if loader is not None:
            return loader()
        return actions.target_picker_provider()

    def _scope(self):
        try:
            return self.window.actions.capture()
        except Exception:
            return None

    @staticmethod
    def _close(provider):
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass

    def _error_info(self, exc):
        code = getattr(exc, "code", "")
        title, detail = _ERRORS.get(code, _GENERIC)
        if code not in _ERRORS:
            raw = str(exc).strip()
            if raw:
                detail += "\n" + raw[:300]
        return title, detail

    def _start_timer(self, request, provider, title, detail, timeout):
        if self._timer is not None:
            self._timer.stop()
        timer = self._timer = QTimer(self)
        timer.setSingleShot(True)

        def expired():
            if request != self._request or self._closing():
                return
            self._request += 1
            if self._pending_provider is provider:
                self._pending_provider = None
            self._close(provider)
            self._warn(title, detail)

        timer.timeout.connect(expired)
        timer.start(timeout)
        return timer

    def _stop_timer(self, timer):
        timer.stop()
        if self._timer is timer:
            self._timer = None

    def _highlight(self, ref, provider=None, window_ref=None, *, allow_open=True):
        request = self._request + 1
        self._request = request
        if self._pending_provider is not None:
            self._close(self._pending_provider)
        if provider is None:
            try:
                provider = self._provider()
            except Exception as exc:
                self._warn("无法定位对象", str(exc))
                return
            if provider is None:
                self._warn("无法定位对象", "当前会话没有 Virtuoso 桥接连接。")
                return
        self._pending_provider = provider
        scope = self._scope()
        try:
            future = provider.highlight(ref, window_ref)
        except Exception as exc:
            self._pending_provider = None
            self._close(provider)
            title, detail = self._error_info(exc)
            self._warn(title, detail)
            return
        timer = self._start_timer(
            request, provider,
            "对象定位超时",
            "目标对象或窗口可能已删除、关闭或无响应。请重新打开窗口后重试；不会自动重试。",
            self.TIMEOUT_MS,
        )

        def loaded(result):
            if request != self._request or self._closing():
                return
            self._request += 1
            self._pending_provider = None
            self._stop_timer(timer)
            if not isinstance(result, dict) or result.get("ok") is False:
                self._warn("对象定位失败", "Virtuoso 返回了无法识别的结果。")
                return
            self._show_loaded(ref, result)

        def failed(exc):
            if request != self._request or self._closing():
                return False
            self._request += 1
            self._pending_provider = None
            self._stop_timer(timer)
            if getattr(exc, "code", "") == "ambiguous_target" and window_ref is None:
                self._choose_window(ref)
                return False
            if getattr(exc, "code", "") == "target_not_open" and (
                    window_ref is None and allow_open):
                self._offer_open(ref)
                return False
            title, detail = self._error_info(exc)
            self._warn(title, detail)
            return False

        def settled(*_args):
            self._stop_timer(timer)
            if self._pending_provider is provider:
                self._pending_provider = None
            self._close(provider)

        try:
            self.window.commands.watch(
                future, success=loaded, failure=failed, settled=settled, scope=scope
            )
        except Exception as exc:
            self._request += 1
            self._pending_provider = None
            self._stop_timer(timer)
            self._close(provider)
            self._warn("无法定位对象", str(exc))

    def _offer_open(self, ref):
        offer_location(self, ref, confirm)

    def _show_loaded(self, ref, result):
        got_target = result.get("target") or {}
        want_target = ref["target"]
        if (result.get("kind") != ref["kind"]
                or result.get("name") != ref["name"]
                or any(got_target.get(key) != want_target.get(key)
                       for key in ("library", "cell", "view"))):
            self._warn(
                "对象已变化",
                "Virtuoso 返回的对象与引用不一致，未确认定位；请重新选择对象。",
            )
            return
        target = ref["target"]
        label = "/".join((target["library"], target["cell"], target["view_name"]))
        label += " · " + ref["kind"] + " " + ref["name"]
        if result.get("kind") == "cellview":
            self._status("已置顶 " + label)
        elif result.get("selected"):
            self._status("已选中并高亮 " + label)
        elif result.get("zoomed"):
            self._status("已定位并缩放到 " + label)
        elif result.get("highlighted"):
            self._status("已高亮 " + label)
        else:
            self._status("已定位到 " + label)
        if not signature_matches(ref, result.get("bbox")):
            self._notice(
                "对象已变化",
                "报告发布后目标几何或身份已变化；本次仍定位到当前对象。",
            )

    def _choose_window(self, ref):
        request = self._request + 1
        self._request = request
        if self._pending_provider is not None:
            self._close(self._pending_provider)
            self._pending_provider = None
        provider = None
        try:
            provider = self._provider()
            if provider is None:
                self._warn("无法列出目标窗口", "当前会话没有 Virtuoso 桥接连接。")
                return
            future = provider.windows(ref)
        except Exception as exc:
            self._close(provider)
            self._warn("无法列出目标窗口", str(exc))
            return
        self._pending_provider = provider
        scope = self._scope()
        timer = self._start_timer(
            request, provider,
            "窗口列表读取超时",
            "目标窗口可能已关闭或无响应；请重新打开窗口后重试。",
            self.WINDOW_TIMEOUT_MS,
        )

        def loaded(result):
            if request != self._request or self._closing():
                return
            self._request += 1
            self._stop_timer(timer)
            self._pending_provider = None
            rows = result.get("windows") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                self._warn("无法列出目标窗口", "Virtuoso 返回了无法识别的窗口列表。")
                return
            menu = QMenu(self.window)
            self._window_menu = menu
            added = False
            for row in rows:
                if not isinstance(row, dict) or not row.get("window_ref"):
                    continue
                added = True
                label = row.get("title") or row.get("window") or "Virtuoso 窗口"
                action = menu.addAction(label)

                def choose(_checked=False, window_ref=row.get("window_ref"), cad_ref=ref):
                    self._highlight(cad_ref, window_ref=window_ref)

                action.triggered.connect(choose)
            if added:
                menu.popup(QCursor.pos())
            else:
                self._warn(
                    "无法列出目标窗口",
                    "目标窗口没有可用的窗口引用；可能已关闭。",
                )

        def failed(exc):
            if request != self._request or self._closing():
                return
            self._request += 1
            self._stop_timer(timer)
            self._pending_provider = None
            _title, detail = self._error_info(exc)
            self._warn("无法列出目标窗口", detail)

        def settled(*_args):
            self._stop_timer(timer)
            if self._pending_provider is provider:
                self._pending_provider = None
            self._close(provider)

        try:
            self.window.commands.watch(
                future, success=loaded, failure=failed, settled=settled, scope=scope
            )
        except Exception as exc:
            self._request += 1
            self._stop_timer(timer)
            self._pending_provider = None
            self._close(provider)
            self._warn("无法列出目标窗口", str(exc))


__all__ = ["CADObjectActions"]
