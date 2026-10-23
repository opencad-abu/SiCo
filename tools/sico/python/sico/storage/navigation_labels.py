"""Bounded, revision-checked storage for the project's one-label-per-session layout."""

from __future__ import annotations

import re

from ..core.contracts import identifier
from . import revisioned_document as document
from .roots import state_root

SCHEMA = "sico.navigation-labels.v1"
MAX_BYTES = 512 * 1024
MAX_LABELS = 128
MAX_LABEL_NAME = 40
MAX_LABEL_MEMBERS = 4096
MAX_LOOSE = 4096
LABEL_ID = re.compile(r"l_[a-f0-9]{32}\Z")


def default_path(project):
    return state_root(project) / "ai/agent-navigation.json"


def empty_layout():
    return {"schema": SCHEMA, "labels": [], "loose_order": []}


def label_name(value):
    if not isinstance(value, str):
        raise ValueError("标签名称必须是文本")
    text = " ".join(value.replace("\0", "").split())
    if not text or len(text) > MAX_LABEL_NAME:
        raise ValueError("标签名称必须为 1–%d 个字符" % MAX_LABEL_NAME)
    return text


def _sessions(value, label, *, used=None):
    if not isinstance(value, list) or len(value) > MAX_LABEL_MEMBERS:
        raise ValueError(label + "会话列表无效或过长")
    result = []
    for value_id in value:
        value_id = identifier(value_id)
        if value_id in result or (used is not None and value_id in used):
            raise ValueError("一个会话只能属于一个标签")
        result.append(value_id)
    return result


def validate_layout(value):
    """Return a normalized copy; never repair an invalid one-label document."""

    if not isinstance(value, dict) or set(value) != {"schema", "labels", "loose_order"}:
        raise ValueError("标签布局字段无效")
    if value["schema"] != SCHEMA:
        raise ValueError("不支持的标签布局版本")
    labels = value["labels"]
    if not isinstance(labels, list) or len(labels) > MAX_LABELS:
        raise ValueError("标签数量无效或过多")
    used = set()
    names = set()
    result = []
    total = 0
    for row in labels:
        if not isinstance(row, dict) or set(row) != {"id", "name", "sessions"}:
            raise ValueError("标签条目字段无效")
        label_id = row["id"]
        if not isinstance(label_id, str) or LABEL_ID.fullmatch(label_id) is None:
            raise ValueError("标签 ID 无效")
        name = label_name(row["name"])
        if name.casefold() in names:
            raise ValueError("标签名称必须唯一")
        names.add(name.casefold())
        sessions = _sessions(row["sessions"], "标签", used=used)
        used.update(sessions)
        total += len(sessions)
        result.append({"id": label_id, "name": name, "sessions": sessions})
    if total > MAX_LABEL_MEMBERS:
        raise ValueError("标签会话总数超过限制")
    loose = _sessions(value["loose_order"], "未归类", used=used)
    if len(loose) > MAX_LOOSE:
        raise ValueError("未归类会话过多")
    return {"schema": SCHEMA, "labels": result, "loose_order": loose}


def read(project):
    path = default_path(project)
    value, revision = document.read(path, max_bytes=MAX_BYTES)
    if value is None:
        return {"path": str(path), "value": empty_layout(), "revision": None}
    try:
        value = validate_layout(value)
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("标签布局不可读或格式无效：" + str(exc)) from None
    return {"path": str(path), "value": value, "revision": revision}


def save(project, value, *, expected_revision):
    value = validate_layout(value)
    path = default_path(project)
    saved, revision = document.save(
        project, path, value,
        expected_revision=expected_revision,
        label="标签布局",
        max_bytes=MAX_BYTES,
    )
    return {"path": str(path), "value": validate_layout(saved), "revision": revision}
