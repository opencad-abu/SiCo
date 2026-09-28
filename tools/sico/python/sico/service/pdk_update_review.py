"""Exact PDK differences rendered from verified tool evidence for every audit view."""

import json
import re

from cadai.skill_diagnostics import FIELDS


def semantic_preview(data):
    """Exclude top-level transport diagnostics, preserving every reviewed field."""
    return {key: value for key, value in data.items() if key not in FIELDS}


def preview(index, row):
    index.verify_evidence(row["evidence"])
    for ref in row["evidence"]:
        data = index.evidence_value(ref["id"]).get("data", {})
        if (
            data.get("update_ref") == row["pdk_update_ref"]
            and data.get("revision") == row["pdk_update_revision"]
        ):
            return data
    raise ValueError("PDK review no longer matches its original evidence")


def cell(value):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>~-])", r"\\\1", text)


def markdown(index, row):
    if not row.get("pdk_update_ref"):
        return ""
    data = preview(index, row)
    parts = ["### PDK 数据变更\n\n基于修订：" + cell(data["revision"])]
    for group in data["changes"]:
        lines = [
            "### " + cell(group["file"]),
            "",
            "| 字段 | 更新前 | 更新后 |",
            "| --- | --- | --- |",
        ]
        for change in group["changes"]:
            before = cell(change["before"]) if "before" in change else "（不存在）"
            after = "（删除）" if change.get("removed") else cell(change["after"])
            lines.append(f"| {cell(change['path'])} | {before} | {after} |")
        parts.append("\n".join(lines))
    mapping = data.get("mapping")
    if isinstance(mapping, dict):
        lines = ["### 手册与 CDF 参数对照", "", "| 对照项 | 内容 |", "| --- | --- |"]
        for key, value in mapping.items():
            if key == "entries":
                for entry in value:
                    for field, content in entry.items():
                        lines.append(
                            f"| {cell(entry['entry_id'] + '.' + field)} | {cell(content)} |"
                        )
            else:
                lines.append(f"| {cell(key)} | {cell(value)} |")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def project(index, rows, fields):
    result = {}
    for key, row in rows.items():
        value = {name: row[name] for name in fields if name in row}
        if row.get("pdk_update_ref"):
            value["review_markdown"] = markdown(index, row)
        result[key] = value
    return result
