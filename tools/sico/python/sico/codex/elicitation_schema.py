"""Bounded public MCP form schemas and typed answers; no arbitrary JSON editor."""

import math
import re
from copy import deepcopy
from datetime import date, datetime
from urllib.parse import urlsplit


def text(value, limit=4000, *, empty=False):
    if (not isinstance(value, str) or len(value) > limit or not empty and not value.strip()
            or any(ord(c) < 32 and c not in "\n\r\t" or ord(c) == 127
                   or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("交互文字缺失、过长或包含无效字符")
    return value


def web_url(value):
    text(value, 8192)
    if any(c.isspace() for c in value) or "\\" in value:
        raise ValueError("网页地址格式无效")
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"https", "http"} or not parts.hostname
                or parts.username is not None or parts.password is not None):
            raise ValueError()
        parts.port
    except ValueError:
        raise ValueError("只支持不含账号密码的 HTTP/HTTPS 网页地址") from None
    return value


def keys(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError("此交互包含尚不支持的表单结构")


def finite(value):
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def options(schema):
    """Return protocol values and display labels without conflating the two."""
    source = schema.get("items", schema)
    if "enum" in source:
        values = source["enum"]
        labels = schema.get("enumNames")
        if labels is None:
            labels = values
        if not isinstance(values, list) or not isinstance(labels, list):
            raise ValueError("表单选项格式无效")
        if len(values) != len(labels):
            raise ValueError("表单选项与名称不匹配")
        rows = list(zip(values, labels))
    else:
        rows = source.get("oneOf", source.get("anyOf", []))
        if not isinstance(rows, list):
            raise ValueError("表单选项格式无效")
        for row in rows:
            keys(row, {"const", "title"})
        rows = [(row.get("const"), row.get("title")) for row in rows]
    if not 1 <= len(rows) <= 32:
        raise ValueError("表单选项数量超出范围")
    rows = [(text(v, 500, empty=True), text(label, 500, empty=True)) for v, label in rows]
    if len({v for v, _ in rows}) != len(rows):
        raise ValueError("表单选项存在重复值")
    return rows


def form_schema(schema):
    keys(schema, {"type", "properties", "required", "$schema"})
    properties = schema.get("properties")
    required = schema.get("required") if schema.get("required") is not None else []
    if (schema.get("type") != "object" or not isinstance(properties, dict)
            or len(properties) > 16 or not isinstance(required, list)
            or any(not isinstance(k, str) or k not in properties for k in required)
            or len(set(required)) != len(required)):
        raise ValueError("表单必须是最多 16 个字段的平面对象")
    if schema.get("$schema") is not None:
        text(schema["$schema"], 500)
    for name, field in properties.items():
        text(name, 200)
        if not isinstance(field, dict):
            raise ValueError("表单字段格式无效")
        kind = field.get("type")
        common = {"type", "title", "description", "default"}
        for key, limit in (("title", 500), ("description", 4000)):
            if field.get(key) is not None:
                text(field[key], limit, empty=True)
        extra = {"string": {"enum", "enumNames", "oneOf", "format", "minLength", "maxLength"},
                 "integer": {"minimum", "maximum"}, "number": {"minimum", "maximum"},
                 "boolean": set(), "array": {"items", "minItems", "maxItems"}}
        if not isinstance(kind, str) or kind not in extra:
            raise ValueError("表单字段类型暂不支持")
        keys(field, common | extra[kind])
        if kind == "array":
            source = field.get("items")
            keys(source, {"type", "enum", "anyOf"})
            if "enum" in source and "anyOf" in source:
                raise ValueError("多选字段选项结构冲突")
            if "enum" in source and source.get("type") != "string":
                raise ValueError("多选字段只支持字符串选项")
            options(field)
        elif kind == "string" and ("enum" in field or "oneOf" in field):
            if "enum" in field and "oneOf" in field:
                raise ValueError("表单选项结构冲突")
            options(field)
        if field.get("format") not in (None, "email", "uri", "date", "date-time"):
            raise ValueError("字符串格式暂不支持")
        for low, high in (("minLength", "maxLength"), ("minItems", "maxItems"),
                          ("minimum", "maximum")):
            for key in (low, high):
                bound = field.get(key)
                if bound is None:
                    continue
                if (not finite(bound)
                        or low != "minimum" and (type(bound) is not int or bound < 0)):
                    raise ValueError("表单范围格式无效")
            if (field.get(low) is not None and field.get(high) is not None
                    and field[low] > field[high]):
                raise ValueError("表单范围上下限冲突")
        if (field.get("minLength", 0) or 0) > 4000 or (field.get("minItems", 0) or 0) > 32:
            raise ValueError("表单要求超过可答复的长度或选项数量")
        if field.get("default") is not None:
            field_value(field, field["default"])
    return deepcopy(schema)


def field_value(field, value):
    kind = field["type"]
    if kind == "boolean":
        valid = type(value) is bool
    elif kind in {"number", "integer"}:
        valid = finite(value)
        valid = valid and (kind != "integer" or value == int(value))
        if valid and kind == "integer":
            value = int(value)
        if valid:
            valid = all(field.get(key) is None or compare(value, field[key])
                        for key, compare in (("minimum", lambda a, b: a >= b),
                                             ("maximum", lambda a, b: a <= b)))
    elif kind == "array":
        valid = isinstance(value, list) and len(value) <= 32
        if valid:
            allowed = {v for v, _ in options(field)}
            valid = (all(isinstance(v, str) and v in allowed for v in value)
                     and len(set(value)) == len(value)
                     and len(value) >= (field.get("minItems") or 0)
                     and len(value) <= (field.get("maxItems") if field.get("maxItems") is not None
                                        else 32))
    else:
        text(value, 4000, empty=True)
        valid = len(value) >= (field.get("minLength") or 0)
        valid = valid and len(value) <= (field.get("maxLength") if field.get("maxLength")
                                        is not None else 4000)
        if "enum" in field or "oneOf" in field:
            valid = valid and value in {v for v, _ in options(field)}
        fmt = field.get("format")
        try:
            if fmt == "email":
                valid = valid and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) is not None
            elif fmt == "uri":
                valid = (valid and bool(urlsplit(value).scheme)
                         and not any(c.isspace() for c in value))
            elif fmt == "date":
                date.fromisoformat(value)
            elif fmt == "date-time":
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                valid = valid and "T" in value and parsed.tzinfo is not None
        except ValueError:
            valid = False
    if not valid:
        raise ValueError("答复不符合字段类型、选项或范围")
    return deepcopy(value)


