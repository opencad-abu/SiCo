"""Fail-closed tool, path, environment, and network policy."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from ..artifact_paths import path_has_any_symlink_component
from .context import ArtifactLocator
from .protocol import Action, ErrorCode, ProtocolError


_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_NAME = re.compile(r"(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str = ""
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def require(self) -> None:
        if not self.allowed:
            raise ProtocolError(self.code or ErrorCode.INVALID_ARGUMENTS, self.reason or "policy denied", self.details)


@dataclass(frozen=True)
class AgentPolicy:
    """Explicit allowlists for the runtime.

    Empty mutation/path/tool allowlists mean deny-all.  This makes a missing
    profile fail closed instead of silently granting process or filesystem
    authority.
    """

    allowed_tools: frozenset[str] = frozenset()
    read_roots: tuple[Path, ...] = ()
    write_roots: tuple[Path, ...] = ()
    allowed_environment: frozenset[str] = frozenset()
    allow_network: bool = False
    allow_process: bool = False
    max_artifact_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_artifact_bytes, int)
            or isinstance(self.max_artifact_bytes, bool)
            or self.max_artifact_bytes <= 0
        ):
            raise ValueError("max_artifact_bytes must be a positive integer")
        if not isinstance(self.allowed_tools, (set, frozenset, tuple, list)):
            raise ValueError("allowed_tools must be a set of names")
        normalized_tools = set()
        for raw_tool in self.allowed_tools:
            if (
                not isinstance(raw_tool, str)
                or not raw_tool
                or raw_tool != raw_tool.strip()
                or any(ch.isspace() for ch in raw_tool)
            ):
                raise ValueError("invalid tool allowlist entry: %r" % (raw_tool,))
            normalized_tools.add(raw_tool)
        object.__setattr__(self, "allowed_tools", frozenset(normalized_tools))
        if not isinstance(self.allowed_environment, (set, frozenset, tuple, list)):
            raise ValueError("allowed_environment must be a set of names")
        if not isinstance(self.allow_network, bool) or not isinstance(self.allow_process, bool):
            raise ValueError("policy capability flags must be boolean")
        try:
            normalized_environment = frozenset(self.allowed_environment)
        except (TypeError, ValueError) as exc:
            raise ValueError("allowed_environment must be a set of names") from exc
        object.__setattr__(self, "allowed_environment", normalized_environment)
        for name in self.allowed_environment:
            if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
                raise ValueError("invalid environment allowlist entry: %r" % (name,))
            if _SECRET_NAME.search(name):
                raise ValueError("secret-bearing environment names cannot be allowlisted")
        if isinstance(self.read_roots, (str, bytes)) or isinstance(self.write_roots, (str, bytes)):
            raise ValueError("policy roots must be sequences of paths")
        try:
            read = tuple(_canonical_root(item) for item in self.read_roots)
            write = tuple(_canonical_root(item) for item in self.write_roots)
        except TypeError as exc:
            raise ValueError("policy roots must be sequences of paths") from exc
        object.__setattr__(self, "read_roots", read)
        object.__setattr__(self, "write_roots", write)

    @classmethod
    def restrictive(
        cls,
        *,
        tools: Iterable[str] = (),
        read_roots: Iterable[str | Path] = (),
        write_roots: Iterable[str | Path] = (),
        environment: Iterable[str] = (),
    ) -> "AgentPolicy":
        return cls(frozenset(str(item) for item in tools), tuple(Path(item) for item in read_roots), tuple(Path(item) for item in write_roots), frozenset(str(item) for item in environment))

    def check_tool(self, name: str) -> PolicyDecision:
        if not isinstance(name, str) or not name:
            return PolicyDecision(False, ErrorCode.TOOL_NOT_ALLOWED.value, "tool name is invalid", {"tool": str(name)})
        if name not in self.allowed_tools:
            return PolicyDecision(False, ErrorCode.TOOL_NOT_ALLOWED.value, "tool is not allowlisted", {"tool": name})
        return PolicyDecision(True)

    def require_tool(self, name: str) -> None:
        self.check_tool(name).require()

    def check_path(self, path: str | Path, *, write: bool = False, must_exist: bool = False) -> Path:
        try:
            candidate = Path(path).expanduser()
        except (TypeError, ValueError, OSError) as exc:
            raise ProtocolError(ErrorCode.PATH_DENIED, "path is invalid", {"path": str(path)}) from exc
        raw_path = str(path)
        if (
            "\x00" in raw_path
            or "\\" in raw_path
            or _WINDOWS_DRIVE.match(raw_path) is not None
            or len(raw_path) > 4096
            or _unsafe_path_spelling(raw_path)
        ):
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "path has an unsafe spelling", {"path": raw_path})
        # Reject lexical traversal even if normalization would happen to land
        # back inside an allowlisted root.  Policy decisions are made against
        # the provider-supplied spelling as well as the physical target so the
        # audit trail cannot hide a traversal attempt.
        if ".." in candidate.parts:
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "path contains parent traversal", {"path": str(path)})
        roots = self.write_roots if write else (*self.read_roots, *self.write_roots)
        if not roots:
            raise ProtocolError(ErrorCode.PATH_DENIED, "no path roots are configured")
        if candidate.is_absolute():
            logical = candidate
        else:
            raise ProtocolError(ErrorCode.PATH_DENIED, "agent paths must be absolute")
        # Walk components to reject a symlink even when its resolved target is
        # still inside an allowlisted root.  This keeps artifact provenance
        # stable and prevents aliases from changing underneath a run.  Check
        # the spelling before containment so an alias that escapes the root is
        # reported as SYMLINK_ESCAPE rather than an ambiguous PATH_ESCAPE.
        symlink_seen = False
        for root in roots:
            # A missing final component is fine for a write, but every
            # existing parent must remain a regular directory.  The helper
            # also rejects a symlink root itself.
            if _contains_symlink_between(root, logical):
                symlink_seen = True
                continue
            try:
                physical = logical.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise ProtocolError(ErrorCode.PATH_ESCAPE, "path cannot be resolved", {"path": str(path)}) from exc
            if not _is_relative_to(physical, root):
                continue
            if must_exist and (not physical.exists() or physical.is_symlink()):
                raise ProtocolError(ErrorCode.PATH_DENIED, "path is missing or not a regular target", {"path": str(path)})
            if physical.exists() and not (physical.is_file() or physical.is_dir()):
                raise ProtocolError(ErrorCode.PATH_DENIED, "path is not a regular file or directory", {"path": str(path)})
            if must_exist and physical.is_file() and physical.stat().st_size > self.max_artifact_bytes:
                raise ProtocolError(ErrorCode.PATH_DENIED, "artifact exceeds policy size cap", {"path": str(path)})
            return physical
        if symlink_seen:
            raise ProtocolError(ErrorCode.SYMLINK_ESCAPE, "path traverses a symlink", {"path": str(path)})
        try:
            resolved_logical = logical.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "path cannot be resolved", {"path": str(path)}) from exc
        if any(_is_relative_to(resolved_logical, root) for root in roots):
            raise ProtocolError(ErrorCode.SYMLINK_ESCAPE, "path traverses a symlink", {"path": str(path)})
        raise ProtocolError(ErrorCode.PATH_ESCAPE, "path is outside policy roots", {"path": str(path)})

    def check_locator(self, locator: ArtifactLocator, *, write: bool = False, must_exist: bool = False) -> Path:
        roots = self.write_roots if write else (*self.read_roots, *self.write_roots)
        if not roots:
            raise ProtocolError(ErrorCode.PATH_DENIED, "no path roots are configured")
        last_error: ProtocolError | None = None
        for root in roots:
            try:
                path = locator.resolve(root, must_exist=must_exist)
                return self.check_path(path, write=write, must_exist=must_exist)
            except ProtocolError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ProtocolError(ErrorCode.PATH_DENIED, "artifact locator is not allowed")

    def sanitize_environment(self, environment: Mapping[str, str] | None = None) -> dict[str, str]:
        """Validate and return the explicitly allowlisted environment.

        ``environment=None`` means "select values from the ambient process";
        in that mode unlisted ambient names are ignored because a normal shell
        contains many unrelated variables.  A mapping supplied by a tool is a
        capability request, however, so every supplied name must be
        allowlisted.  This distinction prevents a nested ``env`` object from
        silently dropping an attacker-controlled variable and making the
        caller believe it was accepted.
        """

        explicit = environment is not None
        source = os.environ if environment is None else environment
        if not isinstance(source, Mapping):
            raise ProtocolError(ErrorCode.ENVIRONMENT_DENIED, "environment must be an object")
        result: dict[str, str] = {}
        for raw_name, value in source.items():
            if not isinstance(raw_name, str) or not _ENV_NAME.fullmatch(raw_name):
                raise ProtocolError(
                    ErrorCode.ENVIRONMENT_DENIED,
                    "environment name is invalid",
                    {"name": str(raw_name)},
                )
            if explicit and raw_name not in self.allowed_environment:
                raise ProtocolError(
                    ErrorCode.ENVIRONMENT_DENIED,
                    "environment variable is not allowlisted",
                    {"name": raw_name},
                )
            if raw_name not in self.allowed_environment:
                continue
            if _SECRET_NAME.search(raw_name):
                raise ProtocolError(
                    ErrorCode.ENVIRONMENT_DENIED,
                    "secret environment variable is forbidden",
                    {"name": raw_name},
                )
            if not isinstance(value, str) or "\x00" in value:
                raise ProtocolError(
                    ErrorCode.ENVIRONMENT_DENIED,
                    "environment value is invalid",
                    {"name": raw_name},
                )
            result[raw_name] = value
        # A provider must never inherit ambient credentials or arbitrary
        # proxy/network settings.  Only explicit names are returned.
        return result

    def validate_action(self, action: Action, *, source_generation: str, template_lock: str | None = None) -> None:
        if not isinstance(action, Action):
            raise ProtocolError(ErrorCode.INVALID_ACTION, "policy can validate only an Action")
        if not isinstance(source_generation, str) or not source_generation:
            raise ProtocolError(ErrorCode.INVALID_ACTION, "source generation is invalid")
        if action.expected_source_generation != source_generation:
            raise ProtocolError(
                ErrorCode.STALE_SOURCE_GENERATION,
                "action targets a different source generation",
                {"expected": source_generation, "received": action.expected_source_generation},
            )
        if template_lock is not None and action.template_lock not in (None, template_lock):
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "action targets a different template lock")
        # Explicitly reject shell/process/network-shaped arguments even when a
        # caller accidentally registers a broad tool name.
        _validate_action_capability_fields(action.params, self)


def _contains_symlink_between(root: Path, target: Path) -> bool:
    try:
        root = root.resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    try:
        relative = target.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _canonical_root(value: str | Path) -> Path:
    """Resolve an allowlist root without silently accepting a symlink alias."""
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("policy root is invalid: %r" % (value,)) from exc
    if path_has_any_symlink_component(path):
        raise ValueError("policy roots must not be symlinks: %s" % path)
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError("policy root cannot be resolved: %s" % path) from exc
    if not resolved.is_dir():
        raise ValueError("policy root is not a directory: %s" % path)
    return resolved


def _unsafe_path_spelling(value: str) -> bool:
    """Detect lexical ``.``/``..`` and repeated interior separators.

    ``Path.parts`` normalizes some of these components before policy sees
    them, so inspect the original spelling as well.  Leading/trailing slashes
    are valid absolute-path syntax; empty interior components are not.
    """
    if not value:
        return True
    pieces = value.split("/")
    for index, piece in enumerate(pieces):
        if piece in {".", ".."}:
            return True
        if piece == "" and index not in {0, len(pieces) - 1}:
            return True
    return False


def _validate_action_capability_fields(
    value: Any,
    policy: AgentPolicy,
    path: str = "params",
    _seen: set[int] | None = None,
) -> None:
    """Reject process/network-shaped action data recursively.

    Action parameters are provider-controlled JSON.  Checking only the first
    level would let a nested object or list smuggle a command or endpoint past
    the policy boundary.
    """
    forbidden_process = {
        "shell",
        "command",
        "argv",
        "python",
        "eval",
        "exec",
        "executable",
        "process",
        "program",
        "subprocess",
        "script",
        "code",
    }
    forbidden_network = {"url", "endpoint", "network", "proxy", "host"}
    if _seen is None:
        _seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _seen:
            raise ProtocolError(
                ErrorCode.INVALID_ACTION,
                "action capability fields contain a cyclic object",
                {"field": path},
            )
        _seen.add(identity)
        try:
            for raw_key, child in value.items():
                key = str(raw_key)
                lowered = key.lower()
                child_path = path + "." + key
                # The presence of a process-shaped field is itself a capability
                # request.  Reject even falsey values so ``{"command": ""}`` or
                # ``{"argv": []}`` cannot be used to probe/alter execution paths.
                if lowered in forbidden_process and not policy.allow_process:
                    raise ProtocolError(
                        ErrorCode.TOOL_NOT_ALLOWED,
                        "process or code execution is disabled",
                        {"field": child_path},
                    )
                if lowered in forbidden_network and not policy.allow_network:
                    raise ProtocolError(
                        ErrorCode.TOOL_NOT_ALLOWED,
                        "network access is disabled",
                        {"field": child_path},
                    )
                _validate_action_capability_fields(child, policy, child_path, _seen)
        finally:
            _seen.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _seen:
            raise ProtocolError(
                ErrorCode.INVALID_ACTION,
                "action capability fields contain a cyclic object",
                {"field": path},
            )
        _seen.add(identity)
        try:
            for index, child in enumerate(value):
                _validate_action_capability_fields(child, policy, "%s[%d]" % (path, index), _seen)
        finally:
            _seen.remove(identity)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = ["AgentPolicy", "PolicyDecision"]
