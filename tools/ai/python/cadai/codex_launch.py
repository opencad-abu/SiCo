"""Codex terminal client configuration, including its optional gateway binding."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path

from cadenv import EDA_TEMP_MARKERS
from sicosessionenv import ENVIRONMENT_NAMES as SESSION_ENVIRONMENT_NAMES

from .agent_profile import INTERACTIVE_PROFILE, allowed_tool_names, validate_profile
from .mcp_command import mcp_command
from .codex_selection import CodexConfigurationError, codex_tool_format, require_supported_runtime
from .launch_policy import MCP_TOOL_NAMES as _ALL_MCP_TOOLS
from .launch_policy import assistant_instructions
from .optional_resources import ENVIRONMENT_KEYS as OPTIONAL_RESOURCE_KEYS
from .model_environment import RETIRED_MODEL_KEYS, settings as model_settings, publish_gateway
from .tool_help_schema import DOC_ENV_NAMES

_SICO_API_PROVIDER = "sico-environment"


_OPENAI_API_BASE_URL = "https://api.openai.com/v1"


def _configured_codex_options(environment: Mapping[str, str]) -> list[str]:
    options: list[str] = []
    settings = model_settings(environment)
    model, base_url, api_key = (settings[name] for name in ("MODEL", "API_BASE_URL", "API_KEY"))

    if model:
        options.extend(["-m", model])
    if api_key and not base_url:
        base_url = _OPENAI_API_BASE_URL
    if base_url:
        provider_fields = [
            f"name={json.dumps('SiCo environment')}",
            f"base_url={json.dumps(base_url, ensure_ascii=False)}",
            'wire_api="responses"',
        ]
        if api_key:
            provider_fields.append('env_key="SICO_API_KEY"')
        options.extend(
            [
                "-c",
                f"model_providers.{_SICO_API_PROVIDER}={{" + ",".join(provider_fields) + "}",
                "-c",
                f'model_provider="{_SICO_API_PROVIDER}"',
            ]
        )
    return options


def prepare_codex_gateway(environment: dict[str, str]):
    """Own the real gateway credential in the optional, shared Responses wrapper."""
    settings = model_settings(environment)
    for name in RETIRED_MODEL_KEYS:
        environment.pop(name, None)
    if codex_tool_format(environment) != "flat":
        return None
    from cadai.responses_wrapper import ResponsesWrapper

    base_url, api_key = settings["API_BASE_URL"], settings["API_KEY"]
    if not base_url or not api_key:
        raise CodexConfigurationError(
            "The Responses wrapper requires shared SICO_API_BASE_URL "
            "(or SICO_API_URL) and SICO_API_KEY in the launch environment"
        )
    wrapper = ResponsesWrapper(base_url, api_key)
    for name in ("SICO_API_KEY", "CAD_AGENT_API_KEY", "OPENAI_API_KEY"):
        if environment.get(name) == api_key:
            environment.pop(name)
    publish_gateway(environment, wrapper.base_url, wrapper.token)
    return wrapper


def build_codex_command(
    codex: str,
    python: str,
    workspace: Path,
    mcp_cwd: Path,
    timeout: float,
    environment: Mapping[str, str] | None = None,
    profile: str = INTERACTIVE_PROFILE,
) -> list[str]:
    source = os.environ if environment is None else environment
    require_supported_runtime(source)
    validate_profile(profile)
    quote = json.dumps
    ancestors = list(reversed((workspace, *workspace.parents)))
    projects = (
        "{"
        + ",".join(
            f'{quote(str(path), ensure_ascii=False)}={{trust_level="untrusted"}}'
            for path in ancestors
        )
        + "}"
    )
    mcp = mcp_command(python, mcp_cwd / "sico-ai", timeout, environment=source)
    enabled_tools = list(allowed_tool_names(profile, _ALL_MCP_TOOLS))
    env_vars = [
        *DOC_ENV_NAMES,
        "PATH",
        *SESSION_ENVIRONMENT_NAMES,
        "SICO_HOME",
        "SICO_PYTHON_ROOT",
        "SICO_PYTHON",
        *OPTIONAL_RESOURCE_KEYS,
        "CAD_TEMP_DIR",
        "SICO_TEMP_DIR",
        *EDA_TEMP_MARKERS,
        "TMPDIR",
        "TMP",
        "TEMP",
        "SQLITE_TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
    ]
    command = [
        codex,
        "-C",
        str(workspace),
        "-a",
        "on-request",
        "-s",
        "read-only" if profile != INTERACTIVE_PROFILE else "workspace-write",
        "--disable",
        "plugins",
        "--disable",
        "apps",
        "-c",
        f"projects={projects}",
        "-c",
        f"developer_instructions={quote(assistant_instructions(profile), ensure_ascii=False)}",
        "-c",
        f"shell_environment_policy.set={{SICO_PYTHON={quote(python, ensure_ascii=False)}}}",
        "-c",
        (
            'shell_environment_policy.exclude=["SICO_AI_*","SICO_CODEX_*","SICO_CLAUDE_*","CAD_AI_*","CAD_CODEX_*","CAD_CLAUDE_*",'
            '"SICO_API_KEY","OPENAI_API_KEY","ANTHROPIC_*","CLAUDE_CODE_*"]'
        ),
        "-c",
        f"mcp_servers.virtuoso.command={quote(mcp[0], ensure_ascii=False)}",
        "-c",
        (f"mcp_servers.virtuoso.args={quote(mcp[1:], ensure_ascii=False)}"),
        "-c",
        f"mcp_servers.virtuoso.cwd={quote(str(mcp_cwd), ensure_ascii=False)}",
        "-c",
        f"mcp_servers.virtuoso.env_vars={json.dumps(env_vars, separators=(',', ':'))}",
        "-c",
        f"mcp_servers.virtuoso.enabled_tools={json.dumps(enabled_tools)}",
        "-c",
        "mcp_servers.virtuoso.required=true",
        "-c",
        "mcp_servers.virtuoso.startup_timeout_sec=10",
        "-c",
        f"mcp_servers.virtuoso.tool_timeout_sec={math.ceil(timeout) + 10}",
    ]
    command.extend(_configured_codex_options(source))
    if codex_tool_format(source) == "flat":
        command.extend([
            "-c", "features.enable_request_compression=false",
            "-c", f"model_providers.{_SICO_API_PROVIDER}.supports_websockets=false",
        ])
    return command
