"""Three-level JSON workspace configuration and strict template locks.

The loader intentionally accepts JSON only.  YAML source templates can be
normalized at build time, but production startup never downloads a parser or
executes configuration code.  User, project, and lock files are independent;
an absent optional file yields an immutable empty config while a malformed
present file fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from ..artifact_paths import (
    PathContractError,
    path_has_any_symlink_component,
    validate_relative_path,
)
from ..agent.protocol import ErrorCode, ProtocolError


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_MAX_CONFIG_BYTES = 1024 * 1024


def _unknown_fields(value: Mapping[Any, Any], allowed: set[str]) -> list[str]:
    """Return deterministic field names without comparing mixed key types."""
    return sorted(str(key) for key in value if not isinstance(key, str) or key not in allowed)


@dataclass(frozen=True)
class UserConfig:
    path: Path
    values: Mapping[str, Any] = field(default_factory=dict)

    @property
    def provider(self) -> str | None:
        value = self.values.get("provider")
        return value if isinstance(value, str) else None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    values: Mapping[str, Any] = field(default_factory=dict)

    @property
    def project_id(self) -> str | None:
        value = self.values.get("project_id", self.values.get("id"))
        return value if isinstance(value, str) else None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)


@dataclass(frozen=True)
class TemplateLock:
    path: Path
    schema: int
    repo_id: str
    remote: str | None
    commit: str
    tree_digest: str | None
    templates: tuple[Mapping[str, Any], ...]
    sha256: str
    values: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)

    def matches(self, *, commit: str | None = None, tree_digest: str | None = None, template_digest: str | None = None) -> bool:
        if commit is not None and commit != self.commit:
            return False
        if tree_digest is not None and tree_digest != self.tree_digest:
            return False
        if template_digest is not None:
            return any(item.get("digest") == template_digest for item in self.templates)
        return True


@dataclass(frozen=True)
class WorkspaceConfig:
    cwd: Path
    user: UserConfig
    project: ProjectConfig
    template_lock: TemplateLock | None
    user_path: Path
    project_path: Path
    lock_path: Path

    @property
    def merged(self) -> dict[str, Any]:
        merged = dict(self.user.values)
        merged.update(dict(self.project.values))
        if self.template_lock is not None:
            merged["template_lock"] = self.template_lock.to_dict()
        return merged


def load_user_config(path: str | Path | None = None, *, home: str | Path | None = None) -> UserConfig:
    target = Path(path).expanduser() if path is not None else Path(home or os.path.expanduser("~")) / ".aivw" / "config.json"
    if path_has_any_symlink_component(target):
        raise ProtocolError(ErrorCode.PATH_DENIED, "user config path traverses a symlink", {"path": str(target)})
    values = _load_optional_object(target, "user config", {"schema", "provider", "provider_config", "cache", "credentials", "limits", "telemetry"})
    if "credentials" in values:
        _validate_credential_refs(values["credentials"])
    return UserConfig(target.resolve(strict=False), values)


def load_project_config(path: str | Path | None = None, *, cwd: str | Path | None = None) -> ProjectConfig:
    target = Path(path).expanduser() if path is not None else Path(cwd or Path.cwd()) / ".aivw" / "project.json"
    if path_has_any_symlink_component(target):
        raise ProtocolError(ErrorCode.PATH_DENIED, "project config path traverses a symlink", {"path": str(target)})
    values = _load_optional_object(target, "project config", {"schema", "project_id", "id", "name", "profile", "recipe", "source_root", "payload_root", "environment", "provider", "template_lock"})
    _validate_project_values(values)
    return ProjectConfig(target.resolve(strict=False), values)


def load_template_lock(path: str | Path) -> TemplateLock:
    raw_target = Path(path).expanduser()
    if path_has_any_symlink_component(raw_target):
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock path traverses a symlink", {"path": str(raw_target)})
    target = raw_target.resolve(strict=False)
    if not target.is_file() or target.is_symlink():
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock file is unavailable", {"path": str(target)})
    try:
        value = json.loads(
            target.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_object_names,
        )
    except (OSError, ValueError) as exc:
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock is not valid JSON", {"detail": str(exc)}) from exc
    if not isinstance(value, Mapping):
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock root must be an object")
    allowed = {"schema", "repo", "templates"}
    unknown = _unknown_fields(value, allowed)
    if unknown:
        raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "template lock contains unknown fields", {"fields": unknown})
    schema = value.get("schema")
    if schema != 1:
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "unsupported template lock schema")
    repo = value.get("repo")
    if not isinstance(repo, Mapping):
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock repo must be an object")
    repo_unknown = _unknown_fields(repo, {"id", "remote", "commit", "digest", "tree_digest"})
    if repo_unknown:
        raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "template lock repo contains unknown fields", {"fields": repo_unknown})
    repo_id = repo.get("id")
    commit = repo.get("commit")
    if not isinstance(repo_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", repo_id):
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock repo.id is invalid")
    if not isinstance(commit, str) or not _HEX40.fullmatch(commit):
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock repo.commit must be a full 40-hex commit")
    tree_digest = repo.get("tree_digest", repo.get("digest"))
    if tree_digest is not None and (not isinstance(tree_digest, str) or not _DIGEST.fullmatch(tree_digest)):
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock tree digest is invalid")
    remote = repo.get("remote")
    if remote is not None and (not isinstance(remote, str) or not remote.strip()):
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock repo.remote is invalid")
    raw_templates = value.get("templates")
    if not isinstance(raw_templates, list) or not raw_templates:
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock templates must be a non-empty array")
    templates: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_templates):
        if not isinstance(raw, Mapping):
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock entry %d is not an object" % index)
        unknown_template = _unknown_fields(raw, {"id", "version", "path", "digest", "pdk_adapters"})
        if unknown_template:
            raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "template lock entry has unknown fields", {"fields": unknown_template})
        identifier = raw.get("id")
        version = raw.get("version")
        digest = raw.get("digest")
        path_value = raw.get("path")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock template id is invalid or duplicated")
        if not isinstance(version, str) or not version:
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock template version is invalid")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock template digest is invalid")
        try:
            validate_relative_path(path_value, "template lock template")
        except PathContractError:
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock template path is unsafe")
        adapters = raw.get("pdk_adapters", [])
        if not isinstance(adapters, list) or any(not isinstance(item, str) or not item for item in adapters):
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "template lock pdk_adapters is invalid")
        seen.add(identifier)
        templates.append(dict(raw))
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return TemplateLock(target, 1, repo_id, remote, commit, tree_digest, tuple(templates), hashlib.sha256(canonical.encode("utf-8")).hexdigest(), dict(value))


def resolve_workspace_config(
    cwd: str | Path | None = None,
    *,
    home: str | Path | None = None,
    require_template_lock: bool = False,
) -> WorkspaceConfig:
    working = Path(cwd or Path.cwd()).expanduser().resolve()
    if not working.is_dir():
        raise ProtocolError(ErrorCode.PATH_DENIED, "workspace cwd is not a directory", {"path": str(working)})
    user_path = Path(home or os.path.expanduser("~")) / ".aivw" / "config.json"
    project_path = working / ".aivw" / "project.json"
    lock_path = working / ".aivw" / "template.lock.json"
    user = load_user_config(user_path)
    project = load_project_config(project_path)
    lock = None
    if os.path.lexists(str(lock_path)):
        if path_has_any_symlink_component(lock_path):
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "workspace template lock path traverses a symlink", {"path": str(lock_path)})
        lock = load_template_lock(lock_path)
    elif require_template_lock:
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "workspace template lock is missing", {"path": str(lock_path)})
    return WorkspaceConfig(working, user, project, lock, user_path.resolve(strict=False), project_path.resolve(strict=False), lock_path.resolve(strict=False))


def _load_optional_object(path: Path, label: str, allowed: set[str]) -> dict[str, Any]:
    # ``exists()`` is false for a dangling symlink; inspect ``lexists`` first
    # so an attacker cannot turn a present configuration link into an absent
    # optional file and silently fall back to defaults.
    if not os.path.lexists(str(path)):
        return {}
    if path_has_any_symlink_component(path) or path.is_symlink() or not path.is_file():
        raise ProtocolError(ErrorCode.PATH_DENIED, "%s is not a regular file" % label, {"path": str(path)})
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ProtocolError(ErrorCode.CONTEXT_LIMIT_EXCEEDED, "%s exceeds size cap" % label)
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_object_names,
        )
    except ProtocolError:
        raise
    except (OSError, ValueError) as exc:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s is not valid JSON" % label, {"detail": str(exc)}) from exc
    if not isinstance(value, Mapping):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s root must be an object" % label)
    unknown = _unknown_fields(value, allowed)
    if unknown:
        raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "%s contains unknown fields" % label, {"fields": unknown})
    schema = value.get("schema", 1)
    if schema != 1:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s schema is unsupported" % label)
    _ensure_json(value)
    return dict(value)


def _validate_credential_refs(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ProtocolError(ErrorCode.ENVIRONMENT_DENIED, "credentials must be an object of references")
    for name, ref in value.items():
        if not isinstance(name, str) or not isinstance(ref, str) or not ref.startswith("secret://") or len(ref) <= len("secret://"):
            raise ProtocolError(ErrorCode.ENVIRONMENT_DENIED, "credentials must use secret:// references", {"name": name})


def _validate_project_values(value: Mapping[str, Any]) -> None:
    environment = value.get("environment")
    if environment is not None:
        if not isinstance(environment, Mapping):
            raise ProtocolError(ErrorCode.ENVIRONMENT_DENIED, "project environment must be an object")
        for name, env_value in environment.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                raise ProtocolError(ErrorCode.ENVIRONMENT_DENIED, "project environment name is invalid", {"name": name})
            if re.search(r"TOKEN|KEY|SECRET|PASSWORD|AUTH|CREDENTIAL", name, re.IGNORECASE):
                raise ProtocolError(ErrorCode.ENVIRONMENT_DENIED, "secret environment names are forbidden", {"name": name})
            if not isinstance(env_value, str) or "\x00" in env_value:
                raise ProtocolError(ErrorCode.ENVIRONMENT_DENIED, "project environment value is invalid", {"name": name})


def _ensure_json(value: object) -> None:
    try:
        json.dumps(value, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "configuration contains non-JSON data", {"detail": str(exc)}) from exc


class _DuplicateObjectName(ValueError):
    pass


def _reject_duplicate_object_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise _DuplicateObjectName("duplicate configuration field: %s" % key)
        result[key] = item
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


__all__ = [
    "ProjectConfig",
    "TemplateLock",
    "UserConfig",
    "WorkspaceConfig",
    "load_project_config",
    "load_template_lock",
    "load_user_config",
    "resolve_workspace_config",
]
