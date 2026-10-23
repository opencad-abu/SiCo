"""Project-level model backend and protocol with revision-checked storage."""

from __future__ import annotations

from ..storage.roots import state_root
from ..storage import revisioned_document as document

DEFAULT_BACKEND = "codex"
DEFAULT_PROVIDER = "responses"
BACKENDS = {
    "codex": frozenset({"responses"}),
    "python": frozenset({"anthropic", "openai"}),
}

ModelConflictError = document.DocumentConflictError
ModelSaveUncertainError = document.DocumentSaveUncertainError


def default_path(project):
    return state_root(project) / "ai/agent-model.json"


def _validate(value):
    if (not isinstance(value, dict) or set(value) != {"version", "backend", "provider"}
            or value["version"] != 1):
        raise ValueError("模型设置文件格式无效")
    backend, provider = value["backend"], value["provider"]
    if backend not in BACKENDS or provider not in BACKENDS[backend]:
        raise ValueError("模型后端与协议不匹配")
    return backend, provider


def read(project):
    path = default_path(project)
    value, revision = document.read(path)
    if value is None:
        backend, provider = DEFAULT_BACKEND, DEFAULT_PROVIDER
    else:
        try:
            backend, provider = _validate(value)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("模型设置不可读或格式无效：" + str(exc)) from None
    return {"path": str(path), "backend": backend, "provider": provider,
            "revision": revision, "editable": path.parents[1].name == ".sico"}


def save(project, backend, provider, *, expected_revision):
    if backend not in BACKENDS:
        raise ValueError("模型后端必须是 codex 或 python")
    if provider not in BACKENDS[backend]:
        raise ValueError("模型协议必须与后端匹配")
    path = default_path(project)
    value, revision = document.save(
        project, path, {"version": 1, "backend": backend, "provider": provider},
        expected_revision=expected_revision, label="模型设置",
    )
    backend, provider = _validate(value)
    return {"path": str(path), "backend": backend, "provider": provider,
            "revision": revision, "editable": path.parents[1].name == ".sico"}
