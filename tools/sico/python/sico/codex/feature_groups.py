"""Declarative Codex built-in feature groups, their defaults and recovery.

Every process-start option that selects a built-in Codex capability belongs to
exactly one group here: the group names its purpose, the lifecycle necessity
of its default, how to enable it again, and whether a session may override it
through the journal (``codex.features.configured``).  ``runtime_options`` is
the single source the runtime builds its app-server options from, so the
defaults cannot drift between the declaration and the wire command.

``features.memories``/``memories.*`` are owned by ``thread_memory`` (they have
their own thread-level mode and readback) and the provider-selected sandbox
compatibility flag stays in provider options; neither is duplicated here.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

from ..storage.journal import BULK_KINDS

CONFIGURED_EVENT = "codex.features.configured"
PROJECT_FILE = "feature-groups.json"
_MAX_PROJECT_BYTES = 8192
_NECESSITY = frozenset({"required", "default_on", "review", "required_off"})

# Ordered exactly like the app-server command line was written before.
GROUPS = (
    {
        "id": "goals",
        "purpose": "任务目标与进度跟踪（get_goal/create_goal/update_goal）",
        "necessity": "default_on",
        "recovery": "会话特性配置中重新打开 goals 组",
        "evidence": "exec 透传实测：关闭后移除 get_goal/create_goal/update_goal（−3 工具/−3,216 B），指令不变",
        "configurable": True,
        "options": {"features.goals": True},
    },
    {
        "id": "project_instructions",
        "purpose": "工程 AGENTS.md 指令（兼容 AGENT.md）",
        "necessity": "default_on",
        "recovery": "会话特性配置中重新打开 project_instructions 组",
        "evidence": "Codex 原生按工程 cwd 向上发现并注入 AGENTS.md；显式保留 32 KiB 预算和 AGENT.md 回退名",
        "configurable": True,
        "options": {
            "project_doc_max_bytes": 32 * 1024,
            "project_doc_fallback_filenames": ["AGENT.md"],
        },
    },
    {
        "id": "shell",
        "purpose": "模型自带命令执行与终端复用（exec_command/write_stdin）",
        "necessity": "required",
        "recovery": "会话特性配置中重新打开 shell 组",
        "evidence": "实测关闭后移除 exec_command/write_stdin（−2 工具/−2,350 B）",
        "configurable": True,
        "options": {"features.shell_tool": True, "features.shell_snapshot": True},
    },
    {
        "id": "unified_exec",
        "purpose": "统一 exec 实现（长任务、TTY 复用）",
        "necessity": "required",
        "recovery": "会话特性配置中重新打开 unified_exec 组",
        "evidence": "实测关闭不改变工具集合；exec_command 自身契约缩小 114 B",
        "configurable": True,
        "options": {"features.unified_exec": True, "features.unified_exec_tty": True},
    },
    {
        "id": "request_user_input",
        "purpose": "默认模式下的用户输入询问通道",
        "necessity": "default_on",
        "recovery": "会话特性配置中重新打开 request_user_input 组",
        "evidence": "实测关闭不改变请求（该开关不控制工具是否注入）",
        "configurable": True,
        "options": {"features.default_mode_request_user_input": True},
    },
    {
        "id": "multi_agent",
        "purpose": "原生子任务协作与父/子生命周期",
        "necessity": "required",
        "recovery": "会话特性配置中重新打开 multi_agent 组",
        "evidence": "实测关闭移除 5 个 multi_agent_v1__*（−10,135 B）",
        "configurable": True,
        "options": {"features.multi_agent": True, "features.multi_agent_v2": True},
    },
    {
        "id": "view_image",
        "purpose": "读取本地图片（截图/版图审查）",
        "necessity": "default_on",
        "recovery": "会话特性配置中重新打开 view_image 组",
        "evidence": "实测关闭移除 view_image（−372 B）",
        "configurable": True,
        "options": {"features.view_image": True},
    },
    {
        "id": "image_generation",
        "purpose": "模型侧图片生成",
        "necessity": "review",
        "recovery": "会话特性配置中重新打开 image_generation 组",
        "evidence": "exec 实测开关不改变请求（当前配置未注入工具）",
        "configurable": True,
        "options": {"features.image_generation": True},
    },
    {
        "id": "browser",
        "purpose": "内置浏览器工具",
        "necessity": "review",
        "recovery": "会话特性配置中重新打开 browser 组",
        "evidence": "exec 实测开关不改变请求（当前配置未注入工具）",
        "configurable": True,
        "options": {"features.browser_use": True},
    },
    {
        "id": "computer",
        "purpose": "内置计算机操作工具",
        "necessity": "review",
        "recovery": "会话特性配置中重新打开 computer 组",
        "evidence": "exec 实测开关不改变请求（当前配置未注入工具）",
        "configurable": True,
        "options": {"features.computer_use": True},
    },
    {
        "id": "sleep",
        "purpose": "等待工具（轮询节流）",
        "necessity": "review",
        "recovery": "会话特性配置中重新打开 sleep 组",
        "evidence": "exec 实测开关不改变请求（当前配置未注入工具）",
        "configurable": True,
        "options": {"features.sleep_tool": True},
    },
    {
        "id": "apps",
        "purpose": "已配置的应用/连接器工具",
        "necessity": "required",
        "recovery": "会话特性配置中重新打开 apps 组（用户已选择的连接器不得静默禁用）",
        "evidence": "实测无已配置应用时请求不变；用户已选择的连接器不得静默禁用，生产配置需复测",
        "configurable": True,
        "options": {"features.apps": True},
    },
    {
        "id": "plugins",
        "purpose": "原生插件（默认关闭，避免启动时访问 GitHub 同步插件目录）",
        "necessity": "review",
        "recovery": "确需本地插件时，在未绑定运行实例的会话中启用 plugins；Codex 0.162.1 同时会恢复 GitHub 同步，无法单独关闭同步",
        "evidence": "固定 0.162.1 实测：关闭后 plugin/read 拒绝访问；普通技能和独立 MCP 不依赖此开关",
        "configurable": True,
        "options": {"features.plugins": False},
    },
    {
        "id": "elicitation",
        "purpose": "MCP 表单/URL 交互请求",
        "necessity": "default_on",
        "recovery": "会话特性配置中重新打开 elicitation 组",
        "evidence": "实测关闭不改变工具请求（交互通道在调用期生效）",
        "configurable": True,
        "options": {"features.auth_elicitation": True,
                    "features.tool_call_mcp_elicitation": True},
    },
    {
        "id": "skill_dependency_install",
        "purpose": "技能声明依赖的自动安装",
        "necessity": "review",
        "recovery": "会话特性配置中重新打开 skill_dependency_install 组",
        "evidence": "实测关闭不改变工具请求（依赖安装在调用期生效）",
        "configurable": True,
        "options": {"features.skill_mcp_dependency_install": True},
    },
    {
        "id": "web_search",
        "purpose": "联网检索",
        "necessity": "review",
        "recovery": "会话特性配置中重新打开 web_search 组",
        "evidence": "实测关闭移除内置 web_search 工具（小 schema，−50 B）",
        "configurable": True,
        "options": {"web_search": "live"},
    },
    {
        "id": "request_compression",
        "purpose": "请求压缩（ResponsesWrapper 要求关闭）",
        "necessity": "required_off",
        "recovery": "仅当网关支持压缩时由 provider/启动配置更改",
        "evidence": "ResponsesWrapper 要求 identity 编码；属协议兼容而非可选能力",
        "configurable": False,
        "options": {"features.enable_request_compression": False},
    },
    {
        "id": "connection_retries",
        "purpose": "无界连接重试（策略要求有界）",
        "necessity": "required_off",
        "recovery": "由 provider/启动配置更改",
        "evidence": "有界重试为策略要求（0 重试由 provider 层负责）",
        "configurable": False,
        "options": {"features.unbounded_connection_retries": False},
    },
    {
        "id": "analytics",
        "purpose": "客户端遥测（保持关闭）",
        "necessity": "required_off",
        "recovery": "由 provider/启动配置更改",
        "evidence": "客户端遥测保持关闭",
        "configurable": False,
        "options": {"analytics.enabled": False},
    },
    {
        "id": "sandbox",
        "purpose": "审批与工作区沙箱边界",
        "necessity": "required",
        "recovery": "由 provider/启动配置更改（安全边界不随会话开关切换）",
        "evidence": "审批与沙箱边界为安全要求，不随会话开关切换",
        "configurable": False,
        "options": {"approval_policy": "never", "approvals_reviewer": "user",
                    "sandbox_mode": "workspace-write"},
    },
)

INDEX = {group["id"]: group for group in GROUPS}


def option_defaults() -> dict:
    """Every option value this registry owns, in declaration order."""
    merged: dict = {}
    for group in GROUPS:
        for key, value in group["options"].items():
            if key in merged:
                raise ValueError(f"feature option declared twice: {key}")
            merged[key] = value
    return merged


def validate_overrides(groups) -> dict:
    """One session's ``{group_id: {option: value}}``; unknown/fixed groups fail."""
    if groups is None:
        return {}
    if not isinstance(groups, dict):
        raise ValueError("feature overrides require a group map")
    result: dict = {}
    for group_id, options in groups.items():
        group = INDEX.get(group_id)
        if group is None:
            raise ValueError(f"unknown feature group: {group_id}")
        if not group["configurable"]:
            raise ValueError(
                f"feature group {group_id} is fixed by provider/launch policy")
        if not isinstance(options, dict) or not options:
            raise ValueError(f"feature group {group_id} needs an option map")
        for key, value_ in options.items():
            if key not in group["options"]:
                raise ValueError(f"{key} does not belong to feature group {group_id}")
            expected = group["options"][key]
            if type(value_) is not type(expected):
                raise ValueError(f"{key} expects {type(expected).__name__}")
        result[group_id] = dict(options)
    return result