def response(spec, value):
    keys(value, {"action", "content"})
    action = value.get("action")
    if action not in ("accept", "decline", "cancel"):
        raise ValueError("请选择提交、拒绝或取消")
    content = value.get("content")
    if action != "accept" or spec["mode"] == "url":
        if content is not None:
            raise ValueError("此操作不应附带表单内容")
        return {"action": action}
    schema = spec["schema"]
    if (not isinstance(content, dict) or set(content) - set(schema["properties"])
            or set(schema.get("required") or []) - set(content)):
        raise ValueError("请填写必填字段，且不要添加表单外的字段")
    return {"action": action, "content": {
        name: field_value(schema["properties"][name], entry) for name, entry in content.items()
    }}


def reply_text(row, reply):
    result = reply["response"]
    action = {"accept": "已提交", "decline": "已拒绝", "cancel": "已取消"}[result["action"]]
    lines = [row["title"] + " · " + action]
    fields = row["elicitation"].get("schema", {}).get("properties", {})
    for key, value in (result.get("content") or {}).items():
        field = fields[key]
        if field["type"] == "boolean":
            value = "是" if value else "否"
        elif field["type"] == "array" or "enum" in field or "oneOf" in field:
            labels = dict(options(field))
            value = ("、".join(labels[v] for v in value)
                     if isinstance(value, list) else labels[value])
        lines.append((field.get("title") or key) + "：" + str(value))
    return "\n".join(lines)


def status_text(row):
    return {"prepared": "准备中", "pending": "待答复", "answer_received": "答复已提交，等待交付",
            "dispatching": "正在发送答复", "answered": "答复已发送，服务结果另行核对",
            "unconfirmed": "答复交付未确认，不会自动重发", "invalid": "交互已失效",
            "withdrawn": "执行已结束，此交互不再等待答复"}.get(row["status"], "")
