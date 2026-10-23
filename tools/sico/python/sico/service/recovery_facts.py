"""Read authoritative recovery evidence without repairing or opening a writer.

One full fold is the authoritative read; the fold is cached beside the
journal and reused while the journal fingerprint is unchanged, so a long
journal costs one scan per change instead of one scan per recovery action.
Concurrent readers of the same session share one fold: the startup scan and a
user operation arriving together must not run two CPU-bound folds side by side.
"""

import hashlib
import json
import os
import threading
from dataclasses import dataclass, replace

from ..core.contracts import TERMINAL, BoundContext, RunState, identifier
from ..storage.history import SessionReader, owned_directory
from ..storage.inbox_records import read_input
from ..storage.journal import open_private
from ..storage.record_integrity import MANAGED_EVENTS, require_components
from ..storage.roots import agent_root
from ..storage.session_release import read_release, validate_release
from ..storage.session_snapshot import fingerprint, read
from ..transport.framing import ProtocolError, strict_json
from ..core.session_events import SESSION_CONTRACT, EventCursor
from .service_session_dto import SessionAddress

# v2：缓存记录已覆盖的字节/序列位置与最后一条状态，可在追加日志上只折新增行。
FOLD_CONTRACT = "cadai.journal.fold.v2"
FOLD_NAME = "events.fold.json"
# 折叠缓存只是读路径的复用结果：指纹不符即整篇重折，缓存异常也只回到旧的扫描路径。
FOLD_BYTES = 4 * 1024 * 1024


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


@dataclass(frozen=True)
class JournalFold:
    """Journal-only fold state; cacheable and reusable by migration."""

    digest: str
    context: object
    task: dict
    ends: tuple
    recovered: tuple
    terminal: dict
    task_inputs: dict
    abandoned: dict
    deleted: bool
    bridge_identity: object
    components: dict
    messages: tuple = ()
    covered: int = 0
    covered_sequence: int = 0


def read_facts(project, session_id, operation_id=None, *, end_operation=None):
    try:
        return _read_facts(project, session_id, operation_id, end_operation)
    except ProtocolError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ProtocolError("Invalid durable recovery evidence") from exc


# 同一会话的折叠互斥：等到的读者在锁内复查缓存，先到的折叠已把缓存写回，
# 于是整篇折叠只发生一次（启动扫描与用户操作经常同时到达）。
_FOLD_LOCKS = {}
_FOLD_LOCKS_GUARD = threading.Lock()


def _fold_lock(directory):
    with _FOLD_LOCKS_GUARD:
        return _FOLD_LOCKS.setdefault(directory, threading.Lock())


def _read_facts(project, session_id, operation_id, end_operation):
    root = agent_root(project)
    reader = SessionReader(root, session_id)
    saved = read(reader.directory)
    fd = open_private(reader.directory / "events.jsonl", os.O_RDONLY)
    with os.fdopen(fd, "rb") as stream:
        journal = reader.journal_fingerprint(stream)
        cacheable = end_operation is None and reader.migration is None
        fold, prefix = _load_fold(reader.directory, journal) if cacheable else (None, None)
        if fold is None and cacheable:
            with _fold_lock(reader.directory):
                fold, prefix = _load_fold(reader.directory, journal)
                if fold is None:
                    fold = _fold_cached(session_id, reader, stream, saved, journal, prefix, project)
        elif fold is None:
            fold = fold_journal(session_id, reader.iter_raw(stream), saved,
                                migration=reader.migration is not None,
                                end_operation=end_operation, project=project)
    if not fold.deleted:
        require_components(root, session_id, **fold.components)
    facts = facts_from_fold(fold, session_id, saved,
                            input_records(reader.directory / "inbox"),
                            lambda event: read_release(reader.directory, event), operation_id)
    if reader.migration is not None:
        if (saved is not None and saved["project_id"] != reader.migration["source_project_id"]
                or facts.unresolved):
            raise ProtocolError("Migrated session provenance is invalid or unresolved")
        facts = replace(facts, archived=True)
    return facts


