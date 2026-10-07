"""Data contracts and strict JSON helpers for legacy bundle records."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..protocol import ErrorCode, ProtocolError
from ..value_codec import thaw

_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_BUNDLE_FORMAT = "aivw-agent-bundle-v1"
_PYTHON_VERSION = (3, 9, 13)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")

class _DuplicateBundleKey(ValueError):
    pass


def _reject_bundle_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


def _reject_bundle_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise _DuplicateBundleKey("duplicate bundle field: %s" % key)
        result[key] = item
    return result


def _load_bundle_json(raw: str | bytes) -> Any:
    return json.loads(
        raw,
        parse_constant=_reject_bundle_constant,
        object_pairs_hook=_reject_bundle_duplicates,
    )


@dataclass(frozen=True)
class BundleValidation:
    root: Path
    valid: bool
    protocol_version: str
    source_generation: str | None
    template_lock: str | None
    files_checked: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "LEGACY_DATA_VALID" if self.valid else "BLOCKED_BUNDLE"

    def require(self) -> "BundleValidation":
        if not self.valid:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "bundle validation failed", {"errors": list(self.errors)})
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "valid": self.valid,
            "status": self.status,
            "validation_scope": "legacy_provider_data",
            "release_qualified": False,
            "protocol_version": self.protocol_version,
            "source_generation": self.source_generation,
            "template_lock": self.template_lock,
            "files_checked": list(self.files_checked),
            "errors": list(self.errors),
            "metadata": thaw(self.metadata),
        }


__all__ = ["BundleValidation", "_BUNDLE_FORMAT", "_COMMIT", "_PYTHON_VERSION", "_SHA256", "_WINDOWS_DRIVE", "_DuplicateBundleKey", "_load_bundle_json", "_reject_bundle_constant", "_reject_bundle_duplicates"]
