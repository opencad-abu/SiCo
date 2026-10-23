"""Project-owned single-category labels for the session navigation."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path

from ..core.contracts import identifier
from ..storage import navigation_labels as storage
from ..storage.revisioned_document import DocumentConflictError
from ..transport.framing import ProtocolError
from .service_protocol import exact_fields


def _label(layout, label_id):
    return next((row for row in layout["labels"] if row["id"] == label_id), None)


def _same_name(layout, name):
    folded = name.casefold()
    return next((row for row in layout["labels"] if row["name"].casefold() == folded), None)


def _session_ids(value):
    if not isinstance(value, (list, tuple)) or not value or len(value) > 256:
        raise ValueError("一次最多移动 256 个会话")
    result = []
    for row in value:
        row = identifier(row)
        if row in result:
            raise ValueError("移动列表中有重复会话")
        result.append(row)
    return result


class SessionLabels:
    """The sole writer for label names, membership and display order."""

    def __init__(self, project):
        self.project = Path(project)
        self._changed = None

    def observe(self, callback):
        self._changed = callback

    def snapshot(self):
        try:
            state = storage.read(self.project)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {
                "revision": None,
                "labels": [],
                "loose_order": [],
                "error": str(exc),
            }
        value = state["value"]
        return {
            "revision": state["revision"],
            "labels": copy.deepcopy(value["labels"]),
            "loose_order": list(value["loose_order"]),
            "error": "",
        }

    def _strict(self):
        state = storage.read(self.project)
        return state["value"], state["revision"]

    def _save(self, value, revision):
        saved = storage.save(self.project, value, expected_revision=revision)
        result = {
            "revision": saved["revision"],
            "labels": copy.deepcopy(saved["value"]["labels"]),
            "loose_order": list(saved["value"]["loose_order"]),
            "error": "",
        }
        if self._changed is not None:
            try:
                self._changed(result)
            except Exception:
                pass
        return result

    @staticmethod
    def _check_revision(current, expected):
        if expected != current:
            raise DocumentConflictError("标签布局已被修改，请刷新后重试")

    def create(self, name, *, expected_revision=None):
        value, revision = self._strict()
        name = storage.label_name(name)
        existing = _same_name(value, name)
        if existing is not None:
            return self.snapshot()
        self._check_revision(revision, expected_revision)
        value["labels"].append({
            "id": "l_" + uuid.uuid4().hex,
            "name": name,
            "sessions": [],
        })
        return self._save(value, revision)

    def rename(self, label_id, name, *, expected_revision):
        value, revision = self._strict()
        self._check_revision(revision, expected_revision)
        row = _label(value, label_id)
        if row is None:
            raise ValueError("标签不存在")
        name = storage.label_name(name)
        existing = _same_name(value, name)
        if existing is not None and existing["id"] != label_id:
            raise ValueError("标签名称必须唯一")
        if row["name"] == name:
            return self.snapshot()
        row["name"] = name
        return self._save(value, revision)

    def delete(self, label_id, *, expected_revision):
        value, revision = self._strict()
        self._check_revision(revision, expected_revision)
        row = _label(value, label_id)
        if row is None:
            raise ValueError("标签不存在")
        value["labels"] = [item for item in value["labels"] if item["id"] != label_id]
        value["loose_order"] = list(row["sessions"]) + [
            item for item in value["loose_order"] if item not in row["sessions"]
        ]
        return self._save(value, revision)

    def move_sessions(self, session_ids, label_id, before_id, *, expected_revision):
        value, revision = self._strict()
        self._check_revision(revision, expected_revision)
        original = copy.deepcopy(value)
        moving = _session_ids(session_ids)
        if before_id is not None:
            before_id = identifier(before_id)
            if before_id in moving:
                raise ValueError("拖放目标不能是正在移动的会话")
        if label_id is not None:
            label_id = identifier(label_id)
            if _label(value, label_id) is None:
                raise ValueError("标签不存在")

        for row in value["labels"]:
            row["sessions"] = [item for item in row["sessions"] if item not in moving]
        value["loose_order"] = [
            item for item in value["loose_order"] if item not in moving
        ]
        target = value["loose_order"] if label_id is None else _label(value, label_id)["sessions"]
        if before_id is None:
            insert_at = len(target)
        else:
            try:
                insert_at = target.index(before_id)
            except ValueError:
                raise ValueError("拖放目标已变化，请刷新后重试") from None
        target[insert_at:insert_at] = moving
        if value == original:
            return self.snapshot()
        return self._save(value, revision)

    def reorder_labels(self, label_ids, *, expected_revision):
        value, revision = self._strict()
        self._check_revision(revision, expected_revision)
        if not isinstance(label_ids, (list, tuple)):
            raise ValueError("标签顺序无效")
        if len(label_ids) != len(value["labels"]):
            raise ValueError("标签顺序必须包含全部标签")
        by_id = {row["id"]: row for row in value["labels"]}
        ordered = []
        for label_id in label_ids:
            label_id = identifier(label_id)
            if label_id not in by_id or any(row["id"] == label_id for row in ordered):
                raise ValueError("标签顺序无效")
            ordered.append(by_id[label_id])
        if ordered == value["labels"]:
            return self.snapshot()
        value["labels"] = ordered
        return self._save(value, revision)

    def command(self, params):
        if not isinstance(params, dict) or not isinstance(params.get("action"), str):
            raise ProtocolError("Invalid label command")
        action = params["action"]
        expected = params.get("expected_revision")
        if action == "create_label":
            fields = {"action", "expected_revision", "name"}
            exact_fields(params, fields)
            return self.create(params["name"], expected_revision=expected)
        if action == "rename_label":
            fields = {"action", "expected_revision", "label_id", "name"}
            exact_fields(params, fields)
            return self.rename(params["label_id"], params["name"],
                               expected_revision=expected)
        if action == "delete_label":
            fields = {"action", "expected_revision", "label_id"}
            exact_fields(params, fields)
            return self.delete(params["label_id"], expected_revision=expected)
        if action == "move_sessions":
            fields = {"action", "expected_revision", "session_ids", "label_id", "before_id"}
            exact_fields(params, fields)
            return self.move_sessions(params["session_ids"], params["label_id"],
                                      params["before_id"], expected_revision=expected)
        if action == "reorder_labels":
            fields = {"action", "expected_revision", "label_ids"}
            exact_fields(params, fields)
            return self.reorder_labels(params["label_ids"], expected_revision=expected)
        raise ValueError("不支持的标签操作")
