"""Read-only preflight for a future physical PVT adapter.

The preflight verifies approved tool paths, model containment, and a fresh
payload output boundary.  It deliberately does not launch Spectre/AMS, create
directories, inspect PSF, or establish a design verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .pvt_contract import (
    PVT_BLOCKED_ENVIRONMENT,
    PVT_BLOCKED_INPUT,
    PVT_READY,
    PVT_STALE_ARTIFACT,
    normalize_physical_pvt_matrix,
)


def preflight_physical_pvt(
    contract: object,
    *,
    tools: Mapping[str, str],
    model_root: Path,
    payload_root: Path,
) -> dict[str, Any]:
    """Return a static eligibility record for a registered PVT adapter."""

    try:
        normalized = normalize_physical_pvt_matrix(contract)
    except (TypeError, ValueError) as exc:
        return {
            "status": PVT_BLOCKED_INPUT,
            "code": "pvt_contract_invalid",
            "reason": str(exc),
        }
    if not isinstance(tools, Mapping):
        return {
            "status": PVT_BLOCKED_ENVIRONMENT,
            "code": "pvt_tool_map_invalid",
            "reason": "qualified tool map must be an object",
        }

    simulator = normalized["execution"]["simulator"]
    required_tools = ("spectre",) if simulator == "spectre" else ("runams", "spectre")
    missing_tools: list[str] = []
    invalid_tools: list[str] = []
    for name in required_tools:
        raw_path = tools.get(name)
        if not isinstance(raw_path, str) or not raw_path:
            missing_tools.append(name)
            continue
        path = Path(raw_path)
        if not _approved_tool(path):
            invalid_tools.append(name)
    if missing_tools or invalid_tools:
        return {
            "status": PVT_BLOCKED_ENVIRONMENT,
            "code": "pvt_tools_unavailable",
            "missing_tools": missing_tools,
            "invalid_tools": invalid_tools,
            "required_tools": list(required_tools),
        }

    model_root_error = _directory_error(model_root, "model_root")
    if model_root_error is not None:
        return {
            "status": PVT_BLOCKED_ENVIRONMENT,
            "code": "pvt_model_root_unavailable",
            "reason": model_root_error,
        }
    payload_error = _directory_error(payload_root, "payload_root")
    if payload_error is not None:
        return {
            "status": PVT_BLOCKED_ENVIRONMENT,
            "code": "pvt_payload_root_unavailable",
            "reason": payload_error,
        }

    model_file = model_root / Path(normalized["model_binding"]["model_file"])
    model_error = _contained_file_error(model_root, model_file, "model file")
    if model_error is not None:
        return {
            "status": PVT_BLOCKED_ENVIRONMENT,
            "code": "pvt_model_file_unavailable",
            "reason": model_error,
            "model_file": normalized["model_binding"]["model_file"],
        }

    output_relative = normalized["execution"]["payload_relative_root"]
    output_root = payload_root / Path(output_relative)
    if _has_symlink_component(payload_root, output_root) or output_root.exists():
        return {
            "status": PVT_STALE_ARTIFACT,
            "code": "pvt_output_stale",
            "reason": "physical PVT output root already exists or contains a symlink",
            "output_root": output_relative,
        }

    return {
        "status": PVT_READY,
        "scope": "physical_pvt_contract_preflight",
        "physical_pvt_execution": "not_invoked",
        "design_verdict": "NOT_ESTABLISHED",
        "adapter_registration": "pending",
        "tool_identity_qualification": "not_performed_by_preflight",
        "model_section_verification": "pending_adapter",
        "simulator": simulator,
        "required_tools": list(required_tools),
        "model_file": normalized["model_binding"]["model_file"],
        "model_sections": dict(normalized["model_binding"]["section_by_value"]),
        "output_root": output_relative,
        "expected_point_count": normalized["expected_point_count"],
    }


def _approved_tool(path: Path) -> bool:
    """Require an absolute, non-symlink, regular executable file."""

    return (
        path.is_absolute()
        and path.is_file()
        and not path.is_symlink()
        and _is_executable(path)
    )


def _is_executable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


def _directory_error(path: Path, label: str) -> str | None:
    if not isinstance(path, Path) or not path.is_absolute():
        return f"{label} must be an absolute path"
    if not path.is_dir() or path.is_symlink():
        return f"{label} is missing or not a regular directory: {path}"
    if _has_symlink_component(path, path):
        return f"{label} contains a symlink component: {path}"
    return None


def _contained_file_error(root: Path, path: Path, label: str) -> str | None:
    try:
        physical_root = root.resolve(strict=False)
        physical = path.resolve(strict=False)
        if not physical.is_relative_to(physical_root):
            return f"{label} escaped its approved root"
    except (OSError, ValueError):
        return f"{label} could not be resolved"
    if path.is_symlink() or not path.is_file():
        return f"{label} is missing or not a regular file: {path}"
    if _has_symlink_component(root, path):
        return f"{label} contains a symlink component: {path}"
    return None


def _has_symlink_component(root: Path, candidate: Path) -> bool:
    """Detect direct or intermediate symlinks without resolving first."""

    try:
        root_absolute = root if root.is_absolute() else Path.cwd() / root
        candidate_absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
        relative = candidate_absolute.relative_to(root_absolute)
    except (OSError, ValueError):
        return True
    if root_absolute.is_symlink():
        return True
    current = root_absolute
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return candidate.is_symlink()


__all__ = ["preflight_physical_pvt"]
