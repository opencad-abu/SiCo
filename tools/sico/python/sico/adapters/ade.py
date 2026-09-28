"""Read bounded current ADE setup and history metadata for the fixed GUI target."""

from __future__ import annotations

from ..core.contracts import ToolResult
from ..core.tools import ReadTool, no_arguments
from ..transport.methods import QueryUnavailable


def register_ade(registry, broker):
    descriptions = {
        "read_ade_setup": (
            "Read current live ADE setup: testbench, simulator, configured output expressions, "
            "global variables and corner names. Uses the bound test, or the bound session. "
            "This is current setup, not a History checkpoint or evaluated simulation results. "
            "Counts and truncation limits are explicit. Requires a bound ADE window."
        ),
        "read_ade_history": (
            "List History metadata in the bound ADE session, or only the captured History. "
            "Returns names, result database references and any available completion counts. "
            "Counts are observations, not evidence that simulations passed. No result files "
            "are opened, expressions evaluated or simulation jobs started."
        ),
    }

    def handler(method):
        def read(inputs, context):
            if not context.snapshot.get("ade_session"):
                return ToolResult(
                    "unavailable", "请从 ADE 窗口或 Results/History 菜单重新打开助手。",
                    {"code": "ade_binding_required", "availability": "requires_binding",
                     "next_action": "open_assistant_from_ade", "method": method},
                )
            try:
                data = broker.read(context, method)
            except QueryUnavailable as exc:
                return ToolResult(exc.code, str(exc))
            summary = (
                "当前 ADE 设置（输出表达式尚未求值）"
                if method == "read_ade_setup"
                else "History 元数据（未读取仿真数值）"
            )
            if data.get("truncated"):
                summary += "；已达到查询上限，返回部分数据"
            return ToolResult(summary=summary, data=data)

        return read

    for method, description in descriptions.items():
        registry.register(
            ReadTool(
                method,
                description,
                {"type": "object", "properties": {}, "additionalProperties": False},
                no_arguments,
                handler(method),
            )
        )
