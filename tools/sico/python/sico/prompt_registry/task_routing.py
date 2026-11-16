"""Single authority: task routing and tool profile selection rules."""

INSTRUCTIONS = (
    "工具能力由会话自动解析，按当前输入选择即可；不要要求用户选择或修改 tool profile。"
    "只有宿主显式以 SICO_PDK_WORKFLOW=design 限定时，才报告该环境限制。"
    "普通读取、参数核对、CDF/原理图对比等小任务直接调用相应工具；"
    "它们不需要先归属某个流程，也不需要创建 workflow。"
    "先判断任务是简单/通用还是特定流程；特定流程使用其受控入口并自动切换 workflow。"
    "除非宿主确实缺少工具或证据，不要因未选择 profile/流程而拒绝任务。"
    "参数结构不确定、或调用返回 invalid_arguments/unsupported 时，再用 mcp_help 的 "
    "schema/example 取权威模式；已知工具直接调用，不要为每个工具先读帮助。"
    "参数、模型与规格来自用户/项目；缺失的输入要澄清。"
)
