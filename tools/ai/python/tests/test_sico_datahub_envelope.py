"""DataHub envelopes enforce one bounded UTF-8 JSON representation."""

import json

import pytest
import sico_datahub_wire as wire
from sico_datahub.codec import (
    ArtifactRef,
    artifact_ref,
    decode,
    decode_bytes,
    encode,
    encode_bytes,
    encode_line,
)
from sico_datahub.envelope import Envelope, Error, Meta, Page
from sico_datahub.errors import HubError, may_retry


def _page():
    return Page(offset=0, limit=50, total=120, next=50, truncated=True)


def test_roundtrip_preserves_explicit_empty_fields_and_pagination():
    envelope = Envelope(
        contract="sico.cad.objects.v1",
        action="list",
        id="o1",
        payload={
            "session_ref": "fixture",
            "window_ref": "wctx_fixture_1",
            "target": {
                "library": "worklib",
                "cell": "OTA",
                "view_name": "schematic",
                "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
            },
            "items": [],
            "selection_count": 0,
            "query": "",
        },
        page=_page(),
        meta=Meta(trace_id="", source="skill", target="python"),
    )
    restored = decode(encode(envelope))
    assert restored == envelope
    row = json.loads(encode(envelope))
    assert row["meta"]["trace_id"] == ""
    assert row["payload"]["query"] == ""
    assert row["page"] == {
        "offset": 0,
        "limit": 50,
        "total": 120,
        "next": 50,
        "truncated": True,
    }
    assert row["error"] is None
    assert encode_line(envelope).endswith(b"\n")


def test_error_and_unknown_have_distinct_replay_semantics():
    failed = Envelope(
        contract="sico.cad.objects.v1",
        action="list",
        id="o2",
        status="error",
        error=Error("read_failed", "read failed", {"window_ref": "wctx_fixture_1"}),
    )
    assert decode(encode(failed)) == failed
    assert failed.may_retry()
    assert not failed.may_retry(write=True)
    unknown = Envelope(
        contract="sico.cad.objects.v1",
        action="list",
        id="o3",
        status="unknown",
    )
    assert decode(encode(unknown)) == unknown
    assert not unknown.may_retry()
    assert not may_retry("unknown", "timeout", write=False)
    assert may_retry("error", "cancelled", write=True)
    with pytest.raises(HubError, match="require an error"):
        Envelope(
            contract="sico.cad.objects.v1",
            action="list",
            id="o4",
            status="error",
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda row: row.update(status="pending"),
        lambda row: row.update(hub="other"),
        lambda row: row.update(contract="bad"),
        lambda row: row.update(version=True),
        lambda row: row.update(id=""),
        lambda row: row.update(payload=[]),
        lambda row: row["meta"].update(trace_id="bad\nvalue"),
        lambda row: row["meta"].update(deadline_ms=0),
        lambda row: row.update(error={"code": "internal", "message": "x", "details": {}}),
        lambda row: row.update(extra=True),
    ],
)
def test_envelope_fields_are_strict(change):
    row = wire.make_envelope(
        "sico.cad.objects.v1",
        "list",
        "o1",
        {"window_ref": "wctx_fixture_1"},
    )
    change(row)
    with pytest.raises(HubError):
        wire.validate_envelope(row)


@pytest.mark.parametrize(
    "page",
    [
        {"offset": 0, "limit": 50, "total": 10, "next": 50, "truncated": True},
        {"offset": 0, "limit": 50, "total": 120, "next": None, "truncated": True},
        {"offset": 0, "limit": 50, "total": 120, "next": 50, "truncated": False},
        {"offset": -1, "limit": 50, "total": 120, "next": 50, "truncated": True},
        {"offset": 0, "limit": 0, "total": 120, "next": 1, "truncated": True},
        {"offset": 0, "limit": 50, "total": 120, "next": "50", "truncated": True},
    ],
)
def test_page_must_be_consistent(page):
    with pytest.raises(HubError):
        Envelope(
            contract="sico.cad.objects.v1",
            action="list",
            id="o1",
            page=Page.from_dict(page),
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"hub":"a","hub":"b"}',
        b'{"value":NaN}',
        b'{"value":"\\ud800"}',
        b'{"value":"\xff"}',
        b"[]",
        b"{" + b'"a":' + b"[" * 40 + b"]" * 40 + b"}",
    ],
)
def test_invalid_json_is_rejected(raw):
    with pytest.raises(HubError):
        wire.loads(raw)


def test_text_sequence_depth_and_byte_limits_are_enforced():
    base = {
        "hub": wire.HUB,
        "contract": "sico.cad.objects.v1",
        "version": 1,
        "id": "o1",
        "action": "list",
        "status": "ok",
        "payload": {},
        "meta": {
            "trace_id": "",
            "source": "python",
            "target": "python",
            "deadline_ms": 5000,
        },
        "error": None,
    }
    with pytest.raises(HubError, match="text limit"):
        wire.validate_envelope({**base, "payload": {"value": "x" * 4097}})
    with pytest.raises(HubError, match="item limit"):
        wire.validate_envelope({**base, "payload": {"value": [0] * 201}})
    nested = "leaf"
    for _ in range(wire.MAX_DEPTH + 1):
        nested = [nested]
    with pytest.raises(HubError, match="nesting"):
        wire.validate_envelope({**base, "payload": {"value": nested}})
    with pytest.raises(HubError, match="byte limit"):
        wire.validate_envelope({
            **base,
            "payload": {"value": ["x" * 4000 for _ in range(14)]},
        })
    with pytest.raises(HubError, match="byte limit"):
        wire.validate_envelope({
            **base,
            "status": "error",
            "error": {
                "code": "internal",
                "message": "failed",
                "details": {"value": ["x" * 4000 for _ in range(200)]},
            },
        })


def test_invalid_utf8_and_control_characters_are_stable_errors():
    with pytest.raises(HubError) as failure:
        wire.decode_envelope(b'{"hub":"\xff"}')
    assert failure.value.code == "invalid_arguments"
    with pytest.raises(HubError, match="control"):
        wire.make_envelope(
            "sico.cad.objects.v1",
            "list",
            "o1",
            {"query": "line\nbreak"},
        )


def test_page_value_requires_all_fields_even_when_next_is_null():
    page = Page(offset=0, limit=50, total=0, next=None, truncated=False)
    assert page.to_dict()["next"] is None
    envelope = Envelope(
        contract="sico.cad.objects.v1",
        action="list",
        id="o1",
        page=page,
    )
    assert decode(encode(envelope)) == envelope


def test_artifacts_and_base64_are_digest_bound_and_bounded(tmp_path):
    path = tmp_path / "waveform.dat"
    data = b"waveform\x00payload"
    path.write_bytes(data)
    ref = artifact_ref(path)
    assert ref.path == str(path) and ref.size == len(data)
    assert len(ref.sha256) == 64
    assert ArtifactRef.from_dict(ref.to_dict()) == ref
    assert decode_bytes(encode_bytes(data), max_bytes=len(data)) == data
    with pytest.raises(HubError, match="absolute"):
        ArtifactRef.from_dict({**ref.to_dict(), "path": "relative.dat"})
    with pytest.raises(HubError, match="digest"):
        ArtifactRef.from_dict({**ref.to_dict(), "sha256": "x" * 64})
    with pytest.raises(HubError, match="byte limit"):
        decode_bytes(encode_bytes(data), max_bytes=len(data) - 1)
    with pytest.raises(HubError, match="base64"):
        decode_bytes("%%%")
