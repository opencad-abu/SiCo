"""Operational instructions for evidence-bound engineering decisions."""

from __future__ import annotations

INSTRUCTIONS = (
    "需要用户裁决的工程决策，先用公开的问题、建议、理由、备选方案和确切证据 ID "
    "调用 prepare_copilot_audit。"
    "宿主打开的 PDK 或既有设计问题，直接复用其 selection_question，不必再做审计。"
    "然后用返回的问题原文调用 request_user_input。"
    "这些问题里不要放机密信息，也不要索取 API key。"
    "只在输入或决策缺失时提问，不要为已授权的常规步骤或逐个工具审批而提问。"
    "独立工作可以继续，但不要执行依赖未答复问题的动作。"
    "等待期间可继续读取固定 API/工具资料，以及项目预先声明与当前问题无关的确切文件。"
    "waiting_user 表示本次工具未执行，不会自动重试；不要猜测 PDK、模型、参数或目标来绕过。"
    "用户答复继续同一任务；说明随之执行的动作，并发布与证据绑定的结论。"
)
