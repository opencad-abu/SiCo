"""Typed v2 extension contracts; activation is gated by schema.require_supported."""

import re
from urllib.parse import urlsplit

from cadai.mcp_server import RESERVED_IDS


def text(value, limit=4096):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 for c in value)):
        raise ValueError("资源字段必须为有界非空文本")
    return value


def fields(row, required, optional=()):
    if not isinstance(row, dict) or set(row) - set(required) - set(optional) or set(required) - set(row):
        raise ValueError("资源扩展条目字段不完整或不支持")


def boolean(value):
    if type(value) is not bool:
        raise ValueError("资源开关必须为布尔值")
    return value


def seconds(value):
    if type(value) not in (int, float) or not 0 < value <= 600:
        raise ValueError("资源超时必须在 0 到 600 秒之间")
    return value


def strings(value, limit=128, *, unique=True):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("资源列表类型或长度无效")
    values = [text(item) for item in value]
    if unique and len(set(values)) != len(values):
        raise ValueError("资源列表包含重复项")
    return values


def credential(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
        raise ValueError("凭据必须是环境变量引用")
    return value


def url(value):
    text(value)
    if any(c.isspace() for c in value):
        raise ValueError("服务 URL 不能包含空白")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None \
            or parsed.password is not None or parsed.fragment or parsed.query:
        raise ValueError("服务 URL 必须使用 HTTP(S)，不能含凭据、查询串或片段")
    try:
        parsed.port
    except ValueError:
        raise ValueError("服务 URL 端口无效") from None
    return value


def validate_extension(kind, row, ids):
    from .schema import NAME, path_value

    if kind == "skill_overrides":
        fields(row, {"path", "enabled"})
        path = path_value(row["path"], resolve=False)
        if not path.endswith("/SKILL.md") or ("skill", path) in ids:
            raise ValueError("技能路径无效或重复")
        ids.add(("skill", path))
        return {"path": path, "enabled": boolean(row["enabled"])}
    common = {"id", "name", "source", "enabled"}
    fields(row, common, {"transport", "command", "args", "cwd", "env_refs", "url",
                        "credential_ref", "startup_timeout_sec", "tool_timeout_sec", "required",
                        "enabled_tools"} if kind == "mcp_servers" else
                       {"type", "path", "formats", "include", "exclude", "update", "adapter",
                        "url", "collection", "credential_ref", "timeout_sec"})
    key = row["id"]
    if not isinstance(key, str) or not NAME.fullmatch(key) or key in ids:
        raise ValueError("外部资源 ID 无效或重复")
    ids.add(key)
    if row["source"] not in ("project", "user"):
        raise ValueError("资源来源无效")
    result = {"id": key, "name": text(row["name"], 128), "source": row["source"],
              "enabled": boolean(row["enabled"])}
    if kind == "mcp_servers":
        if key in RESERVED_IDS:
            raise ValueError("不能覆盖内置 MCP 服务")
        transport = row.get("transport")
        optional = {"startup_timeout_sec", "tool_timeout_sec", "required", "enabled_tools"}
        result["transport"] = transport
        if transport == "stdio":
            fields(row, common | {"transport", "command", "args", "cwd", "env_refs"}, optional)
            result.update(command=text(row["command"]), args=strings(row["args"], unique=False),
                          cwd=path_value(row["cwd"], resolve=False))
            refs = row["env_refs"]
            if not isinstance(refs, dict) or len(refs) > 32:
                raise ValueError("MCP 环境变量引用无效")
            result["env_refs"] = {credential(k): credential(v) for k, v in refs.items()}
        elif transport == "streamable_http":
            fields(row, common | {"transport", "url"}, optional | {"credential_ref"})
            result["url"] = url(row["url"])
        else:
            raise ValueError("不支持此 MCP 连接类型")
        for name in ("startup_timeout_sec", "tool_timeout_sec"):
            if name in row:
                result[name] = seconds(row[name])
        if "required" in row:
            result["required"] = boolean(row["required"])
        if "enabled_tools" in row:
            result["enabled_tools"] = strings(row["enabled_tools"], 512)
    else:
        result["type"] = row.get("type")
        if result["type"] == "local":
            fields(row, common | {"type", "path", "formats", "include", "exclude", "update"})
            formats = strings(row["formats"], 3)
            if not formats or set(formats) - {"text", "markdown", "pdf"} or row["update"] != "manual":
                raise ValueError("本地知识源格式或更新方式不支持")
            result.update(path=path_value(row["path"], resolve=False), formats=formats,
                          include=strings(row["include"]), exclude=strings(row["exclude"]),
                          update="manual")
        elif result["type"] == "remote":
            fields(row, common | {"type", "adapter", "url", "collection", "timeout_sec"},
                   {"credential_ref"})
            adapter = row["adapter"]
            if not isinstance(adapter, str) or not NAME.fullmatch(adapter):
                raise ValueError("远程知识适配器 ID 无效")
            result.update(adapter=adapter, url=url(row["url"]), collection=text(row["collection"]),
                          timeout_sec=seconds(row["timeout_sec"]))
        else:
            raise ValueError("不支持此知识源类型")
    if "credential_ref" in row:
        result["credential_ref"] = credential(row["credential_ref"])
    return result
