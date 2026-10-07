"""Prepare controlled Xcelium options and probe scripts."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from .xcelium_evidence_contract import ADAPTER_NAME, ADAPTER_VERSION, EvidencePreparation
from .xcelium_evidence_routes import qualified_model_parameter_routes


_SAFE_TCL_PATH = re.compile(r"^[^{}\r\n]+$")


def prepare_simulation(
    payload_root: Path,
    smoke_root: Path,
    plan: Mapping[str, Any],
    binding: Mapping[str, Mapping[str, Any]],
    *, adapter_name: str = ADAPTER_NAME, adapter_version: str = ADAPTER_VERSION,
) -> EvidencePreparation:
    """Create only adapter-owned files and fixed Xcelium arguments."""
    evidence_root = payload_root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    compile_args: list[str] = []
    simulation_args: list[str] = []
    artifacts: list[Path] = []
    routes = qualified_model_parameter_routes(plan)
    coverage = plan.get("coverage")
    if isinstance(coverage, Mapping):
        if coverage.get("backend") != "ucis":
            raise ValueError(
                "only raw Xcelium UCIS output is qualified for simulation"
            )
        coverage_path = payload_root / "evidence" / "coverage"
        compile_args.extend(("-coverage", "U"))
        simulation_args.extend(
            (
                "-coverage",
                "U",
                "-covoverwrite",
                "-covworkdir",
                str(coverage_path),
                "-covtest",
                "aivw_l1",
            )
        )
    waveform = plan.get("waveform")
    if isinstance(waveform, Mapping) and waveform.get("retention") == "always":
        waveform_path = payload_root / "evidence" / "waves.shm"
        tcl = smoke_root / "xcelium-evidence.tcl"
        tcl.write_text(
            "database -open waves -into "
            + tcl_path(waveform_path)
            + " -default\n"
            + "probe -create -database waves -all -memories -depth all\n"
            + "run\n",
            encoding="utf-8",
        )
        simulation_args.extend(("-input", str(tcl)))
        artifacts.append(tcl)
    return EvidencePreparation(
        compile_args=tuple(compile_args),
        simulation_args=tuple(simulation_args),
        artifacts=tuple(artifacts),
        metadata={
            "adapter": adapter_name,
            "adapter_version": adapter_version,
            "qualification_scope": "single comparator raw-evidence adapter qualification",
            "coverage_mode": "xcelium_raw_ucd_ucm"
            if isinstance(coverage, Mapping)
            else None,
            "coverage_analysis": "not_run_imc_merge"
            if isinstance(coverage, Mapping)
            else None,
            "waveform_probe": "xcelium_shm_tcl"
            if isinstance(waveform, Mapping)
            and waveform.get("retention") == "always"
            else "runtime_conditional_shm"
            if isinstance(waveform, Mapping)
            and waveform.get("retention") == "on_failure"
            else None,
            "controlled_compile_arguments": list(compile_args),
            "controlled_simulation_arguments": list(simulation_args),
            "matrix_point_count": (
                plan.get("matrix", {}).get("expected_point_count")
                if isinstance(plan.get("matrix"), Mapping)
                else None
            ),
            "matrix_execution_scope": matrix_execution_scope(
                plan, routes
            ),
            "model_parameter_routing": (
                {
                    "instance_count": routes["instance_count"],
                    "dimensions": routes["dimensions"],
                    "instances": [
                        {
                            "name": instance["name"],
                            "parameters": dict(instance["parameters"]),
                            "sv_overrides": dict(instance["overrides"]),
                            "case_ids": list(instance["case_ids"]),
                        }
                        for instance in routes["instances"]
                    ],
                }
                if routes is not None
                else None
            ),
        },
    )


def matrix_execution_scope(
    plan: Mapping[str, Any], routes: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Describe what each matrix coordinate actually drives.

        The recipe owns the Cartesian product, but a comparator adapter only
        qualifies registered model-parameter overrides.  Context and stimulus
        dimensions remain auditable case metadata; they do not select a
        foundry model, Spectre corner, or other physical PVT execution.
        """
    matrix = plan.get("matrix")
    if not isinstance(matrix, Mapping):
        return None
    route_parameters = {
        str(item.get("name")): str(item.get("sv_parameter"))
        for item in (routes or {}).get("dimensions", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("sv_parameter"), str)
    }
    dimensions: list[dict[str, Any]] = []
    for item in matrix.get("dimensions", []):
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        application = item.get("application")
        if not isinstance(name, str) or not isinstance(application, str):
            continue
        raw_values = item.get("values", [])
        dimension: dict[str, Any] = {
            "name": name,
            "source": item.get("source"),
            "application": application,
            "values": list(raw_values) if isinstance(raw_values, list) else [],
        }
        sv_parameter = route_parameters.get(name)
        if application == "model_parameter" and sv_parameter is not None:
            dimension["execution"] = "controlled_sv_parameter_override"
            dimension["sv_parameter"] = sv_parameter
        else:
            dimension["execution"] = "case_metadata_only"
        dimensions.append(dimension)
    return {
        "kind": "recipe_owned_exact_cross_product",
        "expected_point_count": matrix.get("expected_point_count"),
        "physical_pvt_execution": "not_invoked",
        "dimensions": dimensions,
    }


def preflight_tools(tools: Mapping[str, str], plan: Mapping[str, Any]) -> dict[str, Any] | None:
    coverage = plan.get("coverage")
    if isinstance(coverage, Mapping) and coverage.get("backend") == "imc":
        imc = tools.get("imc", "")
        if not imc or not Path(imc).is_file():
            return {
                "status": "BLOCKED_ENVIRONMENT",
                "code": "imc_unavailable",
                "reason": "IMC executable is unavailable; UCIS/IMC analysis is not qualified",
            }
        return {
            "status": "BLOCKED_ENVIRONMENT",
            "code": "imc_analysis_unqualified",
            "reason": "IMC merge/analysis adapter is not qualified",
        }
    return None


def tcl_path(path: Path) -> str:
    value = str(path)
    if not _SAFE_TCL_PATH.fullmatch(value):
        raise ValueError("unsafe Xcelium Tcl path")
    return "{" + value + "}"
