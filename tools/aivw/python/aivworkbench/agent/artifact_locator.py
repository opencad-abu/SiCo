"""Validate and resolve bounded agent artifact references."""

from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping
from ..artifact_paths import PathContractError, path_has_any_symlink_component, path_has_symlink_component, validate_relative_path
from .protocol import ErrorCode, ProtocolError
from .redaction import contains_secret_name


@dataclass(frozen=True)
class ArtifactLocator:
    """A payload-relative, hash-addressed artifact reference."""

    path: str
    sha256: str | None = None
    size: int | None = None
    media_type: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> "ArtifactLocator":
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "artifact locator must be an object")
        allowed = {"path", "sha256", "size", "media_type"}
        unknown = [key for key in value if not isinstance(key, str) or key not in allowed]
        if unknown:
            raise ProtocolError(
                ErrorCode.UNKNOWN_FIELD,
                "artifact locator has unknown fields",
                {"fields": sorted(str(item) for item in unknown)},
            )
        media_type = value.get("media_type")
        if media_type is not None and (not isinstance(media_type, str) or not media_type or len(media_type) > 256):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "artifact locator media_type is invalid")
        return cls(value.get("path"), value.get("sha256"), value.get("size"), media_type)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "artifact locator path must be text")
        try:
            validate_relative_path(self.path, "artifact locator")
        except PathContractError:
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "artifact locator must be a relative path", {"path": self.path})
        if self.path.startswith(("/", "\\")) or "\\" in self.path:
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "artifact locator contains an unsafe separator")
        if contains_secret_name(self.path):
            raise ProtocolError(ErrorCode.PATH_DENIED, "artifact locator path is secret-bearing")
        if self.sha256 is not None and (
            not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "artifact locator sha256 is invalid")
        if self.size is not None and (not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "artifact locator size is invalid")
        if self.media_type is not None and contains_secret_name(self.media_type):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "artifact locator media_type is secret-bearing")

    def resolve(self, root: str | Path, *, must_exist: bool = False) -> Path:
        root_input = Path(root).expanduser()
        if path_has_any_symlink_component(root_input):
            raise ProtocolError(ErrorCode.SYMLINK_ESCAPE, "artifact root must not be a symlink", {"root": str(root)})
        try:
            root_path = root_input.resolve()
        except (OSError, RuntimeError) as exc:
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "artifact root cannot be resolved", {"root": str(root)}) from exc
        if not root_path.is_dir():
            raise ProtocolError(ErrorCode.PATH_DENIED, "artifact root is not a directory", {"root": str(root)})
        candidate = root_path / self.path
        # Reject symlinks in every component, including a final link.  A
        # locator is an audit reference, not a way to follow an alias.
        if path_has_symlink_component(root_path, candidate):
            raise ProtocolError(ErrorCode.SYMLINK_ESCAPE, "artifact locator traverses a symlink", {"path": self.path})
        try:
            physical = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "artifact locator cannot be resolved", {"path": self.path}) from exc
        if not _is_relative_to(physical, root_path):
            raise ProtocolError(ErrorCode.PATH_ESCAPE, "artifact locator escaped its root", {"path": self.path})
        if must_exist and (not physical.is_file() or physical.is_symlink()):
            raise ProtocolError(ErrorCode.PATH_DENIED, "artifact locator does not identify a regular file", {"path": self.path})
        if must_exist and self.sha256 is not None:
            digest = hashlib.sha256(physical.read_bytes()).hexdigest()
            if digest != self.sha256:
                raise ProtocolError(ErrorCode.BUNDLE_HASH_MISMATCH, "artifact locator hash mismatch", {"path": self.path})
        if must_exist and self.size is not None and physical.stat().st_size != self.size:
            raise ProtocolError(ErrorCode.BUNDLE_HASH_MISMATCH, "artifact locator size mismatch", {"path": self.path})
        return physical

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"path": self.path}
        if self.sha256 is not None:
            value["sha256"] = self.sha256
        if self.size is not None:
            value["size"] = self.size
        if self.media_type is not None:
            value["media_type"] = self.media_type
        return value


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

