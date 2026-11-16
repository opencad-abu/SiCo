# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent transport contract wire contracts."""

from __future__ import annotations

import json
import os
import struct
import threading

import pytest

from sico.transport.framing import (
    MAX_FRAME,
    FrameDecoder,
    ProtocolError,
    encode,
    strict_json,
)
from sico.transport.relay import (
    SkillResponseTimeout,
    StdioLines,
    assistant_wire_arguments,
    diagnostic_sink_from_environment,
    emit_diagnostic,
    skill_response_timeout,
)


def test_host_question_identity_ignores_recommendation_description():
    from sico.service.audit import same_host_questions

    expected = [{"id": "pdk_library", "question": "选择 PDK", "options": [
        {"label": "smic28hkmg", "description": "/old/path"},
        {"label": "other", "description": "/other"},
    ]}]
    incoming = [{"id": "pdk_library", "question": "选择 PDK", "options": [
        {"label": "smic28hkmg (Recommended)", "description": "/new/path"},
        {"label": "other", "description": "changed"},
    ]}]
    assert same_host_questions(expected, incoming)


def test_hierarchical_sheet_cell_is_valid_inspect_target():
    from cadai.cdf_tools import build_call

    code = build_call("inspect_cdf", {
        "target": {"library": "sxTest", "cell": "inv@sheet001", "view": "schematic"},
        "instance": "PM0",
    })
    assert '"inv@sheet001"' in code


def test_skill_response_timeout_is_configurable_and_bounded():
    assert skill_response_timeout({"SICO_SKILL_TIMEOUT": "91.2"}) == pytest.approx(91.2)
    assert skill_response_timeout({"SICO_MCP_TIMEOUT": "120"}) == pytest.approx(120)
    with pytest.raises(ValueError, match="set SICO_MCP_TIMEOUT"):
        skill_response_timeout({"CAD_STUDIO_MCP_TIMEOUT": "120"})
    with pytest.raises(ValueError, match="SKILL response timeout"):
        skill_response_timeout({"SICO_SKILL_TIMEOUT": "0"})
    with pytest.raises(ValueError, match="SKILL response timeout"):
        skill_response_timeout({"SICO_SKILL_TIMEOUT": "not-a-number"})


def test_diagnostic_sink_is_private_bounded_jsonl(tmp_path):
    path = tmp_path / "diagnostic.jsonl"
    with pytest.raises(ValueError, match="set SICO_AI_DIAGNOSTIC_LOG"):
        diagnostic_sink_from_environment({"CAD_AI_DIAGNOSTIC_LOG": str(path)})
    sink = diagnostic_sink_from_environment({"SICO_AI_DIAGNOSTIC_LOG": str(path)})
    assert sink is not None
    emit_diagnostic(sink, "skill.dispatch", request_id="a" * 32, method="get_context")
    emit_diagnostic(sink, "skill.reply", request_id="b" * 32, detail="x" * 10000)
    sink.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0] == {
        "event": "skill.dispatch",
        "request_id": "a" * 32,
        "method": "get_context",
    }
    assert rows[1] == {"event": "skill.reply", "request_id": "b" * 32,
                       "diagnostic_truncated": True}
    assert diagnostic_sink_from_environment({"SICO_AI_DIAGNOSTIC_LOG": "relative.jsonl"}) is None


def test_stdio_timeout_uses_dedicated_exception(tmp_path):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb")
    reader = StdioLines(source)
    try:
        with pytest.raises(SkillResponseTimeout):
            reader.next(timeout=0.01)
    finally:
        reader.close()
        source.close()
        os.close(write_fd)


def test_stdio_decodes_control_messages_on_its_reader_thread():
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb")
    decoded_on = []

    def decode(line):
        decoded_on.append(threading.get_ident())
        return strict_json(line)

    reader = StdioLines(source, decode=decode)
    try:
        os.write(write_fd, b'{"kind":"show"}\n{"kind":"shutdown"}\n')
        assert reader.next(2) == {"kind": "show"}
        assert reader.next(2) == {"kind": "shutdown"}
        assert decoded_on == [reader.thread.ident, reader.thread.ident]
        assert reader.thread.ident != threading.get_ident()
    finally:
        reader.close()
        source.close()
        os.close(write_fd)


@pytest.mark.parametrize("invalid", [
    b'{"kind":"show","kind":"shutdown"}\n', b'[]\n', b'{"kind":"show"}',
    b'{"kind":"' + b'x' * 262144 + b'"}\n',
])
def test_stdio_rejects_invalid_control_messages_before_the_ui_queue(invalid):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb")
    reader = StdioLines(source, decode=strict_json)
    try:
        os.write(write_fd, invalid)
        os.close(write_fd)
        write_fd = None
        assert reader.closed.wait(2)
        assert reader.lines.empty()
        with pytest.raises(EOFError):
            reader.next(2)
    finally:
        reader.close()
        source.close()
        if write_fd is not None:
            os.close(write_fd)


def test_split_coalesced_unicode_frames():
    messages = [{"text": "中文 µ"}, {"ok": True}]
    data = b"".join(encode(m) for m in messages)
    decoder = FrameDecoder()
    result = []
    for byte in data:
        result.extend(decoder.feed(bytes([byte])))
    decoder.eof()
    assert result == messages
    assert FrameDecoder().feed(data) == messages


def test_assistant_wire_is_bounded_and_hex_encoded():
    assert assistant_wire_arguments({"method": "eval_skill", "code": "list(1 2)"}) == [
        "eval_skill", "6c6973742831203229"
    ]
    with pytest.raises(ProtocolError):
        assistant_wire_arguments({"method": "eval_skill", "code": ""})
    with pytest.raises(ProtocolError):
        assistant_wire_arguments({"method": "run_shell", "code": "x"})
    with pytest.raises(ProtocolError):
        assistant_wire_arguments({"method": "eval_skill", "code": "x" * 65537})


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b'{"s":"\xff"}'])
def test_strict_json(raw):
    with pytest.raises(ProtocolError):
        strict_json(raw)


def test_bad_size_and_truncated_frame():
    decoder = FrameDecoder()
    with pytest.raises(ProtocolError):
        decoder.feed(struct.pack("!I", MAX_FRAME + 1))
    with pytest.raises(ProtocolError):
        decoder.feed(encode({"ok": 1}))
    decoder = FrameDecoder()
    decoder.feed(encode({"ok": True})[:-1])
    with pytest.raises(ProtocolError):
        decoder.eof()
