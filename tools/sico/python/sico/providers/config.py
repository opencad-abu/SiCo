"""Provider construction and selection, with a compatibility validation export.

The Python HTTP backend (SICO_BACKEND=python, anthropic/openai protocols) has been
retired. Only the Codex provider path remains.
"""

from __future__ import annotations

import os
from pathlib import Path

from cadai.codex_selection import codex_tool_format, require_supported_runtime
from cadai.env_names import name as env_name
from cadai.env_names import value as env_value

from ..transport.framing import strict_json
from .mock import ContextDemoProvider
# Compat: retire after supported imports use configuration.provider.
from ..configuration.provider import validate_config
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
    if backend != "codex":
        raise ValueError("SICO_BACKEND must be codex; the python backend has been retired")
    protocol = str(env_value(environment, "PROVIDER", "")).strip().lower() or "responses"
    if protocol != "responses":
        raise ValueError("SICO_PROVIDER must be responses; python HTTP providers have been retired")
    if not url and not model:
        return None
    if not url or not model:
        raise ValueError("SICO_API_BASE_URL (or SICO_API_URL) and SICO_MODEL must be set together")
    config = {
        "provider": protocol,
        "base_url": url,
        "model": model,
        "api_key_env": credential_reference(environment),
        "backend": "codex",
        "tool_format": codex_tool_format(environment),
        "linux_sandbox": str(env_value(environment, "LINUX_SANDBOX", "default")),
    }
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
    if config.get("backend") != "codex":
        raise ValueError(
            "The Python HTTP backend has been retired; "
            "only Codex providers are supported (backend=codex)"
        )
    from ..codex.settings import CodexSettings

    return CodexSettings(config, environment)


def load_provider(path=None, *, environment=None, launch_dir=None):
    config = resolve_provider_config(path, environment=environment, launch_dir=launch_dir)
    return provider_from_config(config, environment=environment)
