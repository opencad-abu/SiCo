import pytest
from sico.orchestration.thread_binding import ThreadBinding

from sico.orchestration import (
    ChildTaskPolicy,
    CodexCollaborationClient,
    CodexCollaborationError,
    CopilotTaskOrchestrator,
)


def test_child_task_policy_is_domain_only_and_lifecycle_is_codex_owned():
    policy = ChildTaskPolicy(
        "采集 PDK 数据",
        "Collect every device metadata record and write the validated cache.",
        frozenset({"get_pdk_device", "advance_pdk_collection"}),
        completion=lambda result: result.get("ready") is True,
    )
    assert "advance_pdk_collection" in policy.prompt({"library": "smic28hkmg"})
    orchestration = CopilotTaskOrchestrator(ThreadBinding({"thread_id": "parent"}))
    child = orchestration.register("child", policy)
    assert child.parent_thread_id == "parent"
    assert not child.finished
    assert not child.accept({"status": "completed", "ready": False})
    assert child.finished
    assert orchestration.active() == ()


def test_codex_collaboration_client_only_forwards_native_tool_calls():
    calls = []
    policy = ChildTaskPolicy("采集 PDK 数据", "collect", completion=lambda r: r.get("ready") is True)
    client = CodexCollaborationClient(
        CopilotTaskOrchestrator(ThreadBinding({"thread_id": "parent"})),
        invoke=lambda tool, payload: calls.append((tool, payload)))
    client.expect_spawn(policy)
    children = client.observe(
        {
            "type": "collabAgentToolCall",
            "tool": "spawnAgent",
            "senderThreadId": "parent",
            "receiverThreadIds": ["child"],
            "agentsStates": {"child": {"status": "running"}},
        }
    )
    assert len(children) == 1 and children[0].thread_id == "child"
    client.send(children[0], "continue")
    client.wait(children[0], timeout=3)
    client.interrupt(children[0])
    client.close(children[0])
    assert [name for name, _ in calls] == ["sendInput", "wait", "interruptAgent", "closeAgent"]
    assert client.accept(children[0], {"status": "completed", "ready": True})


def test_client_rejects_foreign_events_and_missing_native_hook():
    policy = ChildTaskPolicy("job", "collect")
    client = CodexCollaborationClient(CopilotTaskOrchestrator(ThreadBinding({"thread_id": "parent"})))
    client.expect_spawn(policy)
    assert client.observe({
        "type": "collabAgentToolCall",
        "tool": "spawnAgent",
        "senderThreadId": "other",
        "receiverThreadIds": ["foreign"],
    }) == ()
    child = client.register("child", policy)
    with pytest.raises(CodexCollaborationError, match="model-owned"):
        client.wait(child)
    with pytest.raises(ValueError, match="owned"):
        client.wait(type("Foreign", (), {"parent_thread_id": "other"})())