def runtime_options(overrides=None) -> dict:
    """The option values one runtime should start with."""
    merged = option_defaults()
    for options in validate_overrides(overrides).values():
        merged.update(options)
    return merged


def configured_overrides(journal):
    """The last explicit session override, or None for the registry defaults."""
    result = None
    for row in journal.events(exclude=BULK_KINDS):
        if row["kind"] == CONFIGURED_EVENT:
            payload = row["payload"]
            if set(payload) != {"groups"}:
                raise ValueError("feature configuration requires one 'groups' field")
            result = validate_overrides(payload["groups"])
    return result


def describe(overrides=None) -> list[dict]:
    """Per-group settings view: purpose, default need, current value, recovery."""
    effective = runtime_options(overrides)
    return [
        {
            "id": group["id"],
            "purpose": group["purpose"],
            "necessity": group["necessity"],
            "configurable": group["configurable"],
            "recovery": group["recovery"],
            "evidence": group["evidence"],
            "options": {key: effective[key] for key in group["options"]},
            "overridden": any(
                (overrides or {}).get(group["id"], {}).get(key, effective[key]) != value
                for key, value in group["options"].items()),
        }
        for group in GROUPS
    ]


def project_default_path(state_root) -> Path:
    return Path(state_root) / "ai" / PROJECT_FILE


