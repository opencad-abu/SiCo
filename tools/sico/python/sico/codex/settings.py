"""Explicit Codex configuration; existing model protocol settings remain independent."""

from __future__ import annotations

import math
import os

from sicoenv import read as environment_setting, publish as publish_environment

from cadai.codex_selection import codex_tool_format
from cadai.env_names import name as env_name
from cadai.env_names import value as env_value

from cadai.provider_settings import provider_endpoint, validate_key
from . import CODEX_VERSION

DEFAULT_TOOL_TIMEOUT = 1800
DEFAULT_IDLE_TIMEOUT = 900


def codex_idle_timeout(environment, configured=DEFAULT_IDLE_TIMEOUT):
    raw = env_value(environment, "MODEL_IDLE_TIMEOUT") or configured
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError("Invalid Codex model idle timeout") from None
    if isinstance(raw, bool) or not math.isfinite(value) or not 1 <= value <= 86400:
        raise ValueError("Invalid Codex model idle timeout")
    return int(math.ceil(value))


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


def validate_codex(config):
    if not isinstance(config, dict):
        raise ValueError("Codex configuration must be a JSON object")
    allowed = {
        "backend",
        "provider",
        "base_url",
        "model",
        "api_key_env",
        "timeout",
        "idle_timeout",
        "codex_executable",
        "tool_format",
        "linux_sandbox",
        "external_resources",
    }
    if set(config) - allowed or config.get("provider") != "responses":
        raise ValueError("Codex requires provider=responses and supported Codex options")
    if config.get("tool_format", "native") not in ("native", "flat"):
        raise ValueError("Codex tool_format must be native or flat")
    if config.get("linux_sandbox", "default") not in ("default", "legacy-landlock"):
        raise ValueError("Unsupported Codex Linux sandbox selection")
    endpoint = provider_endpoint(config.get("base_url"), "responses")
    model = config.get("model")
    if not isinstance(model, str) or not model.strip() or any(ord(c) < 32 for c in model):
        raise ValueError("A valid Codex model is required")
    key = config.get("api_key_env", env_name("API_KEY"))
    if not isinstance(key, str) or not key.isidentifier():
        raise ValueError("Invalid API credential environment reference")
    timeout = config.get("timeout", 30)
    if type(timeout) not in {int, float} or not 0 < timeout <= 120:
        raise ValueError("Invalid Codex connection timeout")
    codex_idle_timeout({}, config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT))
    executable = config.get("codex_executable", "codex")
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("Invalid Codex executable")
    if "external_resources" in config:
        from .resource_config import path_value

        path_value(config["external_resources"])
    return endpoint


def codex_base_label(version=CODEX_VERSION):
    """Corner-badge name of the Codex base: ``codex-`` plus the full version.

    启动时已经用 ``codex --version`` 校验过这个版本号，所以角标显示的就是实际基座版本，
    不再截断成两段或改写成 ``v0.156`` 这样的定制写法。
    """
    return f"codex-{version}"


class CodexSettings:
    def __init__(self, config, environment=None):
        self.endpoint = validate_codex(config)
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
