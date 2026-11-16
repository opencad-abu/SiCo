"""Durable records share strict JSON values while retaining storage error boundaries."""

from copy import deepcopy
import json

import pytest

from cadai.json_values import ProtocolError, strict_json
from sico.core.contracts import BoundContext, RunState
from sico.storage import history, history_reader, index, journal_records
from sico.storage.journal import SessionJournal
from sico.storage.project_files import read_record
from sico.storage.workbench import WorkbenchIndex, content_digest
from sico.transport import framing


def test_shared_json_and_history_exports_keep_exact_identity():
    assert framing.strict_json is strict_json
    assert framing.ProtocolError is ProtocolError
    for name in ("SessionReader", "TailWindow", "owned_directory", "validate_event"):
        assert getattr(history, name) is getattr(history_reader, name)


@pytest.mark.parametrize("raw,message", [
    (b'{"nested":{"x":1,"x":2}}', "Invalid JSON payload"),
    (b'{"value":NaN}', "Invalid JSON payload"),
    (b'{"value":Infinity}', "Invalid JSON payload"),
    (b'{"value":-Infinity}', "Invalid JSON payload"),
    (b'{"value":1e999}', "Invalid JSON payload"),
    (b'{"text":"\xff"}', "Invalid JSON payload"),
    (b'{"value":1} trailing', "Invalid JSON payload"),
    (b'[]', "Message must be an object"),
    (b'null', "Message must be an object"),
    (b'{"nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}', "Invalid JSON payload"),
])
def test_invalid_durable_records_keep_exact_protocol_error_and_bytes(tmp_path, raw, message):
    path = tmp_path / "record.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(ProtocolError, match="^" + message + "$") as caught:
        read_record(path)
    assert type(caught.value) is framing.ProtocolError
    assert path.read_bytes() == raw


def test_unicode_and_finite_values_have_one_decoder(tmp_path):
    raw = '{"text":"中文 µ","nested":{"finite":1.25e2,"items":[true,null]}}'
    expected = {"text": "中文 µ", "nested": {"finite": 125.0, "items": [True, None]}}
    path = tmp_path / "record.json"
    path.write_bytes(raw.encode("utf-8"))
    path.chmod(0o600)
    assert read_record(path) == framing.strict_json(raw) == strict_json(raw.encode()) == expected


@pytest.mark.parametrize("strict", [True, False])
def test_index_and_envelope_read_modes_are_unchanged(tmp_path, strict):
    with SessionJournal(tmp_path, "record") as journal:
        journal.append("session.created", {}, RunState(BoundContext("i", "g", "t", {})))
        row = deepcopy(journal.records[0])
    # Trusted metadata scans deliberately use json.loads; strict recovery and
    # evidence paths reject the same complete duplicate-key record.
    raw = json.dumps(row).encode()[:-1] + b',"payload":{"last":true}}\n'
    path = tmp_path / "selected.jsonl"
    path.write_bytes(raw)
    path.chmod(0o600)
    if strict:
        with pytest.raises(ProtocolError):
            journal_records.decode_record(raw, "record", 1)
        with pytest.raises(ProtocolError):
            index.read_records(path, [(0, len(raw))])
    else:
        assert journal_records.decode_record(raw, "record", 1, validate=False)["payload"] == {"last": True}
        assert index.read_records(path, [(0, len(raw))], strict=False)[0]["payload"] == {"last": True}


def test_complete_bad_record_before_torn_tail_is_never_truncated(tmp_path):
    with SessionJournal(tmp_path, "broken") as journal:
        journal.append("session.created", {}, RunState(BoundContext("i", "g", "t", {})))
        path = journal.directory / "events.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"duplicate":1,"duplicate":2}\n{"unfinished":')
    before = path.read_bytes()
    with pytest.raises(ProtocolError):
        SessionJournal(tmp_path, "broken")
    assert path.read_bytes() == before


def test_archived_workbench_evidence_uses_strict_decoder_without_rewriting(tmp_path):
    with SessionJournal(tmp_path, "evidence") as journal:
        artifact = journal.artifact(b'{"value":1,"value":2}')
        value = {"artifact": artifact, "truncated": True}
        workbench = WorkbenchIndex(journal)
        workbench.data["evidence"] = dict(
            id="evidence", value=value, data_type="archive",
            digest=content_digest({"value": value, "inputs": {}}),
        )
        path = journal.root / artifact["path"]
        before = path.read_bytes()
        with pytest.raises(ProtocolError):
            workbench.evidence_value("evidence")
        assert path.read_bytes() == before
