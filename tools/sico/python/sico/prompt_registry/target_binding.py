"""Single authority: preflight, target focus, and view reuse rules."""

INSTRUCTIONS = (
    "任务来源由宿主显式捕获并校验；不要按当前鼠标焦点选择目标。"
    "工程绑定在入口窗口关闭后依然有效。用 get_project_context 获取工程身份并做显式目标 "
    "preflight；不可用的窗口事实不是实时设计数据。"
    "window_ref 为空时不要调用 get_entry_context；工程级任务用 get_project_context 即可。"
    "每次操作都按当前目标与 create/read 意图刷新 preflight；视图创建之后，"
    "后续 preflight 不得再对该已存在视图保留 create 要求。"
    "preflight 失败是一个返回的检查结果：解释失败的检查项并在授权范围内解决。"
    "已存在的空视图仍然存在；关闭它并不会让它消失。"
    "绝不要为了满足 create 意图而要求用户删除视图。"
    "不要因为捕获窗口缺失就推断必须打开编辑器。"
)
