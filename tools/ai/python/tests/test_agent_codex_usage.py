"""Codex-native token usage is retained in the session snapshot."""

from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from sico.service.controller import SessionController


def test_codex_token_usage_prefers_native_cumulative_total(tmp_path):
    context = BoundContext("instance", "generation", "target", {"valid": True})
    with SessionJournal(tmp_path, "codex-usage") as journal:
        backend = CodexBackend(None, ToolRegistry(), journal, context)
        backend._begin("检查", context, "input", "chat")
        backend._record_token_usage({
            "threadId": "thread",
            "turnId": "turn-1",
            "tokenUsage": {
                "last": {"inputTokens": 11, "outputTokens": 3},
                "total": {"inputTokens": 11, "outputTokens": 3},
            },
        })
        backend._record_token_usage({
            "threadId": "thread",
            "turnId": "turn-2",
            "tokenUsage": {
                "last": {"inputTokens": 7, "outputTokens": 2},
                "total": {"inputTokens": 18, "outputTokens": 5},
            },
        })
        assert backend.state.task["input_tokens"] == 18
        assert backend.state.task["output_tokens"] == 5
        usage = [event for event in journal.events() if event["kind"] == "token.usage"]
        assert usage[-1]["payload"] == {
            "input_tokens": 18,
            "output_tokens": 5,
            "total_tokens": 23,
            "context_usage": None,
        }


def test_context_occupancy_uses_last_response_and_recovers_from_journal(tmp_path):
    context = BoundContext("instance", "generation", "target", {"valid": True})
    with SessionJournal(tmp_path, "context-usage") as journal:
        backend = CodexBackend(None, ToolRegistry(), journal, context)
        from codex_thread_fixture import bind_thread
        bind_thread(backend)
        backend._begin("检查", context, "input", "chat")
        for used in (80000, 12000):
            backend._record_token_usage({"threadId": "thread", "turnId": "turn", "tokenUsage": {
                "last": {"inputTokens": used - 1000, "outputTokens": 1000, "totalTokens": used},
                "total": {"inputTokens": 300000, "outputTokens": 10000},
                "modelContextWindow": 200000}})
            assert backend.state.task["context_usage"] == {"used": used, "capacity": 200000}
        controller = SessionController(backend)
        assert controller.updates.read_updates()["snapshot"]["task"]["context_usage"] == {
            "used": 12000, "capacity": 200000}
        backend._finish("completed")
    with SessionJournal(tmp_path, "context-usage") as journal:
        backend = CodexBackend(None, ToolRegistry(), journal, context)
        assert backend.state.task["context_usage"] == {"used": 12000, "capacity": 200000}
        backend._begin("下一任务", context, "next", "chat")
        assert "context_usage" not in backend.state.task
        backend._record_token_usage({"tokenUsage": {
            "last": {"inputTokens": 50, "outputTokens": 1, "totalTokens": 51},
            "modelContextWindow": None}})
        assert backend.state.task["context_usage"] is None
