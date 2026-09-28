"""Explicit provider selection; credentials are read from an environment reference."""

from __future__ import annotations

import os
from pathlib import Path

from cadai.codex_selection import codex_tool_format, require_supported_runtime
from cadai.env_names import name as env_name
from cadai.env_names import value as env_value

from ..transport.framing import strict_json
from .anthropic import AnthropicProvider
from .mock import ContextDemoProvider
from .openai import OpenAIProvider
from cadai.provider_settings import provider_endpoint, validate_settings
from ..storage.roots import state_root

ENV_OPTIONS = ("API_URL", "MODEL")


def credential_reference(environment):
    """Return the current credential reference without copying secret values."""
    env_value(environment, "API_KEY")
    return env_name("API_KEY")


def environment_config(environment):
    require_supported_runtime(environment)
    url, model = (str(env_value(environment, suffix, "")).strip() for suffix in ENV_OPTIONS)
    backend = str(env_value(environment, "BACKEND", "codex")).strip().lower() or "codex"
    if backend not in {"python", "codex"}:
        raise ValueError("SICO_BACKEND must be python or codex")
    default_protocol = "responses" if backend == "codex" else "anthropic"
    protocol = str(env_value(environment, "PROVIDER", "")).strip().lower() or default_protocol
    if protocol not in ({"responses"} if backend == "codex" else {"anthropic", "openai"}):
        raise ValueError("SICO_PROVIDER must match the selected backend (Codex: responses)")
    if not url and not model:
        return None
    if not url or not model:
        raise ValueError("SICO_API_BASE_URL (or SICO_API_URL) and SICO_MODEL must be set together")
    config = {
        "provider": protocol,
        "base_url": url,
        "model": model,
        "api_key_env": credential_reference(environment),
    }
    if backend == "codex":
        config.update(
            backend="codex",
            tool_format=codex_tool_format(environment),
            linux_sandbox=str(env_value(environment, "LINUX_SANDBOX", "default")),
        )
        cli = str(env_value(environment, "CODEX_CLI", "")).strip()
        if cli:
            config["codex_executable"] = cli
    return config


def provider_config_path(launch_dir=None, explicit=None, environment=None):
    env = os.environ if environment is None else environment
    require_supported_runtime(env)
    configured = explicit or str(env_value(env, "PROVIDER_CONFIG", "")).strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else Path(launch_dir or os.getcwd()) / path
    if environment_config(env) is not None:
        return None
    if launch_dir is not None:
        default = state_root(launch_dir) / "ai/agent-provider.json"
        if default.is_file():
            return default
    return None


def read_provider_config(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16_385)
    if len(raw) > 16_384:
        raise ValueError("Provider configuration exceeds size limit")
    return validate_config(strict_json(raw))


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Provider configuration must be a JSON object")
    if config.get("backend") == "codex" or (
        "backend" not in config and config.get("provider") == "responses"
    ):
        from ..codex.settings import validate_codex

        validate_codex(config)
        return {**config, "backend": "codex"}
    allowed = {"provider", "base_url", "model", "api_key_env", "max_tokens", "timeout"}
    protocol = config.get("provider")
    if protocol == "openai":
        allowed |= {"token_limit_field", "include_usage"}
    if (
        set(config) - allowed
        or not isinstance(protocol, str)
        or protocol not in {"anthropic", "openai"}
    ):
        raise ValueError(
            "Configure provider=anthropic or openai; inline keys and unknown options are rejected"
        )
    key_name = config.get("api_key_env", env_name("API_KEY"))
    if not isinstance(key_name, str) or not key_name.isidentifier():
        raise ValueError("Invalid API credential environment reference")
    if any(
        not isinstance(config.get(name), str) or not config[name].strip()
        for name in ("base_url", "model")
    ):
        raise ValueError("Provider base_url and model must be nonempty strings")
    provider_endpoint(
        config["base_url"], "messages" if protocol == "anthropic" else "chat/completions"
    )
    validate_settings(config["model"], config.get("max_tokens", 4096), config.get("timeout", 30))
    token_field = config.get("token_limit_field", "max_tokens")
    if not isinstance(token_field, str) or token_field not in {
        "max_tokens",
        "max_completion_tokens",
    }:
        raise ValueError("Invalid OpenAI token limit field")
    if type(config.get("include_usage", False)) is not bool:
        raise ValueError("OpenAI include_usage must be a boolean")
    return config


def resolve_provider_config(path=None, *, environment=None, launch_dir=None):
    env = os.environ if environment is None else environment
    selected = provider_config_path(launch_dir, path, env)
    if selected is not None:
        return read_provider_config(selected)
    config = environment_config(env)
    return validate_config(config) if config is not None else None


def provider_from_config(config, *, environment=None):
    require_supported_runtime(environment)
    if config is None:
        return ContextDemoProvider()
    config = validate_config(config)
    if config.get("backend") == "codex":
        from ..codex.settings import CodexSettings

        return CodexSettings(config, environment)
    key_name = config.get("api_key_env", env_name("API_KEY"))
    factory = AnthropicProvider if config["provider"] == "anthropic" else OpenAIProvider
    options = {"max_tokens": config.get("max_tokens", 4096), "timeout": config.get("timeout", 30)}
    if config["provider"] == "openai":
        options.update(
            token_limit_field=config.get("token_limit_field", "max_tokens"),
            include_usage=config.get("include_usage", False),
        )
    return factory(
        config["base_url"],
        (os.environ if environment is None else environment).get(key_name, ""),
        config["model"],
        **options,
    )


def load_provider(path=None, *, environment=None, launch_dir=None):
    config = resolve_provider_config(path, environment=environment, launch_dir=launch_dir)
    return provider_from_config(config, environment=environment)
