"""Durable host transition at a conversation boundary; previous task evidence is immutable."""

import os

from ..core.contracts import TERMINAL, BoundContext, identifier


def validate_transition(payload, previous, current, project):
    if set(payload) != {"source", "context", "bridge_identity"}:
        raise ValueError("Invalid conversation host transition")
    source = BoundContext.from_record(payload["source"])
    target = BoundContext.from_record(payload["context"])
    identity = payload["bridge_identity"]
    if (source != previous.context or target != current.context
            or previous.task and previous.task.get("status") not in TERMINAL
            or previous.task != current.task or previous.messages != current.messages
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
    if loop.state.task and loop.state.task.get("status") not in TERMINAL:
        loop.acknowledge_interrupted()
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
