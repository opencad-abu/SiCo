"""Integrity and metadata validation for offline legacy bundle data."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from ..protocol import PROTOCOL_VERSION
from .bundle_contract import (
    BundleValidation,
    _BUNDLE_FORMAT,
    _COMMIT,
    _PYTHON_VERSION,
    _WINDOWS_DRIVE,
    _load_bundle_json,
)

def validate_bundle(
    root: str | Path,
    *,
    expected_protocol_version: str = PROTOCOL_VERSION,
    expected_source_generation: str | None = None,
    expected_template_lock: str | None = None,
    require_response: bool = False,
    require_codex_port: bool = True,
    expected_python_version: tuple[int, int, int] = _PYTHON_VERSION,
) -> BundleValidation:
    """Check legacy provider data; success does not qualify a release."""

    raw_root = Path(root).expanduser()
    if _has_symlink_component(raw_root):
        return BundleValidation(raw_root.resolve(strict=False), False, expected_protocol_version, None, None, (), ("bundle root traverses a symlink",), {})
    try:
        bundle_root = raw_root.resolve(strict=True)
    except (OSError, RuntimeError):
        bundle_root = raw_root.resolve(strict=False)
    errors: list[str] = []
    checked: list[str] = []
    metadata: dict[str, Any] = {}
    if not bundle_root.is_dir() or bundle_root.is_symlink():
        return BundleValidation(bundle_root, False, expected_protocol_version, None, None, (), ("bundle root is not a directory",), {})
    manifest_path = bundle_root / "MANIFEST.sha256"
    build_path = bundle_root / "BUILD-METADATA.json"
    port_path = bundle_root / "codex-port.json"
    for required in ("MANIFEST.sha256", "BUILD-METADATA.json", "LICENSE", "NOTICE"):
        required_path = bundle_root / required
        if required_path.is_symlink():
            errors.append("%s must not be a symlink" % required)
        elif not required_path.is_file():
            errors.append("missing %s" % required)
    if port_path.is_symlink():
        errors.append("codex-port.json must not be a symlink")
    elif require_codex_port and not port_path.is_file():
        errors.append("missing codex-port.json")
    build: Mapping[str, Any] = {}
    if build_path.is_file():
        checked.append("BUILD-METADATA.json")
        try:
            raw_build = _load_bundle_json(build_path.read_text(encoding="utf-8"))
            if not isinstance(raw_build, Mapping):
                raise ValueError("root is not an object")
            _validate_build_metadata(raw_build, errors, expected_protocol_version, expected_python_version)
            build = raw_build
            metadata.update(dict(raw_build))
        except (OSError, ValueError) as exc:
            errors.append("invalid BUILD-METADATA.json: %s" % exc)
    protocol = str(build.get("protocol_version", expected_protocol_version))
    if protocol != expected_protocol_version:
        errors.append("protocol version mismatch: %s" % protocol)
    source_generation = _optional_text(build.get("source_generation"))
    template_lock = _optional_text(build.get("template_lock"))
    if not source_generation:
        errors.append("BUILD-METADATA.json source_generation is missing")
    if "template_lock" not in build:
        errors.append("BUILD-METADATA.json template_lock is missing")
    if expected_source_generation is not None and source_generation != expected_source_generation:
        errors.append("source generation mismatch")
    if expected_template_lock is not None and template_lock != expected_template_lock:
        errors.append("template lock mismatch")
    if port_path.is_file():
        checked.append("codex-port.json")
        try:
            raw_port = _load_bundle_json(port_path.read_text(encoding="utf-8"))
            if not isinstance(raw_port, Mapping):
                raise ValueError("root is not an object")
            _validate_codex_port(raw_port, errors)
            metadata["codex_port"] = dict(raw_port)
        except (OSError, ValueError) as exc:
            errors.append("invalid codex-port.json: %s" % exc)
    if manifest_path.is_file() and not manifest_path.is_symlink():
        checked.append("MANIFEST.sha256")
        listed = _verify_manifest(manifest_path, bundle_root, errors, checked)
        # Every regular file (including metadata and license files) must be
        # covered by the hash manifest.  Otherwise an attacker could
        # append an unverified response or dependency after validation.
        for discovered in _bundle_files(bundle_root):
            if discovered not in listed:
                errors.append("bundle file is not listed in MANIFEST.sha256: %s" % discovered)
    elif manifest_path.is_symlink():
        errors.append("MANIFEST.sha256 must not be a symlink")
    for name in ("LICENSE", "NOTICE"):
        if (bundle_root / name).is_file():
            checked.append(name)
            try:
                if not (bundle_root / name).read_text(encoding="utf-8", errors="replace").strip():
                    errors.append("%s is empty" % name)
            except OSError as exc:
                errors.append("cannot read %s: %s" % (name, exc))
    licenses = bundle_root / "licenses"
    if licenses.is_symlink():
        errors.append("licenses must not be a symlink")
    elif licenses.exists() and not licenses.is_dir():
        errors.append("licenses is not a directory")
    for member in bundle_root.rglob("*"):
        if member.is_symlink():
            errors.append("bundle symlinks are not allowed: %s" % member.relative_to(bundle_root).as_posix())
    response_present = any((bundle_root / name).is_file() for name in ("response.json", "response.jsonl", "response.bundle.json"))
    if require_response and not response_present:
        errors.append("response bundle is missing")
    return BundleValidation(bundle_root, not errors, protocol, source_generation, template_lock, tuple(dict.fromkeys(checked)), tuple(dict.fromkeys(errors)), metadata)


def import_bundle(
    root: str | Path,
    *,
    expected_source_generation: str | None = None,
    expected_template_lock: str | None = None,
    expected_protocol_version: str = PROTOCOL_VERSION,
) -> BundleValidation:
    """Import legacy provider metadata, without copying or executing files."""

    result = validate_bundle(
        root,
        expected_protocol_version=expected_protocol_version,
        expected_source_generation=expected_source_generation,
        expected_template_lock=expected_template_lock,
        require_response=False,
    )
    return result.require()

def build_manifest(root: str | Path, *, include: Iterable[str] | None = None) -> list[str]:
    """Return deterministic integrity hashes for legacy provider data.

    This helper does not write files or qualify runtime releases.
    """

    raw_root = Path(root).expanduser()
    if _has_symlink_component(raw_root):
        raise ValueError("bundle root traverses a symlink")
    bundle_root = raw_root.resolve(strict=True)
    names = sorted(str(item) for item in include) if include is not None else _bundle_files(bundle_root)
    lines: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or "\\" in name or _WINDOWS_DRIVE.match(name):
            raise ValueError("unsafe bundle manifest path: %s" % name)
        relative = Path(name)
        normalized = relative.as_posix()
        if (
            relative.is_absolute()
            or normalized == "."
            or any(part in {"", ".", ".."} for part in relative.parts)
            or name != normalized
            or normalized == "MANIFEST.sha256"
            or normalized in seen
        ):
            raise ValueError("unsafe bundle manifest path: %s" % name)
        seen.add(normalized)
        path = bundle_root / relative
        if _has_symlink_component(path.parent) or not path.is_file() or path.is_symlink() or not _is_relative_to(path.resolve(), bundle_root):
            raise ValueError("bundle manifest target is unavailable: %s" % name)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append("%s  %s" % (digest, normalized))
    return lines

def _validate_build_metadata(
    value: Mapping[str, Any],
    errors: list[str],
    expected_protocol_version: str,
    expected_python_version: tuple[int, int, int],
) -> None:
    allowed = {
        "bundle_format", "protocol_version", "python_version", "source_generation", "template_lock",
        "dependencies", "dependency_hashes", "third_party_licenses", "backports",
        "runtime_library_policy", "python_root_libraries_allowed", "user_site_packages_enabled",
        "stdlib_only", "source_tree_excluded",
        "license_files",
        "build_id", "built_at", "platform",
    }
    unknown = sorted(str(key) for key in value if not isinstance(key, str) or key not in allowed)
    if unknown:
        errors.append("BUILD-METADATA.json contains unknown fields: %s" % ", ".join(unknown))
    bundle_format = value.get("bundle_format")
    if bundle_format not in (None, _BUNDLE_FORMAT):
        errors.append("bundle format is unsupported")
    if value.get("protocol_version") != expected_protocol_version:
        errors.append("BUILD-METADATA.json protocol_version is invalid")
    # Historical M0 metadata declares the runtime-library policy and notices.  Keep older v1 bundles readable when
    # they predate those fields; once a bundle declares any field from the new
    # policy group, require the complete group and validate it fail-closed.
    if bundle_format == _BUNDLE_FORMAT:
        required = {
            "bundle_format",
            "protocol_version",
            "python_version",
            "source_generation",
            "template_lock",
            "dependencies",
            "dependency_hashes",
            "third_party_licenses",
            "stdlib_only",
            "source_tree_excluded",
        }
        missing = sorted(item for item in required if item not in value)
        if missing:
            errors.append("BUILD-METADATA.json is missing fields: %s" % ", ".join(missing))
        python_version = value.get("python_version")
        if (
            not isinstance(python_version, (list, tuple))
            or len(python_version) != 3
            or any(not isinstance(item, int) or isinstance(item, bool) for item in python_version)
            or tuple(python_version) != tuple(expected_python_version)
        ):
            errors.append("BUILD-METADATA.json python_version is not 3.9.13")
        if not isinstance(value.get("stdlib_only"), bool):
            errors.append("BUILD-METADATA.json stdlib_only must be boolean")
        if value.get("source_tree_excluded") is not True:
            errors.append("BUILD-METADATA.json source_tree_excluded must be true")
        if not isinstance(value.get("source_generation"), str) or not value.get("source_generation"):
            errors.append("BUILD-METADATA.json source_generation must be non-empty text")
        if not isinstance(value.get("template_lock"), str) or not value.get("template_lock"):
            errors.append("BUILD-METADATA.json template_lock must be non-empty text")
        policy_fields = {
            "backports",
            "runtime_library_policy",
            "python_root_libraries_allowed",
            "user_site_packages_enabled",
            "license_files",
        }
        declared_policy_fields = policy_fields.intersection(value)
        if declared_policy_fields:
            missing_policy = sorted(policy_fields - declared_policy_fields)
            if missing_policy:
                errors.append(
                    "BUILD-METADATA.json is missing runtime policy fields: %s"
                    % ", ".join(missing_policy)
                )
            if value.get("backports") != []:
                errors.append("BUILD-METADATA.json backports must be an empty array for the M0 bundle")
            if value.get("runtime_library_policy") != "cad-python-root":
                errors.append("BUILD-METADATA.json runtime_library_policy must be cad-python-root")
            if value.get("python_root_libraries_allowed") is not True:
                errors.append("BUILD-METADATA.json python_root_libraries_allowed must be true")
            if value.get("user_site_packages_enabled") is not False:
                errors.append("BUILD-METADATA.json user_site_packages_enabled must be false")
            license_files = value.get("license_files")
            if license_files != ["LICENSE", "NOTICE"]:
                errors.append("BUILD-METADATA.json license_files must list LICENSE and NOTICE")
    dependencies = value.get("dependencies", [])
    hashes = value.get("dependency_hashes", {})
    licenses = value.get("third_party_licenses", [])
    if not isinstance(dependencies, list) or not isinstance(hashes, Mapping) or not isinstance(licenses, list):
        errors.append("BUILD-METADATA.json dependency fields have invalid types")
        return
    names: set[str] = set()
    for item in dependencies:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str) or not isinstance(item.get("sha256"), str):
            errors.append("BUILD-METADATA.json dependency entry is invalid")
            continue
        name = item["name"]
        digest = item["sha256"]
        if not name or name in names:
            errors.append("duplicate or empty dependency name: %s" % name)
        names.add(name)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashes.get(name) != digest:
            errors.append("dependency hash mismatch: %s" % name)
    hash_names = set()
    for raw_name, digest in hashes.items():
        if not isinstance(raw_name, str) or not raw_name:
            errors.append("dependency hash name is invalid")
            continue
        hash_names.add(raw_name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append("dependency hash is invalid: %s" % raw_name)
    license_names = set()
    for item in licenses:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str) or not isinstance(item.get("license"), str):
            errors.append("BUILD-METADATA.json third-party license entry is invalid")
            continue
        name = item["name"]
        if not name or name in license_names:
            errors.append("duplicate or empty third-party license name: %s" % name)
        license_names.add(name)
        if not item.get("license") or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
            errors.append("third-party license entry is incomplete: %s" % name)
        if item.get("sha256") != hashes.get(name):
            errors.append("third-party license hash mismatch: %s" % item["name"])
    if names != hash_names or names != license_names:
        errors.append("dependency/hash/license name sets disagree")
    if bundle_format == _BUNDLE_FORMAT and value.get("stdlib_only") is not None:
        if bool(value.get("stdlib_only")) != (not names):
            errors.append("BUILD-METADATA.json stdlib_only disagrees with bundled dependencies")


def _has_symlink_component(path: Path) -> bool:
    candidate = path if path.is_absolute() else Path.cwd() / path
    current = Path(candidate.anchor) if candidate.is_absolute() else Path.cwd()
    parts = candidate.parts[1:] if candidate.is_absolute() else candidate.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _verify_manifest(path: Path, root: Path, errors: list[str], checked: list[str]) -> set[str]:
    listed: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append("cannot read MANIFEST.sha256: %s" % exc)
        return listed
    seen: set[str] = set()
    for index, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            errors.append("invalid MANIFEST.sha256 line %d" % index)
            continue
        relative = parts[1].strip()
        if not relative or "\\" in relative or _WINDOWS_DRIVE.match(relative):
            errors.append("unsafe or duplicate manifest path: %s" % relative)
            continue
        candidate = Path(relative)
        normalized = candidate.as_posix()
        if (
            candidate.is_absolute()
            or normalized == "."
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or relative != normalized
            or relative in seen
            or relative == "MANIFEST.sha256"
        ):
            errors.append("unsafe or duplicate manifest path: %s" % relative)
            continue
        seen.add(relative)
        listed.add(relative)
        physical_path = root / candidate
        physical = physical_path.resolve(strict=False)
        if _has_symlink_component(physical_path.parent) or physical_path.is_symlink() or not physical.is_file() or not _is_relative_to(physical, root):
            errors.append("manifest file is missing or unsafe: %s" % relative)
            continue
        checked.append(relative)
        actual = hashlib.sha256(physical.read_bytes()).hexdigest()
        if actual != parts[0]:
            errors.append("manifest hash mismatch: %s" % relative)
    return listed


def _validate_codex_port(
    value: Mapping[str, Any],
    errors: list[str],
) -> None:
    allowed = {"format", "reference_commit", "commit", "capabilities", "mappings", "reference_tree_in_bundle", "license", "source_paths"}
    unknown = sorted(str(key) for key in value if not isinstance(key, str) or key not in allowed)
    if unknown:
        errors.append("codex-port contains unknown fields: %s" % ", ".join(unknown))
    format_name = value.get("format")
    if format_name is not None and format_name != "aivw-codex-port-v1":
        errors.append("codex-port format is unsupported")
    commit = value.get("reference_commit", value.get("commit"))
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        errors.append("codex-port reference commit is invalid")
    entries = value.get("capabilities", value.get("mappings", []))
    capability_status: dict[str, str] = {}
    if not isinstance(entries, list):
        errors.append("codex-port capabilities must be an array")
    else:
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                errors.append("codex-port capability %d is not an object" % index)
                continue
            status = entry.get("status")
            if not isinstance(status, str) or status not in {"ported", "partial", "excluded"}:
                errors.append("codex-port capability %d has invalid status" % index)
            name = entry.get("name", entry.get("source", ""))
            if not isinstance(name, str) or not name:
                errors.append("codex-port capability %d has no name" % index)
                continue
            if name in capability_status:
                errors.append("codex-port capability name is duplicated: %s" % name)
                continue
            if isinstance(status, str):
                capability_status[name] = status
    if value.get("reference_tree_in_bundle") is True:
        errors.append("codex-port must not include the reference tree")
    if value.get("license") not in (None, "Apache-2.0"):
        errors.append("codex-port license is unsupported")


def _bundle_files(root: Path) -> list[str]:
    ignored = {"MANIFEST.sha256"}
    result: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            # Symlinks are never valid bundle members, even if they point
            # inside the root.  Report them through validation separately.
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in ignored:
            result.append(relative)
    return result


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = ["build_manifest", "import_bundle", "validate_bundle", "_bundle_files", "_has_symlink_component", "_is_relative_to", "_optional_text", "_validate_build_metadata", "_validate_codex_port", "_verify_manifest"]
