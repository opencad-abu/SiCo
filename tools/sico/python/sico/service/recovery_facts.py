"""Read authoritative recovery evidence without repairing or opening a writer."""

import hashlib
import os
from dataclasses import dataclass

from ..core.contracts import TERMINAL, BoundContext, RunState, identifier
from ..storage.history import SessionReader, owned_directory
from ..storage.inbox_records import read_input
from ..storage.journal import open_private
from ..storage.session_release import read_release, validate_release
from ..storage.session_snapshot import fingerprint, read
from ..storage.roots import agent_root
from ..storage.record_integrity import MANAGED_EVENTS, require_components
from ..transport.framing import ProtocolError
from .events import SESSION_CONTRACT, EventCursor
from .service_session_dto import SessionAddress


@dataclass(frozen=True)
class RecoveryFacts:
    snapshot: object
    context: BoundContext
    task: dict
    version: str
    inputs: tuple
    ends: tuple
    deleted: bool
    operations: tuple
    resources_released: bool = False
    archived: bool = False
    bridge_identity: object = None

    @property
    def unresolved(self):
        if self.deleted:
            return False
        if self.ends:
            observed = self.ends[-1]
            if observed["kind"] == "session.end_observed":
                return not self.resources_released
            return True
        if self.inputs:
            return True
        return self.task.get("status") in {"executing", "needs_reconcile"}


def read_facts(project, session_id, operation_id=None, *, end_operation=None):
    try:
        return _read_facts(project, session_id, operation_id, end_operation)
    except ProtocolError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ProtocolError("Invalid durable recovery evidence") from exc


def _read_facts(project, session_id, operation_id, end_operation):
    root = agent_root(project)
    reader = SessionReader(root, session_id)
    saved = read(reader.directory)
    components = dict(managed=False, empty=True, thread=None)
    def events(stream):
        for row in reader.iter_raw(stream):
            components["empty"] = False
            components["managed"] |= row["kind"] in MANAGED_EVENTS
            if row["kind"] == "codex.thread" and reader.migration is None:
                components["thread"] = row["payload"]
            yield row
    fd = open_private(reader.directory / "events.jsonl", os.O_RDONLY)
    with os.fdopen(fd, "rb") as stream:
        facts = collect_facts(session_id, events(stream), saved,
                              input_records(reader.directory / "inbox"),
                              lambda event: read_release(reader.directory, event), operation_id,
                              end_operation=end_operation, project=project)
    if not facts.deleted:
        require_components(root, session_id, **components)
    if reader.migration is not None:
        from dataclasses import replace

        if (saved is not None and saved["project_id"] != reader.migration["source_project_id"]
                or facts.unresolved):
            raise ProtocolError("Migrated session provenance is invalid or unresolved")
        facts = replace(facts, archived=True)
    return facts


def input_records(inbox):
    if inbox.exists():
        owned_directory(inbox)
        for path in sorted(inbox.glob("*.json")):
            record = read_input(inbox, path.stem)
            if record is None:
                raise ProtocolError("Recovery input changed while reading")
            yield record


