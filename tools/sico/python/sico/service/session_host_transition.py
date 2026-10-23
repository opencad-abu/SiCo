"""Durable host transition at a conversation boundary; previous task evidence is immutable."""

import os

from ..core.contracts import TERMINAL, BoundContext, identifier
from ..storage.journal import durable_task


def validate_transition(payload, previous, current, project):
    if set(payload) != {"source", "context", "bridge_identity"}:
        raise ValueError("Invalid conversation host transition")
    source = BoundContext.from_record(payload["source"])
    target = BoundContext.from_record(payload["context"])
    identity = payload["bridge_identity"]
    # 行内状态不再重复携带任务文本，新旧格式行之间的比较必须按持久化投影进行，
    # 否则跨版本的会话迁移会在证据层被拒绝。
    if (source != previous.context or target != current.context
            or previous.task and previous.task.get("status") not in TERMINAL | {"needs_reconcile"}
            or durable_task(previous.task) != durable_task(current.task)
            or previous.messages != current.messages
            or set(identity) != {"bridge_id", "router_id"}):
        raise ValueError("Conversation host transition does not match prior state")
    for value in identity.values():
        identifier(value)
    if not os.path.samefile(project, target.snapshot.get("cwd", "")):
        raise ValueError("Conversation host belongs to another project")
    return dict(identity)


def transition_host(loop, context, descriptor):
    source = loop.state.context
    if source == context:
        return
    # An interrupted task is not executing; its pending calls stay unresolved
    # in the journal and are reconciled separately. Refusing the host change
    # here would strand a recovered conversation on the dead generation.
    if loop.state.task and loop.state.task.get("status") not in TERMINAL | {"needs_reconcile"}:
        raise ValueError("请先在原会话核对中断任务，再继续连接当前工程宿主")
    previous = type(loop.state).from_record(loop.state.record())
    payload = dict(source=source.record(), context=context.record(),
                   bridge_identity={k: descriptor[k] for k in ("bridge_id", "router_id")})
    loop.state.context = context
    try:
        validate_transition(payload, previous, loop.state, loop.journal.root.parents[2])
        loop.journal.append("session.host_changed", payload, loop.state)
    except BaseException:
        loop.state.context = source
        raise