def _fold_cached(session_id, reader, stream, saved, journal, prefix, project):
    """Fold a cache miss, extending the cached prefix when the journal only grew.

    Fallback is always the full fold: a prefix that no longer lines up, or any
    error while extending, costs one scan and never a wrong answer.
    """

    if prefix is not None:
        previous, covered, sequence = prefix
        try:
            stream.seek(covered)
            fold = fold_journal(
                session_id, reader.iter_raw_records(stream, sequence=sequence), saved,
                resume=previous, origins=reader.origins_before(covered),
                migration=False, project=project)
        except (ValueError, KeyError, TypeError, ProtocolError):
            stream.seek(0)
            fold = fold_journal(session_id, reader.iter_raw_records(stream), saved,
                                migration=False, project=project)
    else:
        stream.seek(0)
        fold = fold_journal(session_id, reader.iter_raw_records(stream), saved,
                            migration=False, project=project)
    if reader.journal_fingerprint(stream) == journal:
        _save_fold(reader.directory, journal, fold)
    return fold


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
    live recovery supplies records from the owning session reader, where the
    same fold is reused from the validated cache. The receipt reader is called
    only when an observed end requires release evidence.
    """
    fold = fold_journal(session_id, events, saved, migration=True,
                        end_operation=end_operation, project=project)
    return facts_from_fold(fold, session_id, saved, input_rows, release_record, operation_id)


def fold_journal(session_id, events, saved, *, migration=False, end_operation=None,
                 project=None, resume=None, origins=None):
    """Fold one journal into cacheable recovery state, validating identity and order.

    ``resume`` continues from a cached fold: only the appended rows are folded,
    the running digest chains onto the cached one, and the validator is seated
    from the cached state plus the prefix origin index.
    """
    context = BoundContext.from_record(saved["context"]) if saved else None
    state = None
    task_inputs, terminal, ends, abandoned, recovered = {}, {}, [], {}, []
    deleted = False
    covered, covered_sequence = 0, 0
    digest = hashlib.sha256().hexdigest()
    bridge_identity = (saved or {}).get("bridge_identity")
    components = dict(managed=False, empty=True, thread=None)
    validator = None
    if resume is not None:
        state = RunState(resume.context, list(resume.messages), dict(resume.task))
        context = resume.context
        task_inputs, terminal = dict(resume.task_inputs), dict(resume.terminal)
        ends, abandoned = list(resume.ends), dict(resume.abandoned)
        recovered, deleted = list(resume.recovered), resume.deleted
        digest, bridge_identity = resume.digest, resume.bridge_identity
        components = dict(resume.components)
        covered, covered_sequence = resume.covered, resume.covered_sequence
        validator = EventCursor(session_id, None, resume.context, history_sequence=2**63-1)
        validator.sequence = resume.covered_sequence
        if origins is not None:
            validator.native_origins = origins
        validator.seed(dict(resume.task))
    for item in events:
        row, offset, length = item if isinstance(item, tuple) else (item, None, None)
        if offset is not None:
            covered, covered_sequence = offset + length, row["sequence"]
        components["empty"] = False
        components["managed"] |= row["kind"] in MANAGED_EVENTS
        if row["kind"] == "codex.thread" and not migration:
            components["thread"] = row["payload"]
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
        digest = hashlib.sha256((digest + fingerprint(row)).encode("ascii")).hexdigest()
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
            recovered.append(row["payload"])
        deleted |= row["kind"] == "codex.thread.deleted"
    if state is not None:
        context = state.context
    return JournalFold(digest, context, state.task if state else {},
                       tuple(ends), tuple(recovered), terminal, task_inputs, abandoned,
                       deleted, bridge_identity, components,
                       messages=tuple(state.messages) if state else (),
                       covered=covered, covered_sequence=covered_sequence)


def facts_from_fold(fold, session_id, saved, input_rows, release_record, operation_id):
    """Assemble public recovery facts from one fold plus the fresh side records."""
    if fold.context is None:
        raise ValueError("历史缺少捕获来源，不能恢复执行")
    inputs = []
    input_digest = hashlib.sha256()
    for record in input_rows:
        input_digest.update(fingerprint(record).encode("ascii"))
        address = record.get("service_address")
        if address is not None and (address["session_id"] != session_id or
                saved and address["project_id"] != saved["project_id"]):
            raise ProtocolError("Recovery input belongs to another session")
        if record["id"] in fold.abandoned and fold.abandoned[record["id"]] != address:
            raise ProtocolError("Abandonment belongs to another input runtime")
        status, task_id = fold.terminal.get(record["id"],
                                            (record["status"], record.get("task_id")))
        if (record["id"] not in fold.abandoned
                and status in {"queued", "executing", "waiting_user", "needs_reconcile"}):
            inputs.append(dict(input_id=record["id"], status=status, task_id=task_id,
                               address=address))
            if len(inputs) > 200:
                raise ValueError("未决输入超过恢复窗口，请先逐项核对原记录")
    resources_released = bool(fold.ends and fold.ends[-1]["kind"] == "session.end_observed"
                              and validate_release(release_record(fold.ends[-1]), fold.ends[-1]))
    operations = () if operation_id is None else tuple(
        payload for payload in fold.recovered if payload["operation_id"] == operation_id)[-1:]
    version = fingerprint({"journal": fold.digest, "inputs": input_digest.hexdigest(),
                           "snapshot": saved, "released": resources_released})
    return RecoveryFacts(saved, fold.context, fold.task, version, tuple(inputs),
                         tuple(fold.ends), fold.deleted, operations,
                         resources_released, bridge_identity=fold.bridge_identity)


def reader_public(row):
    from ..storage.journal import SessionJournal

    return SessionJournal.public(row)


def _fold_path(directory):
    return directory / FOLD_NAME


def _load_fold(directory, journal):
    """Return ``(fold, prefix)`` for the cached fold of this journal.

    An exact fingerprint match returns ``(fold, None)`` and needs no folding.
    When the journal only grew, the cached fold comes back as a prefix
    ``(fold, covered, covered_sequence)`` so the next fold only appends.
    """
    try:
        fd = open_private(_fold_path(directory), os.O_RDONLY)
    except (OSError, ValueError):
        return None, None
    try:
        with os.fdopen(fd, "rb") as stream:
            row = strict_json(stream.read())
    except (OSError, ValueError):
        return None, None
    if row.get("contract") != FOLD_CONTRACT:
        return None, None
    try:
        context = row["context"]
        components = row["components"]
        if (set(components) != {"managed", "empty", "thread"}
                or not isinstance(components["managed"], bool)
                or not isinstance(components["empty"], bool)
                or (components["thread"] is not None
                    and not isinstance(components["thread"], dict))):
            return None, None
        terminal = {}
        for key, value in row["terminal"].items():
            if (not isinstance(key, str) or not isinstance(value, (list, tuple))
                    or len(value) != 2):
                return None, None
            terminal[key] = (str(value[0]), str(value[1]))
        if (not isinstance(row["digest"], str) or not isinstance(row["task"], dict)
                or not isinstance(row["ends"], list) or not isinstance(row["recovered"], list)
                or not isinstance(row["task_inputs"], dict)
                or not isinstance(row["abandoned"], dict)):
            return None, None
        messages = row["messages"]
        covered, covered_sequence = row["covered"], row["covered_sequence"]
        fingerprint_row = row.get("fingerprint")
        if (not isinstance(messages, (list, tuple))
                or any(not isinstance(message, dict) for message in messages)
                or type(covered) is not int or type(covered_sequence) is not int
                or min(covered, covered_sequence) < 0
                or not isinstance(fingerprint_row, list) or len(fingerprint_row) != 5):
            return None, None
        fold = JournalFold(
            digest=row["digest"],
            context=None if context is None else BoundContext.from_record(context),
            task=dict(row["task"]),
            ends=tuple(row["ends"]),
            recovered=tuple(row["recovered"]),
            terminal=terminal,
            task_inputs=dict(row["task_inputs"]),
            abandoned=dict(row["abandoned"]),
            deleted=bool(row["deleted"]),
            bridge_identity=row["bridge_identity"],
            components=dict(components),
            messages=tuple(dict(message) for message in messages),
            covered=covered,
            covered_sequence=covered_sequence,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, None
    if fingerprint_row == list(journal):
        return fold, None
    if (fingerprint_row[0] == journal[0] and fingerprint_row[1] == journal[1]
            and 0 < covered <= journal[2] and covered_sequence > 0):
        return None, (fold, covered, covered_sequence)
    return None, None


def _save_fold(directory, journal, fold):
    """Refresh the cache atomically; a failure only costs the next full fold."""
    row = {
        "contract": FOLD_CONTRACT,
        "fingerprint": list(journal),
        "digest": fold.digest,
        "context": None if fold.context is None else fold.context.record(),
        "task": fold.task,
        "ends": list(fold.ends),
        "recovered": list(fold.recovered),
        "terminal": {key: list(value) for key, value in fold.terminal.items()},
        "task_inputs": dict(fold.task_inputs),
        "abandoned": dict(fold.abandoned),
        "deleted": fold.deleted,
        "bridge_identity": fold.bridge_identity,
        "components": dict(fold.components),
        "messages": [dict(message) for message in fold.messages],
        "covered": int(fold.covered),
        "covered_sequence": int(fold.covered_sequence),
    }
    try:
        payload = json.dumps(row, ensure_ascii=True, allow_nan=False, sort_keys=True)
        if len(payload) > FOLD_BYTES:
            return False
        owned_directory(directory)
        temporary = directory / (FOLD_NAME + ".tmp")
        fd = open_private(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
        os.replace(temporary, _fold_path(directory))
    except (OSError, ValueError, TypeError):
        return False
    return True
