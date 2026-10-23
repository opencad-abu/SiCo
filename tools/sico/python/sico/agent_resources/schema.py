"""Validate the versioned external-resource manifest without starting a runtime."""

from pathlib import Path
import re
import os

from .schema_extensions import validate_extension

KINDS = ("skill_roots", "plugins", "capability_roots")
EXTENSIONS = ("mcp_servers", "knowledge_sources", "skill_overrides")
MAX_MANIFEST = 65536
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


def path_value(value, *, resolve=True):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) < 32 for c in value) or not Path(value).is_absolute()):
        raise ValueError("外部资源路径必须是绝对路径")
    return str(Path(value).resolve()) if resolve else os.path.normpath(value)


def validate_manifest(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        raise ValueError("外部资源清单字段无效")
    version = value["version"]
    if version not in (1, 2):
        raise ValueError("不支持此外部资源清单版本")
    kinds = KINDS if version == 1 else (*KINDS, *EXTENSIONS)
    if set(value) - {"version", *kinds}:
        raise ValueError("外部资源清单字段无效")
    result, ids = {}, set()
    for kind in kinds:
        rows = value.get(kind, [])
        limit = 512 if kind == "skill_overrides" else 32
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError(f"资源 {kind} 必须是列表且最多 {limit} 项")
        result[kind] = []
        for row in rows:
            if kind in EXTENSIONS:
                result[kind].append(validate_extension(kind, row, ids))
                continue
            fields = {"id", "path", "source", "access"}
            if version == 2:
                fields.add("enabled")
            if kind == "plugins":
                fields |= {"name"}
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError("外部资源条目字段不完整或不支持")
            if version == 2 and type(row["enabled"]) is not bool:
                raise ValueError("资源 enabled 必须为布尔值")
            key = row["id"]
            if not isinstance(key, str) or not NAME.fullmatch(key) or key in ids:
                raise ValueError("外部资源 ID 无效或重复")
            ids.add(key)
            access = "read" if kind == "skill_roots" else "execute"
            if row["source"] not in ("user", "project") or row["access"] != access:
                raise ValueError("外部资源来源或使用范围无效")
            if kind == "plugins" and (
                not isinstance(row["name"], str) or not NAME.fullmatch(row["name"])
            ):
                raise ValueError("插件名称无效")
            result[kind].append({**row, "path": path_value(row["path"], resolve=version == 1)})
    return result


def normalize_manifest(value):
    selection = validate_manifest(value)
    return {"version": value["version"], **selection}


def upgrade_manifest(value):
    """Explicit, lossless v1→v2 projection; never writes the source document."""
    selection = validate_manifest(value)
    if value["version"] == 1:
        selection = {kind: [{**row, "enabled": True} for row in selection[kind]] for kind in KINDS}
        selection.update({kind: [] for kind in EXTENSIONS})
    return {"version": 2, **selection}


def require_supported(value):
    """Recognized future configuration must never be silently accepted at runtime."""
    for row in value.get("knowledge_sources", []):
        if row["type"] != "remote":
            continue
        if row["adapter"] == "openai_docs_mcp":
            if row["collection"] not in {"all", "codex", "api"}:
                raise ValueError("openai_docs_mcp 只支持 all、codex、api 集合")
        elif row["adapter"] != "jedai_aai_mcp":
            raise ValueError("尚未接通此远程知识适配器或集合；"
                             "当前支持 openai_docs_mcp 与 jedai_aai_mcp")
