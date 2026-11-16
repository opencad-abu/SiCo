"""Contract tests for MCP envelope and bounded stdio dispatch."""

import io

import pytest

from cadai.mcp import McpServer
from cadai.mcp_protocol import error, handle_message, result, serve, tool_result
from cadai.protocol import decode_line


def test_handle_message_preserves_initialize_list_and_notification_contract():
    instructions = lambda: "instructions"
    tools = lambda: [{"name": "ping"}]
    calls = []

    def call_tool(request_id, params):
        calls.append((request_id, params))
        return result(request_id, {"called": True})

    initialized = handle_message(
        {"id": 1, "method": "initialize", "params": {"protocolVersion": "x"}},
        instructions=instructions, list_tools=tools, call_tool=call_tool,
    )
    assert initialized["result"]["protocolVersion"] == "x"
    assert initialized["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert handle_message(
        {"method": "notifications/initialized"},
        instructions=instructions, list_tools=tools, call_tool=call_tool,
    ) is None
    listed = handle_message(
        {"id": 2, "method": "tools/list"},
        instructions=instructions, list_tools=tools, call_tool=call_tool,
    )
    assert listed["result"]["tools"] == tools()
    called = handle_message(
        {"id": 3, "method": "tools/call", "params": {"name": "ping"}},
        instructions=instructions, list_tools=tools, call_tool=call_tool,
    )
    assert called == result(3, {"called": True})
    assert calls == [(3, {"name": "ping"})]


def test_serve_returns_bounded_protocol_error_and_continues():
    reader = io.BytesIO(b"x" * 40 + b"\n" + b'{"id":1,"method":"ping"}\n')
    writer = io.BytesIO()
    serve(reader, writer, lambda message: result(message.get("id"), {}), max_line_bytes=32)
    responses = [decode_line(line) for line in writer.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_tool_result_maps_oversized_payload_to_stable_error():
    response = tool_result(4, True, {"payload": "x" * 2_000_000})
    assert response["result"]["isError"] is True
    assert '"code": "response_too_large"' in response["result"]["content"][0]["text"]


def test_response_builders_keep_jsonrpc_envelope():
    assert result("one", {"ok": True}) == {"jsonrpc": "2.0", "id": "one", "result": {"ok": True}}
    assert error("one", -32601, "missing") == {
        "jsonrpc": "2.0", "id": "one", "error": {"code": -32601, "message": "missing"}
    }


@pytest.mark.parametrize("method", [None, "", 3, [], {}])
def test_unknown_method_keeps_historical_error(method):
    def unused(*args):
        raise AssertionError("unknown methods must not invoke capabilities")

    response = handle_message(
        {"id": 5, "method": method},
        instructions=unused, list_tools=unused, call_tool=unused,
    )
    assert response == error(5, -32601, f"method not found: {method}")


def test_serve_parse_and_handler_errors_do_not_end_stream():
    writer = io.BytesIO()

    def handle(message):
        if message.get("id") == 1:
            raise RuntimeError("fixture failure")
        return result(message.get("id"), {})

    serve(io.BytesIO(b'\xff\n{"id":1}\n{"id":2}\n'), writer, handle)
    replies = [decode_line(line) for line in writer.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["error"]["code"] == -32603
    assert replies[2] == result(2, {})


@pytest.mark.parametrize("write_fails", [False, True])
def test_server_owns_resource_close_order_even_after_stream_failure(write_fails):
    closed = []

    class Resource:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    class Writer(io.BytesIO):
        def write(self, data):
            if write_fails:
                raise OSError("writer closed")
            return super().write(data)

    server = McpServer(
        Resource("bridge"), reader=io.BytesIO(b'{"id":1,"method":"ping"}\n'),
        writer=Writer(), skill_reference=Resource("reference"),
        snapshot_store=Resource("snapshot"),
    )
    server._pdk_session = Resource("pdk")
    if write_fails:
        with pytest.raises(OSError, match="writer closed"):
            server.run()
    else:
        assert server.run() == 0
    assert closed == ["pdk", "snapshot", "reference", "bridge"]
