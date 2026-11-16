"""Single authority: artifact path resolution rule."""

INSTRUCTIONS = (
    "工具大输出的 attachments/<session>/<sha256>.json 路径以会话根目录 .sico/ai/agent 为基准；"
    "读取时把给出的 path 原样交给 read_artifact，不要让 shell 以工程 cwd 猜测相对路径。"
    "一次工具调用完成并不代表设计或仿真已满足规格。"
    "模型回合被取消不代表外部 EDA 作业已经停止。"
)