def collect_facts(session_id, events, saved, input_rows, release_record, operation_id=None,
                  *, end_operation=None, project=None):
    """Derive recovery evidence from validated records, without a storage pathname.

    Migration supplies descriptor-relative records from its locked inventory;
    live recovery supplies records from the owning session reader. The receipt
    reader is called only when an observed end requires release evidence.
    """
    context = BoundContext.from_record(saved["context"]) if saved else None
    state = None
    task_inputs, terminal, ends, abandoned, operations = {}, {}, [], {}, []
    deleted = False
    digest = hashlib.sha256()
    validator = None
    bridge_identity = (saved or {}).get("bridge_identity")
    for row in events:
        previous = state
        state = RunState.from_record(row["state"])
        if row["kind"] == "session.host_changed":
            from .session_host_transition import validate_transition

            if previous is None and context is not None:
                previous = RunState(context)
            if previous is None:
                raise ProtocolError("Host transition lacks prior conversation state")
            bridge_identity = validate_transition(row["payload"], previous, state, project)
        else:
            expected = previous.context if previous is not None else context
            if expected and (expected.instance_id, expected.generation) != (
                    state.context.instance_id, state.context.generation):
                raise ProtocolError("Recovered context generation changed without transition")
        if validator is None:
            bound = context or state.context
            validator = EventCursor(session_id, None, bound, history_sequence=2**63-1)
        validator.consume(dict(contract=SESSION_CONTRACT, session_id=session_id,
            runtime_id=None, instance_id=validator.identity[2],
            generation=validator.identity[3],
            events=[reader_public(row)]))
        digest.update(fingerprint(row).encode("ascii"))
        if row["kind"] == "task.started":
            key = row["payload"].get("input_id")
            task_inputs[row["task_id"]] = key
            if key:
                terminal[key] = "executing", row["task_id"]
        if row["kind"] in {"task." + value for value in TERMINAL | {"needs_reconcile"}}:
            key = task_inputs.get(row["task_id"])
            if key:
                terminal[key] = row["kind"][5:], row["task_id"]
        if row["kind"] in {"session.end_requested", "session.end_observed"}:
            payload = row["payload"]
            address = SessionAddress.from_record(payload["address"])
            identifier(payload["operation_id"])
            if (address.session.session_id != session_id or
                    saved and address.project_id != saved["project_id"]):
                raise ProtocolError("End evidence belongs to another session")
            if row["kind"] == "session.end_observed" and payload.get("state") not in {
                    "drained", "needs_reconcile"}:
                raise ProtocolError("Invalid durable end state")
            if end_operation is None or (
                    address.session.runtime_id, payload["operation_id"]) == end_operation:
                ends[:] = [reader_public(row)]
        if row["kind"] == "session.input_abandoned":
            abandoned[row["payload"]["input_id"]] = row["payload"].get("address")
        if row["kind"] == "session.recovered":
            from .recovery_contract import recovery_operation

            recovery_operation(row["payload"])
            # A committed recovery opens a new runtime; the prior end remains
            # in history but cannot hide this runtime's interrupted work.
            if end_operation is None:
                ends.clear()
            if row["payload"]["operation_id"] == operation_id:
                operations[:] = [row["payload"]]
        deleted |= row["kind"] == "codex.thread.deleted"
    if state is not None:
        context = state.context
    if context is None:
        raise ValueError("历史缺少捕获来源，不能恢复执行")
    inputs = []
    for record in input_rows:
        digest.update(fingerprint(record).encode("ascii"))
        address = record.get("service_address")
        if address is not None and (address["session_id"] != session_id or
                saved and address["project_id"] != saved["project_id"]):
            raise ProtocolError("Recovery input belongs to another session")
        if record["id"] in abandoned and abandoned[record["id"]] != address:
            raise ProtocolError("Abandonment belongs to another input runtime")
        status, task_id = terminal.get(record["id"], (record["status"], record.get("task_id")))
        if (record["id"] not in abandoned
                and status in {"queued", "executing", "waiting_user", "needs_reconcile"}):
            inputs.append(dict(input_id=record["id"], status=status, task_id=task_id,
                               address=address))
            if len(inputs) > 200:
                raise ValueError("未决输入超过恢复窗口，请先逐项核对原记录")
    resources_released = bool(ends and ends[-1]["kind"] == "session.end_observed"
                              and validate_release(release_record(ends[-1]), ends[-1]))
    digest.update(fingerprint(dict(snapshot=saved, released=resources_released)).encode("ascii"))
    return RecoveryFacts(saved, context, state.task if state else {}, digest.hexdigest(),
                         tuple(inputs), tuple(ends), deleted, tuple(operations),
                         resources_released, bridge_identity=bridge_identity)


def reader_public(row):
    from ..storage.journal import SessionJournal

    return SessionJournal.public(row)
