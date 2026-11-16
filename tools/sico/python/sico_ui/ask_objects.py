"""Object references for the editor-owned Ask composer."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

from PyQt5.QtCore import QObject

from sico.core.cad_ref import decode_ask_response
from sico.core.window_refs import normalize_window_list

from .commands import outcome

ASK_ERROR_TEXT = {
    "capability_not_available":
        "对象读取未加载，请重新加载 SICO.ils（不要只加载 SICO_quick.il）",
    "source_not_captured": "Ask 未能捕获当前窗口；请重新打开 Ask",
    "stale_context": "窗口已变化，请重新打开 Ask",
    "read_failed": "当前窗口对象读取失败，请稍后重试",
    "invalid_arguments": "对象读取参数无效，请重新打开 Ask",
    "result_too_large": "对象过多，请缩小搜索范围",
}


class AskObjectProvider:
    """Resolve picker requests through the Ask SKILL IPC socket."""

    def __init__(self, window_ref, send_request):
        self.window_ref = window_ref
        self.send_request = send_request
        self.pending = {}
        self.counter = 0
        self.closed = False

    def _request(self, fields, kind):
        if self.closed:
            raise ValueError("Ask 对象选择已关闭")
        self.counter += 1
        request_id = "o" + str(self.counter)
        future = Future()
        self.pending[request_id] = (future, kind)
        try:
            self.send_request(" ".join([*fields[:1], request_id, *fields[1:]]))
        except Exception as exc:
            self.pending.pop(request_id, None)
            future.set_exception(exc)
        return future

    def selection(self, window_ref, offset=0, max_items=50):
        if not isinstance(window_ref, str) or not window_ref:
            raise ValueError("Ask 缺少窗口引用")
        return self._request(
            ["selection", window_ref, str(offset), str(max_items)], "selection"
        )

    def objects(self, window_ref, kind="all", query="", offset=0, max_items=50):
        if kind not in {"all", "instance", "net", "terminal", "shape"}:
            raise ValueError("Invalid object kind")
        if type(offset) is not int or not 0 <= offset <= 4096:
            raise ValueError("Invalid object offset")
        if type(max_items) is not int or not 1 <= max_items <= 50:
            raise ValueError("Invalid object limit")
        encoded = str(query).encode("utf-8").hex() or "-"
        return self._request([
            "objects", window_ref, kind, str(offset), str(max_items),
            encoded,
        ], "objects")

    def window_list(self):
        return self._request(["windows", "0", "200"], "windows")

    def receive(self, message):
        decoded = decode_ask_response(message)
        if decoded is None:
            return
        request_id, value = decoded
        pending = self.pending.pop(request_id, None)
        if pending is None:
            return
        future, kind = pending
        try:
            if kind == "windows":
                value = normalize_window_list(value)
        except ValueError as exc:
            future.set_exception(exc)
            return
        if value.get("ok") is False:
            code = value.get("code")
            message = ASK_ERROR_TEXT.get(code) if isinstance(code, str) else None
            future.set_exception(ValueError(
                str(message or value.get("message") or code or "Ask 对象读取失败")
            ))
        else:
            future.set_result(value)

    def close(self):
        for future, _kind in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()

    def shutdown(self):
        self.closed = True
        self.close()


def watch(future, *, success=None, failure=None, settled=None, scope=None):
    """Adapt future callbacks to the ObjectMentionController contract."""
    def done(result):
        value, error = outcome(result)
        if error is None and success is not None:
            success(value)
        elif error is not None and failure is not None:
            failure(error)
        if settled is not None:
            settled(value, error)

    future.add_done_callback(done)


class AskObjectWindow(QObject):
    """Minimal controller window backed by the Ask composer's local IPC."""

    def __init__(self, parent, input_widget, context, send_request):
        super().__init__(parent)
        context = dict(context or {})
        # Recapture the bound window when @ is used. A reference captured when
        # the Ask window opened can already be stale after a hierarchy change.
        context["window_ref"] = "-"
        self.popup_parent = parent
        self.input = input_widget
        self.window_ref = "-"
        self.presentation = SimpleNamespace(
            submission_context=SimpleNamespace(snapshot=context)
        )
        self.lifecycle = SimpleNamespace(closing=False)
        self.provider = AskObjectProvider(
            self.window_ref, send_request
        )
        self.actions = SimpleNamespace(
            target_picker_provider=lambda: self.provider,
            capture=lambda: None,
        )
        self.commands = SimpleNamespace(watch=watch)
        self.object_mentions = None

    def receive(self, message):
        self.provider.receive(message)

    def close(self):
        self.lifecycle.closing = True
        if self.object_mentions is not None:
            self.object_mentions.close()
            self.object_mentions = None
        self.provider.shutdown()


__all__ = ["AskObjectProvider", "AskObjectWindow"]
