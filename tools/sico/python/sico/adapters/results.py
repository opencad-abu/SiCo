"""Shared immutable analysis tools and an explicit live History binding."""

import json
import uuid

from cadai.ac_response_schema import RESPONSE_NAMES
from cadai.ac_schema import AC_NAMES
from cadai.circuit_spec_schema import validate
from cadai.measurement_catalog import CATALOG_NAMES
from cadai.result_tools import RESULT_NAMES
from cadai.waveform_schema import WAVEFORM_NAMES
from cadai.waveform_spec_schema import SPEC_NAMES

from ..core.contracts import ToolResult
from ..core.tools import Tool

NAMES = RESULT_NAMES | WAVEFORM_NAMES | AC_NAMES | RESPONSE_NAMES | SPEC_NAMES | CATALOG_NAMES
LIVE = frozenset({"read_maestro_results", "read_maestro_waveform", "read_maestro_ac_waveform"})
BIND_SCHEMA = {
    "type": "object",
    "properties": {
        "history": {"type": "string", "minLength": 1, "maxLength": 256},
        "setup_ref": {"type": "string", "minLength": 1, "maxLength": 128},
    },
    "required": ["history"],
    "additionalProperties": False,
}


def register_results(registry, client_for):
    from cadai.mcp import _TOOLS_BY_NAME

    def bind(arguments, context):
        client, _ = client_for(context)
        data = client.send(
            "aiCopilotBindResults",
            [arguments["history"], arguments.get("setup_ref"), uuid.uuid4().hex],
        )
        return ToolResult("ok" if data.get("ok") else "tool_error", data=data)

    registry.register(
        Tool(
            "bind_result_context",
            "Bind an EXACT existing History before read_maestro_results/read_maestro_waveform/"
            "read_maestro_ac_waveform. Omit setup_ref to borrow the captured ADE session; otherwise "
            "use a simulation setup owned by this task source. Returns a read-only context_ref "
            "pinned to source/session/History handle/library path. Never opens, saves, runs or follows "
            "GUI focus. A captured History cannot be overridden. For test-scoped entry, scalar "
            "anchors cover the whole History and waveform reads remain restricted to that test. "
            "Immutable queries, measurements and reports need no live binding or preflight.",
            BIND_SCHEMA,
            lambda args: validate(args, BIND_SCHEMA),
            bind,
        )
    )

    def handler(name):
        def execute(arguments, context):
            _, server = client_for(context)  # Local workspace identity check, no native call.
            reply = server.handle(
                {"jsonrpc": "2.0", "id": "analysis", "method": "tools/call",
                 "params": {"name": name, "arguments": arguments}}
            )
            if "error" in reply:
                return ToolResult("invalid_arguments", reply["error"]["message"])
            result = reply["result"]
            data = json.loads(result["content"][0]["text"])
            # An engineering fail/incomplete report is a successful computation.
            return ToolResult("tool_error" if result.get("isError") else "ok", data=data)

        return execute

    for name in sorted(NAMES):
        definition = _TOOLS_BY_NAME[name]
        schema = definition["inputSchema"]
        registry.register(
            Tool(name, definition["description"], schema,
                 lambda args, schema=schema: validate(args, schema), handler(name),
                 execution_domain="virtuoso" if name in LIVE else "local",
                 annotations=definition["annotations"])
        )
