"""Capture only selected knowledge credentials; never forward model or bridge keys."""

from .contracts import KnowledgeError

FORBIDDEN = {"SICO_API_KEY", "SICO_MODEL_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SICO_MCP_TOKEN"}


def capture(sources, environment, model_key="SICO_API_KEY"):
    result, refs = {}, set()
    keys = FORBIDDEN | {model_key}
    secrets = {environment.get(key) for key in keys} - {None, ""}
    for row in sources:
        ref = row.get("credential_ref")
        if not ref:
            continue
        refs.add(ref)
        value = environment.get(ref)
        if ref in keys or ref.startswith(("SICO_RESOURCE_", "SICO_KNOWLEDGE_")) or value and value in secrets:
            raise KnowledgeError("invalid_credential", "知识源不允许引用模型或内置桥凭据")
        if row["enabled"] and value:
            if not isinstance(value, str) or len(value) > 16384 or any(c in value for c in "\r\n\x00"):
                raise KnowledgeError("invalid_credential", "知识源凭据格式无效")
            result[ref] = value
    return result, refs


def token(source, environment):
    name = source.get("credential_ref")
    value = environment.get(name, "") if name else ""
    if name and not value:
        raise KnowledgeError("auth_required", "知识源凭据引用未提供，请在工程服务环境中配置")
    return value
