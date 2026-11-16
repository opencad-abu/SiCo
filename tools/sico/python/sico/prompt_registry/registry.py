"""Central registry and single assembly entry point for all system prompts.

Usage:
    from sico.prompts.registry import assemble
    system = assemble("copilot", context_record=ctx, workbench=True)
"""

from __future__ import annotations

import json

from cadai.skill_access_guidance import INSTRUCTIONS as _SKILL_ACCESS

from .artifact_paths import INSTRUCTIONS as _ARTIFACT_PATHS
from .circuit_workflow import INSTRUCTIONS as _CIRCUIT_WORKFLOW
from .oa_access import INSTRUCTIONS as _OA_ACCESS
from .output_style import INSTRUCTIONS as _OUTPUT_STYLE
from .target_binding import INSTRUCTIONS as _TARGET_BINDING
from .task_routing import INSTRUCTIONS as _TASK_ROUTING
from .untrusted_data import INSTRUCTIONS as _UNTRUSTED_DATA

# Each entry:  name -> (responsibility, char_budget, used_by_roles)
_REGISTRY: dict[str, tuple[str, int, tuple[str, ...]]] = {
    "artifact_paths":    ("附件路径解析与结果完整性约束",                           300,  ("codex_backend",)),
    "circuit_workflow":  ("begin_circuit_task、preflight、request_id 不重放规则", 600,  ("codex_backend", "assistant")),
    "oa_access":         ("OA 数据库只能经平台 API 读写",                          500,  ("codex_backend",)),
    "output_style":      ("模型回复格式与表述规则",                                  400,  ("codex_backend", "assistant")),
    "skill_access":      ("Cadence 对象属性访问运算符与 DPL 探查规则",              500,  ("codex_backend", "assistant")),
    "target_binding":    ("来源绑定、窗口焦点禁止、视图复用规则",                   500,  ("codex_backend",)),
    "task_routing":      ("任务路由与 tool profile 选择规则",                       400,  ("codex_backend",)),
    "untrusted_data":    ("工具输出不可信、截断要报告",                              200,  ("codex_backend", "assistant")),
}


def registry() -> dict[str, tuple[str, int, tuple[str, ...]]]:
    """Return a copy of the fragment registry for inspection and testing."""
    return dict(_REGISTRY)


def assemble(
    role: str,
    *,
    product_name: str = "Silicon Copilot",
    context_record: dict | None = None,
    workbench: bool = False,
    pdk_instructions: str = "",
    circuit_instructions: str = "",
    knowledge_instructions: str = "",
    workbench_instructions: str = "",
) -> str:
    """Return the complete system prompt string for *role*.

    Roles:
      ``codex_backend``   – Codex native back-end (backend_instructions.py)
      ``copilot``         – reserved for future Python agent use
      ``assistant``       – AI Assistant MCP server (launch_policy.py)

    All callers pass the same named arguments; unused arguments are silently
    ignored so callers can be updated incrementally.
    """
    parts: list[str] = []

    if role == "codex_backend":
        parts.append(
            f"你是 Cadence Virtuoso 内的 {product_name}。"
            "通过已注册的 Virtuoso MCP 工具和 SKILL 运行时读取设计数据。"
            "支持范围内的分析使用会话配置的 Python，临时产物使用会话提供的 TMPDIR；"
            "不要根据当前工作目录另建状态根。"
        )
        parts.append(_TARGET_BINDING)
        parts.append(_TASK_ROUTING)
        parts.append(_CIRCUIT_WORKFLOW)
        parts.append(_SKILL_ACCESS)
        parts.append(_UNTRUSTED_DATA)
        parts.append(_OA_ACCESS)
        parts.append(_ARTIFACT_PATHS)
        if pdk_instructions:
            parts.append(pdk_instructions)
        if circuit_instructions:
            parts.append(circuit_instructions)
        if knowledge_instructions:
            parts.append(knowledge_instructions)
        if workbench and workbench_instructions:
            parts.append(workbench_instructions)
        parts.append(_OUTPUT_STYLE)
        if context_record is not None:
            parts.append("绑定的目标不得跟随窗口焦点。上下文数据：\n"
                         + json.dumps(context_record, ensure_ascii=False))

    elif role == "assistant":
        parts.append(
            "你是 Cadence Virtuoso 内的 AI Assistant，"
            "通过 MCP server 连接到当前 Virtuoso 进程。"
            "实时 Virtuoso 上下文具有权威性；不要根据终端工作目录猜测活动设计。"
        )
        parts.append(_CIRCUIT_WORKFLOW)
        parts.append(_SKILL_ACCESS)
        parts.append(_UNTRUSTED_DATA)
        if pdk_instructions:
            parts.append(pdk_instructions)
        if circuit_instructions:
            parts.append(circuit_instructions)
        parts.append(_OUTPUT_STYLE)

    else:
        raise ValueError(f"Unknown prompt role: {role!r}")

    return " ".join(p.strip() for p in parts if p.strip())
