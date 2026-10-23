"""Project-level structured data roots with revision-checked storage."""

from __future__ import annotations

from ..storage.roots import state_root
from ..storage import revisioned_document as document
from .schema import path_value

KEYS = ("pdk_data", "circuit_templates")
LABELS = {"pdk_data": "工艺数据", "circuit_templates": "模板数据"}

DataSourcesConflictError = document.DocumentConflictError
DataSourcesSaveUncertainError = document.DocumentSaveUncertainError


def default_path(project):
    return state_root(project) / "ai/data-sources.json"


def _path(value):
    try:
        return path_value(value, resolve=False)
    except ValueError as exc:
        raise ValueError("数据源路径无效：" + str(exc)) from None


def _validate(value):
    if (not isinstance(value, dict) or set(value) - {"version", *KEYS}
            or value.get("version") != 1):
        raise ValueError("数据源设置文件格式无效")
    result = {}
    for key in KEYS:
        if key in value:
            result[key] = _path(value[key])
    return result


def read(project):
    path = default_path(project)
    value, revision = document.read(path)
    if value is None:
        settings = {}
    else:
        try:
            settings = _validate(value)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("数据源设置不可读或格式无效：" + str(exc)) from None
    return {"path": str(path), "revision": revision,
            "editable": path.parents[1].name == ".sico", **settings}


def save(project, pdk_data, circuit_templates, *, expected_revision):
    payload = {"version": 1}
    for key, value in (("pdk_data", pdk_data),
                       ("circuit_templates", circuit_templates)):
        if value is not None:
            payload[key] = _path(value)
    path = default_path(project)
    value, revision = document.save(
        project, path, payload, expected_revision=expected_revision,
        label="数据源设置",
    )
    settings = _validate(value)
    return {"path": str(path), "revision": revision,
            "editable": path.parents[1].name == ".sico", **settings}
