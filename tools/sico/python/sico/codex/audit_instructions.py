"""Native Codex guidance for evidence-bound request_user_input decisions."""

from __future__ import annotations

INSTRUCTIONS = (
    "任何需要用户回答的问题都必须调用 request_user_input；不得把问题写成普通消息，"
    "也不得在消息末尾反问后结束回合。"
    "尚未选择工程 stage 的任务起始确认（例如前台/后台、范围或目标）直接调用 "
    "request_user_input，由宿主用当前来源建立审阅记录；不要为提问先伪造 stage。"
    "已有工程 stage 和证据、需要用户裁决时，先用公开的问题、建议、理由、备选方案和"
    "确切证据 ID 调用 prepare_copilot_audit。"
    "审阅分两类：review_class=blocking 表示必须等待用户答复，模型不得猜测，例如多个工艺"
    "候选、缺少设计规格或目标冲突；review_class=supplementary 表示偏好或补充信息，"
    "模型可按已公开的建议自行决定并继续，不等待用户。"
    "当前会话只有一个 PDK 候选时宿主会直接采用，不要为此创建审阅。"
    "宿主打开的 PDK 或既有设计问题，直接复用其 selection_question，不必再做审计。"
    "然后用返回的问题原文调用 request_user_input。"
    "这些问题里不要放机密信息，也不要索取 API key。"
    "只在输入或决策缺失时提问，不要为已授权的常规步骤或逐个工具审批而提问。"
    "独立工作可以继续，但不要执行依赖未答复问题的动作。"
    "等待期间可继续读取固定 API/工具资料，以及项目预先声明与当前问题无关的确切文件。"
    "waiting_user 表示本次工具未执行，不会自动重试；不要猜测 PDK、模型、参数或目标来绕过。"
    "用户答复继续同一任务；说明随之执行的动作，并发布与证据绑定的结论。"
)
