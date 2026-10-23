"""Exact PDK differences rendered from verified tool evidence for every audit view."""

import json
import re

from cadai.skill_diagnostics import FIELDS

REVIEW_PAGE_BYTES = 16 * 1024


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
    return "\n\n".join(page["markdown"] for page in pages(index, row)["pages"])


def _groups(data):
    groups = []
    for group in data["changes"]:
        rows = []
        for change in group["changes"]:
            before = cell(change["before"]) if "before" in change else "（不存在）"
            after = "（删除）" if change.get("removed") else cell(change["after"])
            rows.append(f"| {cell(change['path'])} | {before} | {after} |")
        groups.append((group["file"], ["| 字段 | 更新前 | 更新后 |", "| --- | --- | --- |"],
                       rows))
    mapping = data.get("mapping")
    if isinstance(mapping, dict):
        rows = []
        for key, value in mapping.items():
            if key == "entries":
                for entry in value:
                    for field, content in entry.items():
                        rows.append(
                            f"| {cell(entry['entry_id'] + '.' + field)} | {cell(content)} |"
                        )
            else:
                rows.append(f"| {cell(key)} | {cell(value)} |")
        groups.append((
            "手册与 CDF 参数对照",
            ["| 对照项 | 内容 |", "| --- | --- |"],
            rows,
        ))
    return groups


def pages(index, row, *, page_bytes=REVIEW_PAGE_BYTES):
    """Build deterministic structured pages from one verified update preview."""
    if not row.get("pdk_update_ref"):
        return {"page_count": 0, "pages": [], "files": 0, "fields": 0,
                "total_bytes": 0}
    data = preview(index, row)
    if type(page_bytes) is not int or page_bytes < 256:
        raise ValueError("PDK review page size must be at least 256 bytes")
    raw = []
    for file, header, rows in _groups(data):
        current = []
        for row_text in rows:
            candidate = [*current, row_text]
            body = "\n".join([header[0], header[1], *candidate])
            if current and len(body.encode("utf-8")) > page_bytes - 256:
                raw.append((file, header, current))
                current = []
            current.append(row_text)
        if current:
            raw.append((file, header, current))
    total = len(raw)
    result = []
    for number, (file, header, rows) in enumerate(raw, 1):
        prefix = (
            f"### PDK 数据变更（第 {number}/{total} 页）\n\n"
            f"基于修订：{cell(data['revision'])}\n\n"
            f"### {cell(file)}\n\n"
        )
        result.append({
            "page": number,
            "page_count": total,
            "files": [file],
            "fields": len(rows),
            "markdown": prefix + "\n".join([*header, *rows]),
            "complete": True,
        })
    fields = sum(len(group["changes"]) for group in data["changes"])
    if isinstance(data.get("mapping"), dict):
        mapping = data["mapping"]
        fields += sum(len(entry) for entry in mapping.get("entries", []))
        fields += len(mapping) - (1 if "entries" in mapping else 0)
    return {
        "update_ref": row["pdk_update_ref"],
        "revision": data["revision"],
        "page_count": len(result),
        "page_bytes": page_bytes,
        "files": len(data["changes"]),
        "fields": fields,
        "total_bytes": len(json.dumps(data, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8")),
        "pages": result,
    }


def project(index, rows, fields):
    result = {}
    for key, row in rows.items():
        value = {name: row[name] for name in fields if name in row}
        if row.get("pdk_update_ref"):
            value["review_markdown"] = markdown(index, row)
        result[key] = value
    return result
