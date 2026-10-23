"""Non-task actions for durable CAD object links."""

from __future__ import annotations

from PyQt5.QtCore import QObject
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QMenu

from sico.core.cad_ref import normalize_ref, parse_object_link, signature_matches


class CADObjectActions(QObject):
    """Resolve a link click through the captured target bridge, never a task."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def open_link(self, value):
        try:
            session, ref = parse_object_link(value)
        except ValueError:
            return False
        self.open(session, ref)
        return True

    def open(self, session, ref):
        try:
            ref = normalize_ref(ref)
        except ValueError as exc:
            self.window.info_app.warning("对象引用无效", str(exc))
            return
        current = getattr(self.window.page.session, "session_id", "")
        if session != current:
            self.window.info_app.warning(
                "对象引用不属于当前会话",
                "请先打开引用所属的会话，再定位 Virtuoso 对象。",
            )
            return
        if self.window.lifecycle.closing:
            return
        try:
            provider = self.window.actions.target_picker_provider()
        except (ValueError, RuntimeError) as exc:
            self.window.info_app.warning("无法定位对象", str(exc))
            return
        if provider is None:
            self.window.info_app.warning("无法定位对象", "当前会话没有 Virtuoso 桥接连接。")
            return
        self._highlight(ref, provider)

    def _highlight(self, ref, provider=None, window_ref=None):
        if provider is None:
            try:
                provider = self.window.actions.target_picker_provider()
            except (ValueError, RuntimeError) as exc:
                self.window.info_app.warning("无法定位对象", str(exc))
                return
            if provider is None:
                self.window.info_app.warning("无法定位对象", "当前会话没有 Virtuoso 桥接连接。")
                return
        scope = self.window.actions.capture()
        try:
            future = provider.highlight(ref, window_ref)
        except (ValueError, RuntimeError) as exc:
            provider.close()
            self.window.info_app.warning("无法定位对象", str(exc))
            return

        def loaded(result):
            target = ref["target"]
            label = "/".join((target["library"], target["cell"], target["view"]))
            label += " · " + ref["kind"] + " " + ref["name"]
            if result.get("zoomed"):
                self.window.status.setText("已定位并缩放到 " + label)
            elif result.get("highlighted"):
                self.window.status.setText("已高亮 " + label)
            else:
                self.window.status.setText("已定位到 " + label)
            if not signature_matches(ref, result.get("bbox")):
                self.window.info_app.notice(
                    "对象已变化",
                    "报告发布后目标几何或身份已变化；本次仍定位到当前对象。",
                )

        def failed(exc):
            if getattr(exc, "code", "") == "ambiguous_target" and window_ref is None:
                self._choose_window(ref)
                return False
            self.window.info_app.warning(
                "无法定位对象",
                str(exc) + "\n目标未打开、已改变或引用不属于当前设计。",
            )
            return False

        def settled(*_args):
            provider.close()

        self.window.commands.watch(
            future, success=loaded, failure=failed, settled=settled, scope=scope
        )

    def _choose_window(self, ref):
        provider = None
        try:
            provider = self.window.actions.target_picker_provider()
            future = provider.windows(ref)
        except (ValueError, RuntimeError) as exc:
            if provider is not None:
                provider.close()
            self.window.info_app.warning("无法列出目标窗口", str(exc))
            return
        scope = self.window.actions.capture()

        def loaded(result):
            menu = QMenu(self.window)
            self._window_menu = menu
            added = False
            for row in result["windows"]:
                if not row.get("window_ref"):
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
                self.window.info_app.warning(
                    "无法列出目标窗口", "目标窗口没有可用的窗口引用。"
                )

        def failed(exc):
            self.window.info_app.warning("无法列出目标窗口", str(exc))

        def settled(*_args):
            provider.close()

        self.window.commands.watch(
            future, success=loaded, failure=failed, settled=settled, scope=scope
        )


__all__ = ["CADObjectActions"]
