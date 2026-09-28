"""Reuse credentials only from an already configured matching provider endpoint."""

from .published import thaw
from ..providers.config import validate_config
from cadai.provider_settings import provider_endpoint


def recovery_environment(owner, session_id, snapshot):
    config = snapshot["provider_config"]
    environment = dict(snapshot["environment"])
    if config is None:
        return environment
    credential = config.get("api_key_env", "SICO_API_KEY")
    sources = list(owner.creation.dependencies(session_id))
    sources.extend(owner.dependencies_for(key) for key in owner.controllers)
    def candidates():
        for source in sources:
            yield thaw(source.provider_config), source.environment
        current = owner._capture_environment(None)
        yield thaw(owner._resolve_config(None, current)), current

    identity = _identity(config)
    for selected, values in candidates():
        if (selected is not None and _identity(selected) == identity
                and values.get(credential)):
            environment[credential] = values[credential]
            return environment
    raise ValueError("当前模型连接不可用，请检查应用的模型连接")


def _identity(config):
    config = validate_config(config)
    protocol = config["provider"]
    resource = {"responses": "responses", "anthropic": "messages",
                "openai": "chat/completions"}[protocol]
    return (config.get("backend"), protocol, provider_endpoint(config["base_url"], resource),
            config.get("api_key_env", "SICO_API_KEY"))
