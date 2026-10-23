"""Validate finite provider configuration without loading provider implementations."""

from cadai.env_names import name as env_name
from cadai.provider_settings import provider_endpoint, validate_settings


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Provider configuration must be a JSON object")
    if config.get("backend") == "codex" or (
        "backend" not in config and config.get("provider") == "responses"
    ):
        from .codex import validate_codex

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
