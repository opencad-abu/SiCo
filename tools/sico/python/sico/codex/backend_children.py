"""backend children projections with explicit inputs."""

from ..orchestration import ChildTaskPolicy


def project_collaboration(item, thread_id, collaboration, orchestration, child_inputs, seen, child_scope, emit):
    sender = item.get("senderThreadId")
    if not thread_id or sender != thread_id:
        return
    # Let the generic adapter consume policies explicitly queued by the
    # flow.  The fallback below keeps discovery of unplanned Codex child
    # work visible in the session tree.
    observed = collaboration.observe(item)
    if observed:
        for child in observed:
            child_inputs.own(child)
            child_inputs.settle(child)
            marker = (child.thread_id, child.status, item.get("id"))
            if marker in seen:
                continue
            seen.add(marker)
            emit("codex.child", {
                "thread_id": child.thread_id,
                "parent_thread_id": child.parent_thread_id,
                "name": child.policy.name,
                "status": child.status,
                "tool": item.get("tool", ""),
                **child_scope(child),
            })
        return
    tool = item.get("tool", "")
    prompt = item.get("prompt") or ""
    status_map = {"inProgress": "started", "completed": "completed",
                  "failed": "failed", "interrupted": "interrupted",
                  "errored": "failed", "shutdown": "closed", "pendingInit": "started"}
    states = item.get("agentsStates") or {}
    for child_id in item.get("receiverThreadIds") or ():
        if not isinstance(child_id, str) or not child_id or child_id == thread_id:
            continue
        policy = ChildTaskPolicy(
            name=(
                (prompt.strip().splitlines() or ["Codex 子任务"])[0][:120]
                if prompt.strip() else (tool or "Codex 子任务")
            ),
            instructions=(prompt or "完成父会话分派的任务并报告结果"),
        )
        if child_id not in orchestration.children:
            if tool != "spawnAgent":
                continue
            orchestration.register(child_id, policy, parent_thread_id=thread_id)
        child = orchestration.children[child_id]
        if child.parent_thread_id != thread_id:
            continue
        child_inputs.own(child)
        child_state = states.get(child_id) or {}
        # Completion of spawn/sendInput is not completion of the child.
        effective = child_state.get(
            "status", "closed" if tool == "closeAgent" else child.status)
        orchestration.update(child_id, status_map.get(effective, effective))
        child_inputs.settle(child)
        marker = (child_id, effective, item.get("id"))
        if marker in seen:
            continue
        seen.add(marker)
        emit("codex.child", {
            "thread_id": child_id,
            "parent_thread_id": thread_id,
            "name": child.policy.name,
            "status": status_map.get(effective, effective),
            "tool": tool,
            **child_scope(child),
        })


def project_subagent(item, *, live, thread_id, statuses, orchestration, child_inputs, child_scope, emit):
    child_id, status = item.get("agentThreadId"), item.get("kind")
    if (not thread_id or not isinstance(child_id, str) or not child_id
            or child_id == thread_id
            or status not in {"started", "interacted", "interrupted", "completed"}):
        return
    name = item.get("agentPath") or "Codex 子任务"
    previous = statuses.get(child_id, {})
    effective = status if status != "interacted" else previous.get("status", "started")
    row = {"thread_id": child_id, "parent_thread_id": thread_id,
           "name": name, "status": effective, "tool": "subAgentActivity"}
    if child_id not in orchestration.children:
        orchestration.register(child_id, ChildTaskPolicy(name=name, instructions=""),
                                    parent_thread_id=thread_id)
    child = orchestration.children[child_id]
    if child.parent_thread_id != thread_id:
        return
    if live:
        child_inputs.own(child)
    # A historical completion may update the tree, but cannot grant input ownership.
    orchestration.update(child_id, effective)
    child_inputs.settle(child)
    row.update(child_scope(child))
    if previous == row:
        return
    emit("codex.child", row)
    statuses[child_id] = row
