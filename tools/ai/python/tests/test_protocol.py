from __future__ import annotations

import json

import pytest

from cadai.protocol import JsonLineDecoder, ProtocolError, decode_line, encode_line, require_request


def test_fragmented_utf8_is_decoded_only_after_newline() -> None:
    decoder = JsonLineDecoder()
    raw = encode_line({"message": "电源网络"})
    split = raw.index("电".encode()) + 1
    assert decoder.feed(raw[:split]) == []
    assert decoder.feed(raw[split:-1]) == []
    assert decoder.feed(raw[-1:]) == [{"message": "电源网络"}]
    decoder.finish()


def test_multiple_messages_and_empty_lines() -> None:
    decoder = JsonLineDecoder()
    payload = b"\n" + encode_line({"id": 1}) + encode_line({"id": 2})
    assert decoder.feed(payload) == [{"id": 1}, {"id": 2}]


def test_invalid_utf8_and_truncated_stream_are_rejected() -> None:
    with pytest.raises(ProtocolError, match="UTF-8"):
        decode_line(b'{"x":"\xff"}')
    decoder = JsonLineDecoder()
    decoder.feed(b'{"id":1}')
    with pytest.raises(ProtocolError, match="mid-message"):
        decoder.finish()


def test_bounded_serialization_does_not_escape_unicode() -> None:
    encoded = encode_line({"value": "中文"})
    assert "中文".encode() in encoded
    assert json.loads(encoded) == {"value": "中文"}
    with pytest.raises(ProtocolError, match="exceeds"):
        encode_line({"value": "12345"}, max_line_bytes=5)


def test_only_json_objects_are_messages() -> None:
    with pytest.raises(ProtocolError, match="object"):
        decode_line("[1,2,3]")


@pytest.mark.parametrize(
    "request_id",
    (
        "a" * 15,
        "a" * 65,
        "-" + "a" * 15,
        "A" * 16,
        "g" * 16,
        "a" * 16 + "/../../../outside",
        "a" * 16 + "_suffix",
    ),
)
def test_request_id_rejects_values_unsafe_for_spool_paths(request_id: str) -> None:
    with pytest.raises(ProtocolError) as caught:
        require_request({"id": request_id, "method": "get_context", "params": {}})
    assert caught.value.code == "invalid_id"


@pytest.mark.parametrize(
    "request_id",
    ("a" * 16, "01234567-89ab-cdef-0123-456789abcdef", "f" * 64),
)
def test_request_id_accepts_skill_compatible_values(request_id: str) -> None:
    assert require_request(
        {"id": request_id, "method": "get_context", "params": {}}
    ) == (request_id, "get_context", {})
