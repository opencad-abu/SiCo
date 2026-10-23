"""Own a session's auxiliary recap job without occupying its task consumer."""

import threading

from ..codex.recap_request import generate
from ..codex.runtime import CodexRuntime
from ..core.session_events import SYSTEM_ORIGINS
from .recap_history import prompt


class SessionRecap:
    def __init__(self, loop, *, lock, execution, history):
        self.loop, self.lock, self.execution, self.history = loop, lock, execution, history
        self.thread = None
        self.stop = threading.Event()
        self.result = None
        self.request_id = None
        self.recapped = frozenset()

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def identity(self):
        view = self.execution()
        return (view.task.get("id", ""), getattr(self.loop, "thread_id", None), view.current)

    def completed(self):
        return frozenset(row["task_id"] for row in self.history.rows.values()
                         if row["status"] == "completed"
                         and row.get("origin") not in SYSTEM_ORIGINS)

    def status(self):
        with self.lock:
            completed = self.completed()
            valid = self.result and self.result[0] == self.identity()
            return {"supported": self.loop.support.supports("recap"),
                    "task_id": self.execution().task.get("id", ""),
                    "busy": self.busy, "request_id": self.request_id,
                    "completed": len(completed), "new_completed": len(completed - self.recapped),
                    "text": self.result[1] if valid else "",
                    "error": self.result[2] if valid else ""}

    def start(self, *, expected_task, request_id, automatic=False):
        with self.lock:
            view = self.execution()
            view.require_available()
            if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 100
                    or type(automatic) is not bool):
                raise ValueError("纪要请求标识无效")
            self.loop.support.require("recap")
            if (view.busy or view.pending or view.task.get("status") == "needs_reconcile"
                    or expected_task != view.task.get("id", "")):
                raise ValueError("请等待当前任务结束并处理待办后生成纪要")
            if self.busy or self.request_id == request_id:
                return self.status()
            completed = self.completed()
            failed = self.result and self.result[0] == self.identity() and self.result[2]
            if automatic and (failed or len(completed) < 3 or len(completed - self.recapped) < 2):
                return self.status()
            source = prompt(self.loop.journal)
            identity = self.identity()
            model = self.loop.turn_options.selected.get("model") or self.loop.provider.model
            self.stop.clear()
            self.request_id, self.result = request_id, None
            self.thread = threading.Thread(target=self._run,
                args=(identity, source, model, completed), name="sico-recap")
            self.thread.start()
            return self.status()

    def _run(self, identity, source, model, completed):
        def cancelled():
            with self.lock:
                view = self.execution()
                return (self.stop.is_set() or view.closing or view.shutdown or view.busy
                        or bool(view.pending) or self.identity() != identity)

        runtime, text, error = None, "", ""
        try:
            runtime = CodexRuntime(self.loop.provider, self.loop.journal, None,
                                   auxiliary=True, cancelled=cancelled)
            text = generate(runtime, source, model, cancelled)
        except Exception as exc:
            from .diagnostics import diagnostics

            diagnostics.submit(__name__, "Recap generation ended: " + type(exc).__name__)
            error = "纪要生成失败，可点击刷新重试。"
        finally:
            if runtime is not None:
                try:
                    runtime.close()
                except (OSError, RuntimeError, ValueError):
                    text, error = "", "纪要进程清理未确认，请重新打开会话核对。"
            with self.lock:
                if not cancelled():
                    self.result = identity, text, error
                    if text:
                        self.recapped = completed

    def close(self):
        self.stop.set()

    def wait(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)
        return not self.busy
