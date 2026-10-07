"""Production interpreter, installed-library, and bundle capability reporting.

The runtime policy is tied to the exact ``CAD_PYTHON_ROOT`` installation:
stdlib, ``lib-dynload`` and that prefix's ``site-packages`` are available to
production code.  User-site paths and ambient ``PYTHONPATH``/``PYTHONHOME``
injection remain disabled.  Syntax features that Python 3.9 cannot parse are
probed by passing source text to :mod:`ast`; the production modules never
contain those syntax forms themselves.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
from dataclasses import dataclass
import dataclasses
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import site
import sys
import sysconfig
import typing
from typing import Any, Mapping
from sicoenv import value as environment_value


EXPECTED_PYTHON_VERSION = (3, 9, 13)


def _syntax_supported(source: str) -> bool:
    try:
        ast.parse(source, mode="exec")
    except (SyntaxError, ValueError, TypeError):
        return False
    return True


def _dataclass_slots_supported() -> bool:
    """Probe the keyword and its behavior without using 3.10 syntax."""

    try:
        parameters = inspect.signature(dataclass).parameters
        if "slots" not in parameters:
            return False
        sample = type("_RuntimeInfoProbe", (), {"__annotations__": {"value": int}})
        dataclass(**{"slots": True})(sample)
        return hasattr(sample, "__slots__")
    except (TypeError, ValueError, AttributeError):
        return False


def detect_capabilities() -> dict[str, bool]:
    """Return feature/library availability on the *currently running* interpreter."""

    return {
        "match_syntax": _syntax_supported("match value:\n case _:\n  pass"),
        "except_star_syntax": _syntax_supported(
            "try:\n pass\nexcept* Exception:\n pass"
        ),
        "dataclass_slots": _dataclass_slots_supported(),
        "typing_self": hasattr(typing, "Self"),
        "tomllib": importlib.util.find_spec("tomllib") is not None,
        # Python 3.9.13 has no native tomllib, but this fixed installation
        # supplies tomli as an approved prefix library.
        "toml": importlib.util.find_spec("toml") is not None,
        "tomli": importlib.util.find_spec("tomli") is not None,
        "PyQt5": importlib.util.find_spec("PyQt5") is not None,
        "exception_group": hasattr(builtins, "ExceptionGroup"),
        "asyncio_task_group": hasattr(asyncio, "TaskGroup"),
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _user_site_enabled() -> bool:
    # ``site.ENABLE_USER_SITE`` alone is not enough: a launcher may have
    # already removed the directory from ``sys.path`` while the interpreter
    # still reports the feature as enabled.  Conversely, a user-site entry in
    # ``sys.path`` is a production violation even when the flag is false.
    try:
        user_paths = site.getusersitepackages()
    except (AttributeError, OSError):
        return False
    if isinstance(user_paths, str):
        user_paths = [user_paths]
    normalized_sys = {os.path.realpath(item) for item in sys.path if isinstance(item, str)}
    # Either signal is sufficient to fail closed.  A launcher can set the
    # flag incorrectly, and a user-site directory can be injected directly
    # into sys.path even when the flag is false.
    return bool(site.ENABLE_USER_SITE) or any(os.path.realpath(path) in normalized_sys for path in user_paths)


def _stdlib_inside_root(root_real: str | None) -> bool:
    if not root_real:
        return False
    try:
        stdlib = Path(sysconfig.get_paths().get("stdlib", "")).resolve()
        root = Path(root_real).resolve()
        stdlib.relative_to(root)
        return True
    except (KeyError, OSError, RuntimeError, ValueError):
        return False


def _python_paths() -> dict[str, str | None]:
    """Return canonical interpreter paths relevant to the library policy."""

    try:
        paths = sysconfig.get_paths()
    except (AttributeError, OSError, TypeError):
        paths = {}
    result: dict[str, str | None] = {}
    for key in ("stdlib", "platstdlib", "purelib", "platlib", "data"):
        value = paths.get(key)
        result[key] = os.path.realpath(value) if isinstance(value, str) and value else None
    # DESTSHARED is a build-time install path and survives relocation unchanged.
    # Use the actual search directory of this interpreter, still subject to the
    # production-root containment check below. Never accept a guessed fallback.
    dynloads = {
        os.path.realpath(path) for path in sys.path
        if isinstance(path, str) and os.path.basename(path) == "lib-dynload"
        and os.path.isdir(path)
    }
    result["libdynload"] = next(iter(dynloads)) if len(dynloads) == 1 else None
    return result


def _path_inside_root(path: str | None, root_real: str | None) -> bool:
    if not path or not root_real:
        return False
    try:
        return _is_relative_to(Path(path), Path(root_real))
    except (OSError, RuntimeError, ValueError):
        return False


def _module_origin(module_name: str, root_real: str | None) -> dict[str, Any]:
    """Report availability and containment without importing optional modules."""

    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError):
        spec = None
    origin = getattr(spec, "origin", None) if spec is not None else None
    origin_real = os.path.realpath(origin) if isinstance(origin, str) and origin not in {"built-in", "frozen"} else origin
    return {
        "available": spec is not None,
        "origin": origin,
        "origin_realpath": origin_real,
        "origin_inside_CAD_PYTHON_ROOT": _path_inside_root(origin_real, root_real),
    }


def _installed_distributions(root_real: str | None) -> list[dict[str, Any]]:
    """List distributions visible from the fixed prefix, without resolving dependencies."""

    try:
        from importlib import metadata as importlib_metadata
    except ImportError:
        return []
    result: list[dict[str, Any]] = []
    try:
        distributions = importlib_metadata.distributions()
    except Exception:
        return result
    for distribution in distributions:
        try:
            name = distribution.metadata.get("Name") or distribution.name
            version = distribution.version
            location = os.path.realpath(str(distribution.locate_file("")))
        except Exception:
            continue
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        result.append(
            {
                "name": name,
                "version": version,
                "location": location,
                "location_inside_CAD_PYTHON_ROOT": _path_inside_root(location, root_real),
            }
        )
    result.sort(key=lambda item: (item["name"].lower(), item["version"]))
    return result


def _default_bundle_root() -> Path:
    # runtime_info.py -> agent -> aivworkbench -> python -> project root
    return Path(__file__).resolve().parents[3]


def bundle_dependency_info(bundle_root: str | Path | None = None) -> dict[str, Any]:
    """Summarize fixed bundle metadata and hashes without importing anything."""

    root = Path(bundle_root).expanduser().resolve() if bundle_root is not None else _default_bundle_root()
    result: dict[str, Any] = {
        "root": str(root),
        "manifest": None,
        "manifest_sha256": None,
        "dependencies": [],
        "dependency_hashes": {},
        "third_party_licenses": [],
        "runtime_library_policy": None,
        "python_root_libraries_allowed": None,
        "user_site_packages_enabled": None,
        "stdlib_only_bundle_payload": None,
    }
    manifest = root / "MANIFEST.sha256"
    if manifest.is_file() and not manifest.is_symlink():
        try:
            raw = manifest.read_bytes()
            result["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
            entries: list[str] = []
            for line in raw.decode("utf-8", errors="strict").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split(None, 1)
                if len(parts) == 2:
                    entries.append(parts[1].strip())
            result["manifest"] = entries
        except (OSError, UnicodeError):
            result["manifest"] = "UNREADABLE"
    metadata_path = root / "BUILD-METADATA.json"
    if metadata_path.is_file() and not metadata_path.is_symlink():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            metadata = {}
        if isinstance(metadata, Mapping):
            dependencies = metadata.get("dependencies", [])
            if isinstance(dependencies, list):
                result["dependencies"] = list(dependencies)
            hashes = metadata.get("dependency_hashes", metadata.get("hashes", {}))
            if isinstance(hashes, Mapping):
                result["dependency_hashes"] = dict(hashes)
            licenses = metadata.get("third_party_licenses", metadata.get("licenses", []))
            if isinstance(licenses, list):
                result["third_party_licenses"] = list(licenses)
            for key in ("runtime_library_policy", "python_root_libraries_allowed", "user_site_packages_enabled"):
                if key in metadata:
                    result[key] = metadata[key]
            if "stdlib_only" in metadata:
                # Keep the old field readable while naming its limited scope.
                result["stdlib_only_bundle_payload"] = metadata["stdlib_only"]
    # Release metadata keeps the primary notices at the bundle root and may
    # add one file per vendored dependency below ``licenses/``.  Report both
    # locations so runtime-info is sufficient for an acceptance record.
    license_files: list[str] = []
    for name in ("LICENSE", "NOTICE"):
        candidate = root / name
        if candidate.is_file() and not candidate.is_symlink():
            license_files.append(name)
    licenses_root = root / "licenses"
    if licenses_root.is_dir() and not licenses_root.is_symlink():
        license_files.extend(
            item.relative_to(root).as_posix()
            for item in licenses_root.rglob("*")
            if item.is_file() and not item.is_symlink()
        )
    result["license_files"] = sorted(set(license_files))
    return result


def collect_runtime_info(bundle_root: str | Path | None = None) -> dict[str, Any]:
    """Build the acceptance report consumed by launchers and qualification."""

    configured_root = environment_value(os.environ, "SICO_PYTHON_ROOT", ("CAD_PYTHON_ROOT",))
    configured_python = environment_value(os.environ, "SICO_PYTHON", ("CAD_PYTHON",))
    root_real = os.path.realpath(configured_root) if configured_root else None
    configured_python_real = os.path.realpath(configured_python) if configured_python else None
    root_entry = os.path.join(root_real, "bin", "python3") if root_real else None
    root_entry_real = os.path.realpath(root_entry) if root_entry else None
    executable_real = os.path.realpath(sys.executable)
    inside_root = bool(root_real and _is_relative_to(Path(executable_real), Path(root_real)))
    version_info = tuple(int(item) for item in sys.version_info[:3])
    capabilities = detect_capabilities()
    python_paths = _python_paths()
    installed_distributions = _installed_distributions(root_real)
    # Report keys are a persisted qualification schema, not environment exports.
    # Keep the legacy keys until report consumers migrate with a schema revision.
    return {
        "CAD_PYTHON_ROOT": configured_root,
        "CAD_PYTHON": configured_python,
        "sys_executable": sys.executable,
        "realpath_sys_executable": executable_real,
        "realpath_CAD_PYTHON_ROOT": root_real,
        "realpath_CAD_PYTHON": configured_python_real,
        "realpath_CAD_PYTHON_ROOT_entry": root_entry_real,
        "sys_version_info": list(version_info),
        "sys_version": sys.version,
        "expected_python_version": list(EXPECTED_PYTHON_VERSION),
        "version_exact": version_info == EXPECTED_PYTHON_VERSION,
        "executable_inside_root": inside_root,
        "stdlib_inside_root": _stdlib_inside_root(root_real),
        "python_paths": python_paths,
        "stdlib_path": python_paths.get("stdlib"),
        "platstdlib_path": python_paths.get("platstdlib"),
        "purelib_path": python_paths.get("purelib"),
        "platlib_path": python_paths.get("platlib"),
        "python_root_libraries_allowed": bool(
            root_real
            and _path_inside_root(python_paths.get("stdlib"), root_real)
            and _path_inside_root(python_paths.get("purelib"), root_real)
            and _path_inside_root(python_paths.get("platlib"), root_real)
            and _path_inside_root(python_paths.get("libdynload"), root_real)
        ),
        "installed_distributions": installed_distributions,
        "approved_module_probes": {
            "toml": _module_origin("toml", root_real),
            "tomli": _module_origin("tomli", root_real),
            "tomllib": _module_origin("tomllib", root_real),
            "PyQt5": _module_origin("PyQt5", root_real),
        },
        # This is the policy name consumed by acceptance tooling.  It means
        # every library already installed below CAD_PYTHON_ROOT is eligible;
        # it does not imply that every optional library is present.
        "runtime_library_policy": "cad-python-root",
        "CAD_PYTHON_ROOT_absolute": bool(configured_root and os.path.isabs(configured_root)),
        "CAD_PYTHON_ROOT_directory": bool(configured_root and os.path.isdir(configured_root)),
        "CAD_PYTHON_absolute": bool(configured_python and os.path.isabs(configured_python)),
        "CAD_PYTHON_executable_file": bool(
            configured_python
            and os.path.isfile(configured_python)
            and os.access(configured_python, os.X_OK)
        ),
        "CAD_PYTHON_matches_sys_executable": bool(
            configured_python_real and configured_python_real == executable_real
        ),
        "CAD_PYTHON_matches_root_entry": bool(
            configured_python_real
            and root_entry_real
            and configured_python_real == root_entry_real
        ),
        "user_site_packages_enabled": _user_site_enabled(),
        "capabilities": capabilities,
        # Compatibility shims are distinct from libraries installed in the
        # fixed interpreter prefix.  Keep this list for capability accounting.
        "backports": [],
        # Backward-compatible summary: the runtime is intentionally not
        # limited to stdlib; the fixed prefix's installed libraries are valid.
        "stdlib_only": False,
        "bundle": bundle_dependency_info(bundle_root),
    }


def validate_production_runtime(*, require_environment: bool = True) -> dict[str, Any]:
    """Fail closed when the configured production interpreter is unsafe."""

    report = collect_runtime_info()
    if require_environment and (not report["CAD_PYTHON_ROOT"] or not report["CAD_PYTHON"]):
        raise RuntimeError("SICO_PYTHON_ROOT and SICO_PYTHON must be set")
    if not report["CAD_PYTHON_ROOT_absolute"]:
        raise RuntimeError("SICO_PYTHON_ROOT must be an absolute path")
    if not report["CAD_PYTHON_ROOT_directory"]:
        raise RuntimeError("SICO_PYTHON_ROOT is not a directory")
    if not report["CAD_PYTHON_absolute"]:
        raise RuntimeError("SICO_PYTHON must be an absolute path")
    if not report["CAD_PYTHON_executable_file"]:
        raise RuntimeError("SICO_PYTHON is not an executable file")
    if not report["version_exact"]:
        raise RuntimeError("production Python must be exactly 3.9.13")
    if not report["CAD_PYTHON_matches_sys_executable"]:
        raise RuntimeError("sys.executable does not match SICO_PYTHON")
    if not report["CAD_PYTHON_matches_root_entry"]:
        raise RuntimeError("SICO_PYTHON does not match SICO_PYTHON_ROOT/bin/python3")
    if not report["executable_inside_root"]:
        raise RuntimeError("production Python resolves outside SICO_PYTHON_ROOT")
    if not report["stdlib_inside_root"]:
        raise RuntimeError("Python standard library resolves outside SICO_PYTHON_ROOT")
    if not report["python_root_libraries_allowed"]:
        raise RuntimeError("Python libraries do not resolve inside SICO_PYTHON_ROOT")
    if report["user_site_packages_enabled"]:
        raise RuntimeError("user site-packages are enabled; launch with -s/PYTHONNOUSERSITE=1")
    return report


def production_python(*, require_environment: bool = True) -> str:
    """Return the only interpreter allowed for AIVW Python child processes.

    This check is intentionally independent of ``PATH``.  A module imported
    by a test or an embedding application must not silently inherit a foreign
    interpreter merely because the parent process happens to be Python.
    """

    report = validate_production_runtime(require_environment=require_environment)
    configured = report.get("CAD_PYTHON")
    if not isinstance(configured, str) or not configured or not os.path.isabs(configured):
        raise RuntimeError("SICO_PYTHON must be an absolute executable path")
    if not os.path.isfile(configured) or not os.access(configured, os.X_OK):
        raise RuntimeError("SICO_PYTHON is not an executable file")
    # ``validate_production_runtime`` already compares realpath(CAD_PYTHON)
    # with realpath(sys.executable), and checks containment/version.  Return
    # the configured spelling so a symlink such as bin/python3 remains the
    # documented production entry point.
    return configured


# Friendly aliases for callers that use the terminology from the plan.
capability_report = detect_capabilities
get_runtime_info = collect_runtime_info
runtime_info = collect_runtime_info


__all__ = [
    "EXPECTED_PYTHON_VERSION",
    "bundle_dependency_info",
    "capability_report",
    "collect_runtime_info",
    "detect_capabilities",
    "get_runtime_info",
    "production_python",
    "runtime_info",
    "validate_production_runtime",
]
