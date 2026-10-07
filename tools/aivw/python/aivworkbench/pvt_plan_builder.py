"""Build controlled physical PVT execution plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .pvt_contract import (PVT_BLOCKED_ENVIRONMENT, PVT_BLOCKED_INPUT, PVT_READY, PVT_STALE_ARTIFACT)
from .pvt_contract_request import normalize_contract_with_optional_points
from .pvt_launcher import PhysicalPVTLauncherRegistry
from .pvt_model_sections import verify_approved_model_sections
from .pvt_paths import approved_directory, approved_file, approved_executable, approved_model_path, ensure_contained, path_exists_or_has_symlink
from .pvt_plan_models import PVTLaunchContext, PVTLaunchPlan, PVTPointPlan
from .pvt_command import validate_command
from .pvt_preflight import preflight_physical_pvt

def build_physical_pvt_plan(
    contract: object,
    *,
    registry: PhysicalPVTLauncherRegistry,
    tools: Mapping[str, str],
    model_root: Path,
    payload_root: Path,
    deck_root: Path,
    cases: Sequence[Mapping[str, Any]] | None = None,
) -> PVTLaunchPlan:
    """Build a controlled per-point plan without invoking an EDA tool.

    The function is the adapter hand-off boundary.  Every non-``READY`` path
    returns an explicit blocked/stale result before a command builder is
    called.  A ready plan contains commands and expected locators only; the
    caller must still execute and evaluate each point in a separate layer.
    """

    if not isinstance(tools, Mapping):
        return _blocked(
            PVT_BLOCKED_ENVIRONMENT,
            "pvt_tool_map_invalid",
            "qualified tool map must be an object",
        )
    for root, label in (
        (model_root, "model_root"),
        (payload_root, "payload_root"),
        (deck_root, "deck_root"),
    ):
        if not isinstance(root, Path):
            return _blocked(
                PVT_BLOCKED_ENVIRONMENT,
                "pvt_path_type_invalid",
                f"{label} must be a Path",
            )
    try:
        normalized = normalize_contract_with_optional_points(contract, cases)
    except (TypeError, ValueError) as exc:
        return _blocked(PVT_BLOCKED_INPUT, "pvt_contract_invalid", str(exc))
    if not isinstance(registry, PhysicalPVTLauncherRegistry):
        return _blocked(
            PVT_BLOCKED_ENVIRONMENT,
            "pvt_launcher_registry_invalid",
            "physical PVT launcher registry is unavailable",
        )
    launcher_name = normalized["execution"]["launcher"]
    launcher = registry.get(launcher_name)
    if launcher is None:
        return _blocked(
            PVT_BLOCKED_ENVIRONMENT,
            "pvt_adapter_unregistered",
            f"no physical PVT launcher is registered for {launcher_name}",
            adapter=launcher_name,
        )
    if (
        launcher.simulator != normalized["execution"]["simulator"]
        or launcher.raw_output != normalized["execution"]["raw_output"]
    ):
        return _blocked(
            PVT_BLOCKED_INPUT,
            "pvt_adapter_contract_mismatch",
            "registered launcher does not match the recipe simulator/raw_output",
            adapter=launcher.name,
        )

    missing_launcher_tools: list[str] = []
    invalid_launcher_tools: list[str] = []
    for tool_name in launcher.required_tools:
        raw_tool = tools.get(tool_name)
        if not isinstance(raw_tool, str) or not raw_tool:
            missing_launcher_tools.append(tool_name)
            continue
        tool_path = Path(raw_tool)
        if not approved_executable(tool_path):
            invalid_launcher_tools.append(tool_name)
    if missing_launcher_tools or invalid_launcher_tools:
        return _blocked(
            PVT_BLOCKED_ENVIRONMENT,
            "pvt_launcher_tools_unavailable",
            "one or more launcher-declared tools are unavailable or unsafe",
            adapter=launcher.name,
            missing_tools=missing_launcher_tools,
            invalid_tools=invalid_launcher_tools,
            required_tools=list(launcher.required_tools),
        )

    # ``normalize_physical_pvt_matrix`` returns a derived ``points`` table for
    # adapter consumers, while the public preflight accepts the recipe-shaped
    # contract.  Keep that derived field out of the preflight input rather than
    # widening the recipe contract's accepted keys.
    preflight_contract = dict(normalized)
    preflight_contract.pop("points", None)
    try:
        preflight = preflight_physical_pvt(
            preflight_contract,
            tools=tools,
            model_root=model_root,
            payload_root=payload_root,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        # A filesystem race (including a symlink loop) is an environment
        # block, never permission to continue toward simulator execution.
        return _blocked(
            PVT_BLOCKED_ENVIRONMENT,
            "pvt_preflight_unavailable",
            f"physical PVT preflight could not complete: {exc}",
        )
    if preflight.get("status") != PVT_READY:
        return PVTLaunchPlan(
            str(preflight.get("status", PVT_BLOCKED_ENVIRONMENT)),
            {
                **preflight,
                "adapter": launcher.name,
                "adapter_version": launcher.version,
            },
        )

    model_path = approved_model_path(model_root, normalized)
    if isinstance(model_path, str):
        return _blocked(PVT_BLOCKED_ENVIRONMENT, "pvt_model_file_unavailable", model_path)
    section_check = verify_approved_model_sections(
        model_path, normalized["model_binding"]["section_by_value"]
    )
    if section_check["status"] != "PASS":
        return _blocked(
            PVT_BLOCKED_INPUT,
            "pvt_model_sections_unverified",
            "approved model file does not declare every mapped section",
            model_sections=section_check,
        )

    deck_error = approved_directory(deck_root, "deck_root")
    if deck_error is not None:
        return _blocked(PVT_BLOCKED_ENVIRONMENT, "pvt_deck_root_unavailable", deck_error)

    output_relative = normalized["execution"]["payload_relative_root"]
    output_root = payload_root / Path(output_relative)
    points: list[PVTPointPlan] = []
    try:
        for raw_point in normalized["points"]:
            point_id = raw_point["id"]
            deck_path = deck_root / f"{point_id}.deck"
            deck_error = approved_file(deck_root, deck_path, f"deck for {point_id}")
            if deck_error is not None:
                return _blocked(
                    PVT_BLOCKED_ENVIRONMENT,
                    "pvt_deck_unavailable",
                    deck_error,
                    point_id=point_id,
                )
            output_path = output_root / point_id / normalized["execution"]["raw_output"]
            if path_exists_or_has_symlink(payload_root, output_path):
                return _blocked(
                    PVT_STALE_ARTIFACT,
                    "pvt_output_stale",
                    f"output path already exists or contains a symlink: {output_path}",
                    point_id=point_id,
                )
            ensure_contained(payload_root, output_path, "physical PVT output")
            context = PVTLaunchContext(
                contract=normalized,
                point=raw_point,
                model_path=model_path,
                model_section=str(raw_point["model_section"]),
                deck_path=deck_path.resolve(),
                output_path=output_path,
                model_root=model_root.resolve(),
                deck_root=deck_root.resolve(),
                payload_root=payload_root.resolve(),
                tools=dict(tools),
            )
            try:
                raw_command = launcher.command_builder(context)
            except Exception as exc:
                return _blocked(
                    PVT_BLOCKED_INPUT,
                    "pvt_command_builder_failed",
                    f"launcher command builder failed: {exc}",
                    point_id=point_id,
                )
            command_error = validate_command(
                raw_command,
                launcher,
                context,
            )
            if command_error is not None:
                return _blocked(
                    PVT_BLOCKED_INPUT,
                    "pvt_command_unqualified",
                    command_error,
                    point_id=point_id,
                )
            points.append(
                PVTPointPlan(
                    point_id=point_id,
                    coordinates=dict(raw_point["coordinates"]),
                    model_file=str(raw_point["model_file"]),
                    model_section=str(raw_point["model_section"]),
                    deck_path=deck_path.resolve(),
                    output_path=output_path,
                    command=tuple(str(item) for item in raw_command),
                )
            )
    except (OSError, RuntimeError, ValueError) as exc:
        return _blocked(PVT_BLOCKED_ENVIRONMENT, "pvt_path_contract_invalid", str(exc))

    producer = f"{launcher.name}@{launcher.version}"
    try:
        expected_locators = tuple(
            point.expected_locator(payload_root, producer)
            for point in points
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _blocked(
            PVT_BLOCKED_ENVIRONMENT,
            "pvt_locator_path_unavailable",
            f"physical PVT output paths could not be resolved: {exc}",
        )
    summary = {
        **preflight,
        "status": PVT_READY,
        "adapter": launcher.name,
        "adapter_version": launcher.version,
        "adapter_registration": "registered",
        "model_section_verification": section_check,
        "physical_pvt_execution": "not_invoked",
        "design_verdict": "NOT_ESTABLISHED",
        "qualification_scope": "physical_pvt_adapter_contract_only",
        "point_count": len(points),
        "expected_point_count": normalized["expected_point_count"],
        "expected_raw_outputs": [dict(item) for item in expected_locators],
        "raw_locator_policy": "publish_only_after_runtime_output_is_verified",
    }
    # ``locators`` is intentionally empty at planning time.  The manifest
    # contract requires ``exists=true`` and a real file/directory; runtime
    # evaluation returns publishable locators after those checks pass.
    return PVTLaunchPlan(PVT_READY, summary, tuple(points), ())
def _blocked(status: str, code: str, reason: str, **details: Any) -> PVTLaunchPlan:
    summary: dict[str, Any] = {
        "status": status,
        "code": code,
        "reason": reason,
        "physical_pvt_execution": "not_invoked",
        "design_verdict": "NOT_ESTABLISHED",
        "qualification_scope": "physical_pvt_adapter_contract_only",
    }
    summary.update(details)
    return PVTLaunchPlan(status, summary)


__all__ = ["build_physical_pvt_plan"]
