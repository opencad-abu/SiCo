"""Original 0.154 inline review cancellation and recovery through native and flat adapters."""

import threading

import pytest
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)

from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("tool_format", ["native", "flat"])
@pytest.mark.parametrize("history_mode", ["legacy", "paginated"])
def test_review_cancellation_and_restore(tmp_path, gateway, native_codex, monkeypatch,  # noqa: F811
                                         tool_format, history_mode):
    entered, release = threading.Event(), threading.Event()
    calls, frames = [], []
    original, receive = AppServer.request, AppServer._next

    def request(self, method, params, **kwargs):
        if method == "thread/start":
            params = {**params, "historyMode": history_mode}
        calls.append((method, params))
        return original(self, method, params, **kwargs)

    def capture(self, timeout):
        frame = receive(self, timeout)
        frames.append(frame)
        return frame

    def blocked(_request):
        entered.set()
        assert release.wait(15)
        return answer("Late review output")

    monkeypatch.setattr(AppServer, "request", request)
    monkeypatch.setattr(AppServer, "_next", capture)
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "review-cancel") as journal:
        loop = CodexBackend(provider, registry([]), journal, CONTEXT)
        controller = None
        try:
            gateway.responses.put(answer("Existing conversation"))
            assert list(loop.run("Establish review source"))[-1]["kind"] == "task.completed"
            gateway.responses.put(blocked)
            controller = SessionController(loop)
            controller.thread_commands.thread_operation({"kind": "review", "target": {
                "type": "custom", "instructions": "Review the supplied source"}},
                scope=controller.thread_commands.thread_scope(), request_id="explicit-review")
            assert entered.wait(8)
            controller.cancel()
            assert controller.wait(10)
            assert loop.state.task["status"] == "cancelled", journal.events()[-8:]
            assert loop.thread_ops.receipt == {"action": "review", "status": "cancelled"}
            binding = next(e["payload"] for e in journal.events()
                           if e["kind"] == "codex.review.bound")
            outer, internal = binding["turn_id"], binding["execution_turn_id"]
            assert outer != internal
            assert [(m, p) for m, p in calls if m == "turn/interrupt"] == [
                ("turn/interrupt", {"threadId": loop.thread_id, "turnId": internal})]
            assert any(e.get("method") == "turn/completed"
                       and e["params"]["turn"]["id"] == outer
                       and e["params"]["turn"]["status"] == "interrupted" for e in frames)
            assert loop.thread_ops.review.binding is None
        finally:
            release.set()
            if controller:
                controller.close()
                assert controller.wait(10)
            loop.close()
    before = len(calls)
    with SessionJournal(tmp_path, "review-cancel") as journal:
        restored = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            restored.resume_thread()
            assert restored.state.task["status"] == "cancelled"
            assert restored.thread_ops.review.binding is None
            assert not any(m in {"review/start", "turn/start", "turn/interrupt"}
                           for m, _ in calls[before:])
            assert len(gateway.requests) == 2
        finally:
            restored.close()
