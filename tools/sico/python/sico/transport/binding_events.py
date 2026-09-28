"""Bridge-owned binding decisions; persistence precedes visible ownership changes."""

import json
import os
import time
import uuid
from copy import deepcopy

from ..core.contracts import BoundContext, CircuitCallError, identifier, json_copy
from ..storage.journal import MAX_RECORD, open_private, sync_directory
from .bindings import binding_error, resource_keys
from .router_journal import record_digest

CHOICES = {"add_and_select", "add_only", "decline"}


class BindingJournal:
    """One private, non-replayed journal per Bridge lifetime."""

    def __init__(self, path):
        self.path = path
        self.fd = open_private(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        sync_directory(path.parent)

    def append(self, event):
        if self.fd < 0:
            raise OSError("Binding journal is closed")
        envelope = dict(
            contract="cad_ai_binding_journal.v1", event=event, sha256=record_digest(event)
        )
        data = (json.dumps(envelope, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(data) > MAX_RECORD:
            raise ValueError("Binding event exceeds size limit")
        try:
            view = memoryview(data)
            while view:
                count = os.write(self.fd, view)
                if count <= 0:
                    raise OSError("Binding journal write did not progress")
                view = view[count:]
            os.fsync(self.fd)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class BindingEvents:
    """All methods run under the broker condition lock, including deadline ticks."""

    def __init__(
        self, registry, live, busy, *, persist=None, clock=time.monotonic, wall_clock=time.time
    ):
        self.registry, self.live, self.busy = registry, live, busy
        self.persist = persist
        self.clock, self.wall_clock = clock, wall_clock
        self.proposals = {}
        self.deadlines = {}
        self.events = []
        self.fault = ""

    def _publish(self, kind, payload, candidate=None):
        if self.fault:
            raise OSError("Binding journal is unavailable: " + self.fault)
        event = dict(sequence=len(self.events) + 1, kind=kind, payload=json_copy(payload))
        if self.persist:
            try:
                self.persist(event)
            except (OSError, ValueError) as error:
                self.fault = str(error)
                raise
        if candidate is not None:
            self.registry.sessions.clear()
            self.registry.sessions.update(candidate.sessions)
            self.registry.owners.clear()
            self.registry.owners.update(candidate.owners)
        self.events.append(event)
        return event

    def _session(self, session_id):
        identifier(session_id)
        record = self.registry.sessions.get(session_id)
        if record is None or record.releasing:
            raise ValueError("Binding session is unavailable")
        return record

    def _error(self, code, proposal, message, **details):
        context = BoundContext.from_record(proposal["target"])
        error = binding_error(code, message, context)
        record = self.registry.sessions.get(proposal["session_id"])
        error.data.update(
            event_id=proposal["event_id"],
            session_id=proposal["session_id"],
            expected_revision=proposal["expected_revision"],
            current_revision=record.revision if record else None,
            **details,
        )
        return error

    def propose(
        self,
        session_id,
        context,
        *,
        task_id="",
        origin_request_id="",
        reason="",
        created_by_workflow=False,
    ):
        record = self._session(session_id)
        for value in (task_id, origin_request_id):
            if value:
                identifier(value)
        if not isinstance(reason, str) or len(reason) > 2000:
            raise ValueError("Invalid binding reason")
        if created_by_workflow and (not task_id or not origin_request_id):
            raise ValueError("Workflow binding requires its originating task and request")
        if not self.live(context):
            raise binding_error(
                "binding_event_changed", "Binding target is no longer registered", context
            )
        anchor = record.anchor
        if (anchor.instance_id, anchor.generation, anchor.snapshot.get("cwd")) != (
            context.instance_id,
            context.generation,
            context.snapshot.get("cwd"),
        ):
            raise binding_error("binding_event_changed", "Binding anchor changed", context)
        for proposal in self.proposals.values():
            if (
                proposal["session_id"] == session_id
                and proposal["status"] == "proposed"
                and proposal["target"] == context.record()
            ):
                return json_copy(proposal)
        if (
            sum(
                p["status"] == "proposed" and p["session_id"] == session_id
                for p in self.proposals.values()
            )
            >= 16
        ):
            raise binding_error("binding_busy", "Too many pending binding proposals", context)
        owners = sorted(
            {
                self.registry.owners[key]
                for key in resource_keys(context)
                if key in self.registry.owners
            }
        )
        now = self.wall_clock()
        proposal = dict(
            event_id=uuid.uuid4().hex,
            session_id=session_id,
            task_id=task_id,
            origin_request_id=origin_request_id,
            target_id=context.target_id,
            target=context.record(),
            source_target_id=record.default_target_id,
            instance_id=context.instance_id,
            generation=context.generation,
            expected_revision=record.revision,
            binding_id=record.binding_id,
            reason=reason,
            created_by_workflow=bool(created_by_workflow),
            ownership_observation=owners,
            created_at=now,
            deadline=now + record.timeout_seconds,
            timeout_policy="add_and_select"
            if record.auto_bind and created_by_workflow
            else "expire",
            status="proposed",
        )
        deadline = self.clock() + record.timeout_seconds
        self._publish("binding.proposed", proposal)
        self.proposals[proposal["event_id"]] = proposal
        self.deadlines[proposal["event_id"]] = deadline
        return json_copy(proposal)

    def _finish(self, proposal, choice, actor, *, error=None, candidate=None):
        record = (candidate or self.registry).sessions.get(proposal["session_id"])
        result = dict(
            proposal,
            status="invalidated" if error else "resolved",
            resolution=choice,
            actor=actor,
            resolved_at=self.wall_clock(),
            selected_default=record.default_target_id if record else None,
            revision=record.revision if record else None,
        )
        if error:
            result["error"] = dict(code=error.code, message=str(error), data=error.data)
        self._publish("binding.invalidated" if error else "binding.resolved", result, candidate)
        self.proposals[proposal["event_id"]] = result
        self.deadlines.pop(proposal["event_id"], None)
        return json_copy(result)

    def resolve(self, session_id, event_id, choice, *, actor="user"):
        if choice not in CHOICES or actor not in {"user", "timeout_policy"}:
            raise ValueError("Invalid binding resolution")
        proposal = self.proposals.get(identifier(event_id))
        if not proposal or proposal["session_id"] != session_id:
            raise ValueError("Binding event does not belong to this session")
        session = self.registry.sessions.get(session_id)
        if session and session.binding_id != proposal["binding_id"]:
            raise self._error("binding_event_changed", proposal, "Binding session was reopened")
        if proposal["status"] != "proposed":
            if (
                proposal.get("actor") == actor
                and proposal.get("resolution") == choice
                and not proposal.get("error")
            ):
                return json_copy(proposal)
            if proposal.get("error"):
                error = proposal["error"]
                raise CircuitCallError(error["code"], error["message"], data=error["data"])
            code = (
                "binding_event_expired"
                if proposal["actor"] == "timeout_policy"
                else "binding_event_changed"
            )
            raise self._error(code, proposal, "Binding event has already been resolved")
        if actor == "user" and self.clock() >= self.deadlines[event_id]:
            self.resolve(session_id, event_id, "add_and_select", actor="timeout_policy")
            raise self._error(
                "binding_event_expired", proposal, "Binding answer arrived after the deadline"
            )
        if actor == "timeout_policy" and self.clock() < self.deadlines[event_id]:
            raise self._error("binding_event_changed", proposal, "Binding deadline has not elapsed")
        try:
            record = self.registry.sessions.get(session_id)
            context = BoundContext.from_record(proposal["target"])
            if (
                not record
                or record.releasing
                or record.revision != proposal["expected_revision"]
                or record.binding_id != proposal["binding_id"]
                or not self.live(context)
            ):
                raise self._error(
                    "binding_event_changed", proposal, "Binding identity or revision changed"
                )
            if actor == "timeout_policy" and (
                not record.auto_bind
                or proposal["timeout_policy"] != "add_and_select"
                or not proposal["created_by_workflow"]
                or self.busy(session_id, automatic=True)
            ):
                raise self._error(
                    "binding_event_expired", proposal, "Automatic binding is not eligible"
                )
            candidate = deepcopy(self.registry)
            if choice != "decline":
                candidate.add(session_id, context)
                if choice == "add_and_select":
                    candidate.select(session_id, context)
                    candidate.sessions[session_id].default_explicit = True
        except CircuitCallError as error:
            error.data.update(
                event_id=event_id,
                session_id=session_id,
                expected_revision=proposal["expected_revision"],
                current_revision=record.revision if record else None,
            )
            self._finish(proposal, None, actor, error=error)
            raise
        return self._finish(proposal, choice, actor, candidate=candidate)

    def tick(self):
        for event_id, deadline in tuple(self.deadlines.items()):
            proposal = self.proposals[event_id]
            record = self.registry.sessions.get(proposal["session_id"])
            if (
                not record
                or record.releasing
                or record.revision != proposal["expected_revision"]
                or record.binding_id != proposal["binding_id"]
                or not self.live(BoundContext.from_record(proposal["target"]))
            ):
                error = self._error(
                    "binding_event_changed", proposal, "Binding identity or revision changed"
                )
                self._finish(proposal, None, "host", error=error)
            elif self.clock() >= deadline:
                try:
                    self.resolve(
                        self.proposals[event_id]["session_id"],
                        event_id,
                        "add_and_select",
                        actor="timeout_policy",
                    )
                except CircuitCallError:
                    pass  # Invalidations are already durable and visible in the snapshot.

    def invalidate_session(self, session_id):
        for proposal in tuple(self.proposals.values()):
            if proposal["session_id"] == session_id and proposal["status"] == "proposed":
                error = self._error("binding_event_changed", proposal, "Binding session closed")
                self._finish(proposal, None, "host", error=error)

    def reserve(self, session_id, context, request_id):
        return self.reserve_many(session_id, [context], request_id)

    def reserve_many(self, session_id, contexts, request_id):
        record = self._session(session_id)
        candidate = deepcopy(self.registry)
        for index, context in enumerate(contexts):
            candidate.reserve(session_id, context, request_id + "_" + str(index))
        self._publish(
            "binding.resource_reserved",
            dict(
                event_id=uuid.uuid4().hex,
                session_id=session_id,
                binding_id=record.binding_id,
                instance_id=record.anchor.instance_id,
                generation=record.anchor.generation,
                targets=[context.record() for context in contexts],
                request_id=request_id,
                actor="host",
                operation_dispatched=False,
            ),
            candidate,
        )

    def invalidate_target(self, context, reason):
        for session_id, record in tuple(self.registry.sessions.items()):
            if (
                record.targets.get(context.target_id) != context
                or context.target_id in record.invalidated
            ):
                continue
            candidate = deepcopy(self.registry)
            updated = candidate.sessions[session_id]
            updated.invalidated[context.target_id] = reason
            updated.revision += 1
            if updated.default_target_id == context.target_id:
                available = [
                    target
                    for target in updated.targets.values()
                    if target.target_id not in updated.invalidated
                ]
                available.sort(key=lambda target: bool(resource_keys(target)))
                if available:
                    updated.default_target_id = available[0].target_id
                    updated.default_explicit = True
            self._publish(
                "binding.target_invalidated",
                dict(
                    event_id=uuid.uuid4().hex,
                    session_id=session_id,
                    binding_id=record.binding_id,
                    instance_id=context.instance_id,
                    generation=context.generation,
                    target_id=context.target_id,
                    reason=reason,
                    actor="host",
                    operation_dispatched=False,
                    binding=updated.snapshot(),
                ),
                candidate,
            )
        self.tick()

    def change(
        self,
        session_id,
        operation,
        *,
        context=None,
        auto_bind=None,
        timeout_seconds=None,
        actor="user",
        reservation_id=None,
    ):
        if actor not in {"user", "task"}:
            raise ValueError("Invalid binding change actor")
        record = self._session(session_id)
        if operation == "select" and actor == "task" and record.default_explicit:
            self.registry.require(session_id, context)
            return record.snapshot()
        candidate = deepcopy(self.registry)
        if operation == "select":
            if not self.live(context):
                raise binding_error(
                    "binding_event_changed", "Binding target is no longer registered", context
                )
            candidate.select(session_id, context)
            if actor == "user":
                candidate.sessions[session_id].default_explicit = True
        elif operation == "release":
            if self.busy(session_id, automatic=False):
                raise binding_error(
                    "binding_busy", "Session has active or unresolved work", context
                )
            candidate.release(session_id, context)
        elif operation == "release_resource":
            row = record.reservations.get(reservation_id)
            if row is None:
                raise ValueError("Resource reservation is not owned by this session")
            context = BoundContext.from_record(row["context"])
            if self.busy(session_id, automatic=False):
                raise binding_error(
                    "binding_busy", "Session has active or unresolved work", context
                )
            candidate.release_resource(session_id, reservation_id)
        elif operation == "policy":
            if (
                type(auto_bind) is not bool
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= 300
            ):
                raise ValueError("Binding timeout must be between 1 and 300 seconds")
            updated = candidate.sessions[session_id]
            updated.auto_bind, updated.timeout_seconds = auto_bind, timeout_seconds
            updated.revision += 1
        else:
            raise ValueError("Invalid binding change")
        result = candidate.sessions[session_id].snapshot()
        payload = dict(
            event_id=uuid.uuid4().hex,
            session_id=session_id,
            actor=actor,
            binding_id=record.binding_id,
            instance_id=record.anchor.instance_id,
            generation=record.anchor.generation,
            source_target_id=record.default_target_id,
            target_id=context.target_id if context else record.default_target_id,
            binding=result,
        )
        self._publish(
            "binding."
            + {
                "select": "selected",
                "release": "released",
                "policy": "policy_changed",
                "release_resource": "resource_released",
            }[operation],
            payload,
            candidate,
        )
        return result

    def snapshot(self, session_id, after=0):
        if type(after) is not int or after < 0:
            raise ValueError("Invalid binding event cursor")
        record = self._session(session_id)
        pending = []
        for proposal in self.proposals.values():
            if proposal["session_id"] == session_id and proposal["status"] == "proposed":
                pending.append(
                    dict(
                        proposal,
                        remaining_seconds=max(
                            0, self.deadlines[proposal["event_id"]] - self.clock()
                        ),
                    )
                )
        return dict(
            record.snapshot(),
            fault=self.fault,
            proposals=json_copy(pending),
            recent=json_copy(
                [
                    proposal
                    for proposal in self.proposals.values()
                    if proposal["session_id"] == session_id and proposal["status"] != "proposed"
                ][-10:]
            ),
            events=json_copy(
                [
                    event
                    for event in self.events[after:]
                    if event["payload"]["session_id"] == session_id
                ]
            ),
            event_cursor=len(self.events),
        )