def read_project_default(path):
    """One bounded operator/UI-written project default, or None when absent."""
    path = Path(path)
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) > _MAX_PROJECT_BYTES:
        raise ValueError(f"{path}: feature-group default exceeds {_MAX_PROJECT_BYTES} bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid feature-group default") from exc
    if not isinstance(value, dict) or set(value) != {"groups"}:
        raise ValueError(f"{path}: expected exactly one 'groups' field")
    return validate_overrides(value["groups"])


def write_project_default(path, groups):
    """Atomically replace the project default used by future sessions."""
    path = Path(path)
    value = {"groups": validate_overrides(groups)}
    body = json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8")
    if len(body) > _MAX_PROJECT_BYTES:
        raise ValueError("feature-group default exceeds its bound")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return value


class FeatureSetting:
    """Session-scoped feature-group overrides, writable before runtime binding."""

    def __init__(self, backend):
        self.backend = backend
        session = configured_overrides(backend.journal)
        state = Path(backend.journal.root).parents[1]
        project = read_project_default(project_default_path(state))
        self.project_groups = project or {}
        self.groups = session if session is not None else self.project_groups
        self.source = "session" if session is not None else (
            "project" if project else "default")

    def read(self):
        return {
            "groups": describe(self.groups),
            "overrides": deepcopy(self.groups),
            "project_overrides": deepcopy(self.project_groups),
            "source": self.source,
            "bound": getattr(self.backend, "runtime", None) is not None,
        }

    def save(self, groups):
        if getattr(self.backend, "runtime", None) is not None:
            raise ValueError(
                "feature settings require a new session; the current runtime is already bound")
        self.groups = validate_overrides(groups)
        self.source = "session"
        self.backend.task_journal.emit(CONFIGURED_EVENT, {"groups": self.groups})
        return self.read()


__all__ = [
    "CONFIGURED_EVENT", "FeatureSetting", "GROUPS", "INDEX", "PROJECT_FILE",
    "configured_overrides", "describe", "option_defaults", "project_default_path",
    "read_project_default", "runtime_options", "validate_overrides",
    "write_project_default",
]
