"""Live Codex settings with compatibility exports for captured option validation."""

from __future__ import annotations

import math
import os

from sicoenv import read as environment_setting, publish as publish_environment

from cadai.codex_selection import codex_tool_format
from cadai.env_names import name as env_name
from cadai.env_names import value as env_value

from cadai.provider_settings import validate_key
# Compat: retire after supported imports use configuration.codex.
from ..configuration.codex import DEFAULT_IDLE_TIMEOUT, codex_idle_timeout, validate_codex
from . import CODEX_VERSION

DEFAULT_TOOL_TIMEOUT = 1800


def codex_tool_timeout(environment):
    """Resolve the native MCP budget used by the app-server and bridge."""
    for name in ("SICO_MCP_TIMEOUT", "SICO_AI_MCP_TIMEOUT"):
        raw = environment_setting(environment, name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except ValueError:
            raise ValueError("Invalid Codex MCP timeout") from None
        if not math.isfinite(value) or value < 1 or value > 3600:
            raise ValueError("Invalid Codex MCP timeout")
        return int(math.ceil(value))
    return DEFAULT_TOOL_TIMEOUT


def codex_base_label(version=CODEX_VERSION):
    """Corner-badge name of the Codex base: ``codex-`` plus the full version.

    启动时已经用 ``codex --version`` 校验过这个版本号，所以角标显示的就是实际基座版本，
    不再截断成两段或改写成 ``v0.156`` 这样的定制写法。
    """
    return f"codex-{version}"


class CodexSettings:
    def __init__(self, config, environment=None):
        self.endpoint = validate_codex(config)
        # One derivation for every consumer: the provider root and its listing.
        self.base_url = self.endpoint.rsplit("/responses", 1)[0]
        self.models_endpoint = self.base_url + "/models"
        self.options = dict(config)
        self.environment = dict(os.environ if environment is None else environment)
        self.api_key = self.environment.get(config.get("api_key_env", env_name("API_KEY")), "")
        validate_key(self.api_key)
        self.model = config["model"]
        self.timeout = config.get("timeout", 30)
        self.idle_timeout = codex_idle_timeout(self.environment, config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT))
        self.tool_timeout = codex_tool_timeout(self.environment)
        self.tool_format = codex_tool_format(self.environment, config.get("tool_format", "native"))
        self.options["tool_format"] = self.tool_format
        self.base = codex_base_label()
        self.label = f"Codex {CODEX_VERSION} / {self.model} / {self.tool_format}"

    def executable(self):
        from cadai.launch import resolve_agent_executable

        environment = dict(self.environment)
        configured = self.options.get("codex_executable", env_value(environment, "CODEX_CLI"))
        # Terminal-only legacy overrides do not select the desktop engine.
        environment.pop("CAD_CODEX_CLI", None)
        if configured is not None:
            publish_environment(environment, "SICO_CODEX_CLI", configured)
        return resolve_agent_executable("codex", environment=environment)
