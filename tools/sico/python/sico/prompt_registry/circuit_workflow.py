"""Single authority for begin_circuit_task / foreground-background rule."""

INSTRUCTIONS = (
    "目标电路创建/仿真任务开始时确认前台/后台并调用 begin_circuit_task；"
    "宿主打开的既有设计问题会自动等待用户答复。"
    "execute_circuit_operation 之前先 preview/prepare 并对确切目标做 preflight；"
    "get_circuit_operation_schema 返回完整的操作参数。"
    "中断后用原始 request_id 查询 get_circuit_operation；"
    "绝不用新请求绕过状态不明，也不自动重放。"
)
