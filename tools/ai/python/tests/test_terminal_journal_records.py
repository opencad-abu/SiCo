"""Terminal records use session-owned durable artifacts, never screen history."""

import hashlib
import json
from copy import deepcopy

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_output import MAX_CHUNK_BYTES, OutputChunk
from sico.core.terminal_state import apply_event, event, recovered
from sico.storage.history_reader import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.terminal_records import JOURNAL_KIND, restore, store


def request(session="session"):
    return CommandRequest(CommandRef(session, "terminal", "command"), "request",
                          "/workspace/project", "environment-v1",
                          shell="/bin/sh", text="printf evidence")


def task_state():
    return RunState(BoundContext("instance", "generation", "target", {}))


def append(journal, observation):
    return journal.append(JOURNAL_KIND, store(observation, journal), task_state())


def replay(reader):
    state, output = None, bytearray()
    for row in reader.stream_events():
        if row["kind"] != JOURNAL_KIND:
            continue
        observation = restore(row["payload"], reader)
        state = apply_event(state, observation)
        if observation["kind"] == "output":
            output.extend(OutputChunk.from_record(observation["chunk"]).data)
    return recovered(state), bytes(output)


def test_completed_output_is_readable_after_writer_closes(tmp_path):
    value = request()
    data = "中文".encode() + b"\x00\xff\x1b[31m\n"
    with SessionJournal(tmp_path, value.ref.session_id) as journal:
        append(journal, event("accepted", value.ref, request=value.record()))
        append(journal, event("started", value.ref, execution_id="execution"))
        first = OutputChunk(value.ref, 1, 0, "stdout", data[:1])
        second = OutputChunk(value.ref, 2, 1, "stderr", data[1:])
        for chunk in (first, second):
            row = append(journal, event("output", value.ref, chunk=chunk.record()))
            assert "data" not in row["payload"]["chunk"]
            assert row["payload"]["chunk"]["artifact"]["path"].startswith(
                "attachments/session/",
            )
        append(journal, event("exited", value.ref, exit_code=0, signal=None,
                              output_complete=True, output_sequence=2, output_bytes=len(data)))
        root = journal.root
    restored, output = replay(SessionReader(root, "session"))
    assert restored.succeeded and restored.output_complete
    assert restored.output_bytes == len(data) and output == data
    assert restored.request == value


def test_large_output_has_no_five_thousand_line_persistence_limit(tmp_path):
    value = request()
    data = b"".join(f"line {line:05d} 中文\n".encode() for line in range(50000))
    with SessionJournal(tmp_path, "session") as journal:
        append(journal, event("accepted", value.ref, request=value.record()))
        append(journal, event("started", value.ref, execution_id="execution"))
        sequence = 0
        for offset in range(0, len(data), MAX_CHUNK_BYTES):
            sequence += 1
            chunk = OutputChunk(value.ref, sequence, offset, "stdout",
                                data[offset:offset + MAX_CHUNK_BYTES])
            append(journal, event("output", value.ref, chunk=chunk.record()))
        append(journal, event("exited", value.ref, exit_code=3, signal=None,
                              output_complete=True, output_sequence=sequence,
                              output_bytes=len(data)))
        root = journal.root
    state, output = replay(SessionReader(root, "session"))
    assert output.count(b"\n") == 50000
    assert hashlib.sha256(output).digest() == hashlib.sha256(data).digest()
    assert state.exit_code == 3 and not state.succeeded and state.output_complete


def test_unreferenced_tail_is_not_promoted_to_committed_output(tmp_path):
    value = request()
    with SessionJournal(tmp_path, "session") as journal:
        append(journal, event("accepted", value.ref, request=value.record()))
        append(journal, event("started", value.ref, execution_id="execution"))
        chunk = OutputChunk(value.ref, 1, 0, "stdout", b"committed")
        append(journal, event("output", value.ref, chunk=chunk.record()))
        # Simulate a crash after the artifact commit and before its journal row.
        orphan = store(event("output", value.ref, chunk=OutputChunk(
            value.ref, 2, chunk.end, "stdout", b"unreferenced",
        ).record()), journal)["chunk"]["artifact"]
        root = journal.root
    state, output = replay(SessionReader(root, "session"))
    assert state.phase == "unknown" and not state.output_complete
    assert output == b"committed" and state.output_bytes == 9
    assert (root / orphan["path"]).is_file()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "metadata", "size"])
def test_missing_or_damaged_output_never_looks_complete(tmp_path, damage):
    value = request()
    with SessionJournal(tmp_path, "session") as journal:
        payload = store(event("output", value.ref, chunk=OutputChunk(
            value.ref, 1, 0, "stdout", b"evidence",
        ).record()), journal)
        root = journal.root
    artifact = payload["chunk"]["artifact"]
    path = root / artifact["path"]
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"tampered")
    elif damage == "metadata":
        payload["chunk"]["offset"] = 1
    else:
        artifact["size"] += 1
    with pytest.raises((ValueError, OSError)):
        restore(payload, SessionReader(root, "session"))


def test_cross_session_observations_and_artifacts_are_rejected(tmp_path):
    value = request()
    with SessionJournal(tmp_path, "session") as first, SessionJournal(tmp_path, "other") as other:
        observation = event("output", value.ref, chunk=OutputChunk(
            value.ref, 1, 0, "stdout", b"evidence",
        ).record())
        with pytest.raises(ValueError, match="another session"):
            store(observation, other)
        payload = store(observation, first)
        with pytest.raises(ValueError, match="another session"):
            restore(payload, other)
        foreign = other.artifact(b"{}")
        payload["chunk"]["artifact"] = foreign
        with pytest.raises(ValueError, match="Read only attachments"):
            restore(payload, first)


def test_output_storage_failure_cannot_publish_a_journal_row(tmp_path, monkeypatch):
    value = request()
    with SessionJournal(tmp_path, "session") as journal:
        append(journal, event("accepted", value.ref, request=value.record()))

        def full(_data):
            raise OSError("disk full")

        monkeypatch.setattr(journal, "artifact", full)
        with pytest.raises(OSError, match="disk full"):
            append(journal, event("output", value.ref, chunk=OutputChunk(
                value.ref, 1, 0, "stdout", b"evidence",
            ).record()))
        assert len(journal.records) == 1


def test_codecs_leave_input_records_unchanged(tmp_path):
    value = request()
    observation = event("output", value.ref, chunk=OutputChunk(
        value.ref, 1, 0, "pty", b"evidence",
    ).record())
    before = deepcopy(observation)
    with SessionJournal(tmp_path, "session") as journal:
        payload = store(observation, journal)
        durable = deepcopy(payload)
        assert restore(payload, journal) == observation
        assert payload == durable and observation == before
        # Canonical journal values can be encoded independently of live objects.
        assert json.loads(json.dumps(payload)) == payload
