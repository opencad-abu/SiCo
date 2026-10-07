"""Compatibility facade for legacy offline bundle validation and replay.

This facade preserves the historical import path. Integrity validation and the
offline provider each live in their owning modules; source bundle construction
remains a fail-closed legacy tombstone.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping
from pathlib import Path

from ..protocol import ErrorCode, ProtocolError
from . import bundle_contract as _contract
from . import bundle_integrity as _integrity
from .bundle_provider import BundleProvider

BundleValidation = _contract.BundleValidation
_BUNDLE_FORMAT = _contract._BUNDLE_FORMAT
_COMMIT = _contract._COMMIT
_PYTHON_VERSION = _contract._PYTHON_VERSION
_SHA256 = _contract._SHA256
_WINDOWS_DRIVE = _contract._WINDOWS_DRIVE
_DuplicateBundleKey = _contract._DuplicateBundleKey
_load_bundle_json = _contract._load_bundle_json
_reject_bundle_constant = _contract._reject_bundle_constant
_reject_bundle_duplicates = _contract._reject_bundle_duplicates

_bundle_files = _integrity._bundle_files
_has_symlink_component = _integrity._has_symlink_component
_is_relative_to = _integrity._is_relative_to
_optional_text = _integrity._optional_text
_validate_build_metadata = _integrity._validate_build_metadata
_validate_codex_port = _integrity._validate_codex_port
_verify_manifest = _integrity._verify_manifest
build_manifest = _integrity.build_manifest
import_bundle = _integrity.import_bundle
validate_bundle = _integrity.validate_bundle


def build_relocatable_bundle(
    destination: str | Path,
    *,
    source_root: str | Path | None = None,
    source_generation: str,
    template_lock: str,
    reference_commit: str,
    codex_capabilities: Iterable[Mapping[str, Any]] | None = None,
    extra_files: Iterable[str | Path] = (),
    dependencies: Iterable[Mapping[str, Any]] = (),
    overwrite: bool = False,
) -> BundleValidation:
    """Reject the retired source packager before inspecting or writing paths.

    Legacy tombstone only. Remove at the next incompatible provider API version
    after supported release callers have migrated to the runtime packager.
    """
    raise ProtocolError(
        ErrorCode.BUNDLE_INVALID,
        "Legacy AIVW source bundle construction is disabled. "
        "Use deploy/package_runtime_release.py with explicitly inventoried, "
        "qualified native artifacts; source fallback is not supported.",
    )


__all__ = [
    "BundleProvider", "BundleValidation", "build_manifest",
    "import_bundle", "validate_bundle",
]
