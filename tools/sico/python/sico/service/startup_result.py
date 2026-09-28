"""Startup result and user-facing failure values; no resource construction."""

from dataclasses import dataclass

from cadai.codex_selection import CodexConfigurationError


class StartupFailure(Exception):
    def __init__(self, phase, error):
        self.title = ("模型配置" if phase in {"config", "credentials", "provider"}
                      else "已有一个 SiCo 窗口在运行" if phase == "window" else "启动失败")
        if phase == "window":
            message = str(error)
        elif isinstance(error, CodexConfigurationError):
            message = str(error)
        elif self.title == "模型配置":
            message = (
                f"无法加载模型配置：{type(error).__name__}\n"
                "请检查 SICO_BACKEND 与 SICO_PROVIDER\n"
                "（python: anthropic/openai；codex: responses）、\n"
                "SICO_API_URL 和 SICO_MODEL 是否同时设置，\n"
                "以及 HTTPS 地址、Key 或显式指定的 JSON 配置。"
            )
        else:
            message = f"无法连接工程或加载会话：{type(error).__name__}\n{error}"
        super().__init__(message)


@dataclass(frozen=True)
class StartupReady:
    frontend: object
    initialize: bool
    persistent: bool = True
    initial_text: str = ""
    attach_targets: bool = True
