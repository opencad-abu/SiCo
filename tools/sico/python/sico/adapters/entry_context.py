"""Copilot transport adapter; all tool schemas and entry guidance are shared with MCP."""

from cadai.entry_context import TOOLS, enrich, validate_arguments

from ..core.contracts import ToolResult
from ..core.tools import ReadTool
from ..transport.methods import QueryUnavailable


def register_context(registry, broker):
    def handler(name):
        def read(arguments, context):
            try:
                data = enrich(broker.read(context, name, arguments), name, arguments)
            except QueryUnavailable as exc:
                if exc.code == "source_not_captured":
                    return ToolResult(exc.code,
                        "本次入口未捕获窗口；可先列出窗口并指定 window_ref，"
                        "或按明确的 library/cell/view 继续查询。无需重新安装 SKILL。",
                        {"code": exc.code, "next_action": "list_windows_or_inspect_explicit_target"})
                return ToolResult(exc.code, str(exc))
            return ToolResult(data=data)

        return read

    for definition in TOOLS:
        name = definition["name"]
        registry.register(
            ReadTool(
                name,
                definition["description"],
                definition["inputSchema"],
                lambda args, tool=name: validate_arguments(tool, args),
                handler(name),
            )
        )
