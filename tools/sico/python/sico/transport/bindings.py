"""Logical-session ownership of immutable targets within an instance Bridge."""

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.contracts import BoundContext, CircuitCallError


def binding_error(code, message, context, owner=None):
    return CircuitCallError(
        code,
        message,
        data={
            "category": "binding",
            "stage": "binding_registry",
            "target_id": context.target_id,
            "instance_id": context.instance_id,
            "generation": context.generation,
            "owner_session_id": owner,
            "operation_dispatched": False,
            "automatic_resume_allowed": False,
        },
    )


def resource_keys(context):
    snapshot = context.snapshot
    cellview = snapshot.get("cellview")
    ade = snapshot.get("ade_session")
    if not isinstance(cellview, dict) and not ade:
        return frozenset()  # CIW and windowless project anchors are shared.
    prefix = (context.instance_id, context.generation)
    keys = set()
    window = snapshot.get("window")
    if window:
        keys.add((*prefix, "window", str(window)))
    if ade:
        keys.add((*prefix, "ade", str(ade)))
    if isinstance(cellview, dict):
        names = tuple(cellview.get(key) for key in ("lib", "cell", "view"))
        if all(isinstance(name, str) and name for name in names):
            keys.add((*prefix, "cellview", *names))
            path = snapshot.get("library_path")
            if isinstance(path, str) and Path(path).is_absolute():
                keys.add((*prefix, "path", str(Path(path).resolve()), *names[1:]))
    return frozenset(keys)


