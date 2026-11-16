"""Private-project acceptance support using the actual SiCo backend and audit path."""

import json
import time

from sico.codex.feature_groups import CONFIGURED_EVENT
from sico.core.contracts import RunState


def configure_document_session(journal, context):
    """Keep the acceptance model within documentation tools, with native approvals."""
    if any(row["kind"] == CONFIGURED_EVENT for row in journal.events()):
        return
    journal.append(
        CONFIGURED_EVENT,
        {
            "groups": {
                "multi_agent": {"features.multi_agent": False, "features.multi_agent_v2": False},
                "shell": {"features.shell_tool": False, "features.shell_snapshot": False},
                "web_search": {"web_search": "disabled"},
            }
        },
        RunState(context),
    )


def assert_no_knowledge_approval(backend):
    """Read-only knowledge tools are default-approved; a prompt is a policy regression."""
    for identity, pending in list(backend.audit.pending.items()):
        row = backend.audit.index.audits[identity]
        if row["status"] != "pending":
            continue
        spec = row.get("elicitation", {})
        if (pending.get("source") == "elicitation"
                and pending["binding"].get("server_name") == "sico_knowledge"):
            raise AssertionError(
                "read-only knowledge tools must not prompt: " + str(spec.get("message"))
            )


def run_turn(backend, question, *, timeout=180, max_approvals=12):
    started = time.monotonic()
    events = []
    count = 0
    budget_exceeded = False
    for event in backend.run(
        question, cancelled=lambda: budget_exceeded or time.monotonic() - started > timeout
    ):
        events.append(event)
        count = sum(
            row.get("task_id") == backend.state.task["id"] and bool(row.get("elicitation"))
            for row in backend.audit.index.audits.values()
        )
        if count > max_approvals:
            budget_exceeded = True
            continue
        assert_no_knowledge_approval(backend)
    result = {
        "status": backend.state.task["status"],
        "events": events,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "thread_id": backend.thread_id,
        "budget_exceeded": budget_exceeded,
        "approval_count": count,
        "resources": backend.resources.published,
    }
    if backend.runtime:
        history = backend.runtime.rpc.request(
            "thread/read", {"threadId": backend.thread_id, "includeTurns": True}
        )
        turns = history["thread"]["turns"]
        result["items"] = turns[-1]["items"] if turns else []
        result["prior_search_hits"] = [
            hit
            for turn in turns[:-1]
            for item in turn["items"]
            if item.get("type") == "mcpToolCall"
            and item.get("server") == "sico_knowledge"
            and item.get("tool") == "search"
            and item.get("result")
            for hit in tool_value(item).get("hits", [])
        ]
    return result


def knowledge_calls(result):
    return [
        item
        for item in result.get("items", [])
        if item.get("type") == "mcpToolCall" and item.get("server") == "sico_knowledge"
    ]


def tool_value(item):
    content = item.get("result", {}).get("content", [])
    return json.loads(next(row["text"] for row in content if row.get("type") == "text"))


def final_text(result):
    messages = [item for item in result.get("items", []) if item.get("type") == "agentMessage"]
    final = [item for item in messages if item.get("phase") == "final_answer"]
    return "\n".join(item["text"] for item in (final or messages[-1:]))
