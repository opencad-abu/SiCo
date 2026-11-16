"""The ring's published occupancy matches the pinned app-server usage receipt."""

from test_agent_codex_native import CONTEXT, answer, gateway, native_codex, registry, settings

from sico.codex.backend import CodexBackend
from sico.storage.journal import SessionJournal


def test_context_occupancy_matches_native_usage_receipt(tmp_path, gateway, native_codex):
    gateway.responses.put(answer("Context usage probe completed."))
    with SessionJournal(tmp_path, "context-usage") as journal:
        loop = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
        received = []
        try:
            loop._ensure_runtime()
            original = loop.runtime.rpc.next
            def next_event(*args, **kwargs):
                event = original(*args, **kwargs)
                if event.get("method") == "thread/tokenUsage/updated":
                    received.append(event["params"]["tokenUsage"])
                return event
            loop.runtime.rpc.next = next_event
            events = list(loop.run("Report this response's context usage."))
            assert events[-1]["kind"] == "task.completed"
            assert received and received[-1]["modelContextWindow"] > 0
            assert loop.state.task["context_usage"] == {
                "used": received[-1]["last"]["totalTokens"],
                "capacity": received[-1]["modelContextWindow"]}
        finally:
            loop.close()
