"""Validate captured Codex options and idle budgets without constructing a runtime."""

import math

from cadai.env_names import name as env_name
from cadai.env_names import value as env_value
from cadai.provider_settings import provider_endpoint

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
        from ..agent_resources.schema import path_value

        path_value(config["external_resources"])
    return endpoint
