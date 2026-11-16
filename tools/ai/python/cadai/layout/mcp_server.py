"""Standalone MCP server for the draft layout backend plugin.

Run as ``python -m cadai.layout.mcp_server`` with
``SICO_LAYOUT_WORKSPACE`` set, or with the project workspace as cwd.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from cadai.layout.service import LayoutService
from cadai.layout_schema import LAYOUT_TOOLS
from cadai.mcp_layout_handler import LayoutHandlerArgumentError, dispatch_layout
from cadai.mcp_protocol import handle_message, serve, tool_result

INSTRUCTIONS = (
    "Layout backend MCP plugin. Use list_layout_backends to inspect project backends, "
    "preview_layout_job before start_layout_job, then get_layout_job to collect verified "
    "artifacts. This plugin never writes OA."
)

_TOOLS = {row["name"]: row for row in LAYOUT_TOOLS}


class LayoutMcpSession:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)
        self.service = LayoutService(self.workspace)

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        return handle_message(
            message,
            instructions=lambda: INSTRUCTIONS,
            list_tools=lambda: LAYOUT_TOOLS,
            call_tool=self._call,
        )

    def _call(self, request_id: Any, params: Any) -> dict[str, Any]:
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        if name not in _TOOLS:
            return tool_result(
                request_id, False,
                {"ok": False, "code": "unknown_layout_tool",
                 "message": "unknown layout tool: " + str(name)},
                tool_name=name,
                input_schema=_TOOLS.get(name, {}).get("inputSchema"),
            )
        if not isinstance(arguments, dict):
            return tool_result(
                request_id, False,
                {"ok": False, "code": "invalid_arguments",
                 "message": "arguments must be an object"},
                tool_name=name,
                input_schema=_TOOLS[name]["inputSchema"],
            )
        try:
            ok, detail = dispatch_layout(
                name, arguments, get_service=lambda: self.service
            )
        except LayoutHandlerArgumentError as exc:
            ok, detail = False, {"ok": False, "code": "invalid_arguments",
                                 "message": str(exc)}
        return tool_result(
            request_id, ok, detail,
            tool_name=name, input_schema=_TOOLS[name]["inputSchema"],
        )

    def close(self) -> None:
        self.service.close()

    def run(self, reader=None, writer=None) -> int:
        try:
            serve(
                reader or sys.stdin.buffer,
                writer or sys.stdout.buffer,
                self.handle,
            )
        finally:
            self.close()
        return 0


def main() -> int:
    workspace = os.environ.get("SICO_LAYOUT_WORKSPACE") or os.getcwd()
    path = Path(workspace).expanduser()
    if not path.is_dir():
        print("SICO_LAYOUT_WORKSPACE is not a directory: " + str(path), file=sys.stderr)
        return 2
    return LayoutMcpSession(path).run()


if __name__ == "__main__":
    raise SystemExit(main())