def project_key(snapshot):
    """Project identity for session anchoring; symlinked spellings are one project.

    The launcher accepts a work directory through its shell alias (for example
    ``$PWD``) while the SKILL-side snapshot may carry the resolved path, and the
    desktop launch check already uses ``samefile``. The binding registry must be
    equally tolerant, otherwise a quick input fails with "A session cannot bind
    another captured project" for the very same directory.
    """

    cwd = snapshot.get("cwd") if isinstance(snapshot, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return None
    try:
        return os.path.realpath(cwd)
    except OSError:
        return cwd


@dataclass
class SessionBindings:
    anchor: BoundContext
    default_target_id: str
    targets: dict = field(default_factory=dict)
    releasing: bool = False
    holds: dict = field(default_factory=dict)
    revision: int = 0
    timeout_seconds: int = 30
    auto_bind: bool = True
    default_explicit: bool = False
    binding_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    reservations: dict = field(default_factory=dict)
    invalidated: dict = field(default_factory=dict)
    operations: dict = field(default_factory=dict)

    def snapshot(self):
        return {
            "contract": "cad_ai_session_bindings.v1",
            "instance_id": self.anchor.instance_id,
            "generation": self.anchor.generation,
            "target_id": self.default_target_id,
            "default_target_id": self.default_target_id,
            "anchor_target_id": self.anchor.target_id,
            "targets": [context.record() for context in self.targets.values()],
            "state": "release_pending" if self.releasing else "active",
            "holds": dict(self.holds),
            "revision": self.revision,
            "binding_id": self.binding_id,
            "default_explicit": self.default_explicit,
            "policy": {"auto_bind": self.auto_bind, "timeout_seconds": self.timeout_seconds},
            "reservations": [dict(row) for row in self.reservations.values()],
            "invalidated": dict(self.invalidated),
            "operations": [dict(row) for row in self.operations.values()
                           if row["request_id"] in self.holds or row.get("result")][-64:],
        }


class BindingRegistry:
    """Caller holds the broker condition lock for all registry operations."""

    def __init__(self):
        self.sessions = {}
        self.requests = {}
        self.owners = {}

    def add(self, session_id, context):
        context = BoundContext.from_record(context.record())
        record = self.sessions.get(session_id)
        if record:
            anchor = record.anchor
            if (anchor.instance_id, anchor.generation) != (context.instance_id, context.generation):
                raise ValueError("A session cannot bind another Virtuoso instance or generation")
            if project_key(anchor.snapshot) != project_key(context.snapshot):
                raise ValueError("A session cannot bind another captured project")
            if record.releasing:
                raise binding_error("binding_busy", "Session ownership is pending release", context)
            previous = record.targets.get(context.target_id)
            if previous and previous.record() != context.record():
                raise ValueError("Target identity cannot be changed")
        keys = resource_keys(context)
        for key in keys:
            owner = self.owners.get(key)
            if owner is not None and owner != session_id:
                raise binding_error(
                    "binding_conflict", "Target is owned by another session", context, owner
                )
        if record is None:
            record = SessionBindings(context, context.target_id)
            self.sessions[session_id] = record
        if context.target_id not in record.targets:
            record.revision += 1
        record.targets[context.target_id] = context
        for key in keys:
            self.owners[key] = session_id
        return record

    def require(self, session_id, context):
        record = self.sessions.get(session_id)
        if not record or record.releasing or record.targets.get(context.target_id) != context:
            raise binding_error(
                "binding_target_not_owned", "Target is not bound to this session", context
            )
        if context.target_id in record.invalidated:
            raise binding_error("binding_target_closed", "Target window closed or changed", context)
        return record

    def select(self, session_id, context):
        record = self.require(session_id, context)
        if record.default_target_id != context.target_id:
            record.default_target_id = context.target_id
            record.revision += 1
        return record

    def release(self, session_id, context):
        record = self.sessions.get(session_id)
        if not record or record.releasing or record.targets.get(context.target_id) != context:
            raise binding_error(
                "binding_target_not_owned", "Target is not bound to this session", context
            )
        if record.default_target_id == context.target_id:
            raise binding_error(
                "binding_busy", "Select another default before releasing this target", context
            )
        del record.targets[context.target_id]
        record.invalidated.pop(context.target_id, None)
        released_keys = resource_keys(context)
        reserved_keys = set().union(
            *(
                resource_keys(BoundContext.from_record(row["context"]))
                for row in record.reservations.values()
            )
        )
        record.reservations = {
            key: row
            for key, row in record.reservations.items()
            if not resource_keys(BoundContext.from_record(row["context"])) & released_keys
        }
        retained = set().union(*(resource_keys(target) for target in record.targets.values()))
        retained.update(
            key
            for row in record.reservations.values()
            for key in resource_keys(BoundContext.from_record(row["context"]))
        )
        for key in (resource_keys(context) | reserved_keys) - retained:
            self.owners.pop(key, None)
        record.revision += 1
        return record

    def reserve(self, session_id, context, request_id):
        record = self.sessions[session_id]
        keys = resource_keys(context)
        for key in keys:
            owner = self.owners.get(key)
            if owner is not None and owner != session_id:
                raise binding_error(
                    "binding_conflict",
                    "Output resource is owned by another session",
                    context,
                    owner,
                )
        record.reservations[request_id] = dict(reservation_id=request_id, context=context.record())
        for key in keys:
            self.owners[key] = session_id
        return record

    def release_resource(self, session_id, reservation_id):
        record = self.sessions[session_id]
        row = record.reservations.pop(reservation_id)
        retained = set().union(*(resource_keys(target) for target in record.targets.values()))
        retained.update(
            key
            for other in record.reservations.values()
            for key in resource_keys(BoundContext.from_record(other["context"]))
        )
        for key in resource_keys(BoundContext.from_record(row["context"])) - retained:
            self.owners.pop(key, None)
        record.revision += 1
        return record

    def remove(self, session_id):
        record = self.sessions.pop(session_id, None)
        if record:
            self.owners = {key: owner for key, owner in self.owners.items() if owner != session_id}
        return record

    def clear(self):
        self.sessions.clear()
        self.owners.clear()

    def reap(self, routers):
        for session_id, record in tuple(self.sessions.items()):
            if not record.releasing or record.holds or self.requests.get(session_id):
                continue
            router = routers.get(record.anchor.instance_id, record.anchor.generation)
            if router and router.session_unresolved(session_id):
                continue
            self.remove(session_id)

    def busy(self, session_id, *, automatic, routers):
        record = self.sessions.get(session_id)
        if not record or record.releasing or record.holds:
            return True
        router = routers.get(record.anchor.instance_id, record.anchor.generation)
        if automatic:
            # Active reads are allowed, but unknown outcomes block timeout decisions.
            return bool(router and router.has_unknown())
        return bool(self.requests.get(session_id)
                    or (router and router.session_unresolved(session_id)))

    def retire_generation(self, peer, events, diagnostic, bridge_id):
        from .relay import emit_diagnostic

        retired_sessions = [
            session_id
            for session_id, record in self.sessions.items()
            if record.anchor.instance_id == peer.instance_id
            and record.anchor.generation != peer.generation
        ]
        for session_id in retired_sessions:
            events.invalidate_session(session_id)
            self.remove(session_id)
            emit_diagnostic(
                diagnostic,
                "bridge.session_retired",
                bridge_id=bridge_id,
                session_id=session_id,
                instance_id=peer.instance_id,
                generation=peer.generation,
            )

    def register(self, session_id, context, routers, bridge_id, diagnostic):
        from .relay import emit_diagnostic

        existing = self.sessions.get(session_id)
        already_bound = bool(existing and context.target_id in existing.targets)
        record = self.add(session_id, context)
        router = routers.get(context.instance_id, context.generation)
        snapshot = {
            "bridge_id": bridge_id,
            "session_id": session_id,
            **record.snapshot(),
            "router_id": router.router_id if router else None,
        }
        if not already_bound:
            emit_diagnostic(diagnostic,
                            "bridge.session_target_added" if existing else "bridge.session_registered",
                            bridge_id=bridge_id, session_id=session_id,
                            instance_id=context.instance_id, generation=context.generation,
                            target_id=context.target_id, default_target_id=record.default_target_id,
                            target_count=len(record.targets))
        return snapshot
