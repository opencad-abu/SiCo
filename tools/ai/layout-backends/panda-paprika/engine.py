"""Thin orchestration for the external PANDA Paprika P&R backend.

This module owns only Paprika process invocation, run-directory layout and
artifact verification.  It never reads PDK internals, opens OA, or talks to
Virtuoso.  All technology/PCell inputs are produced by SiCo and passed in as
ordinary files or directories.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_ACTIONS = frozenset({"place", "route", "place_and_route"})
REQUIRED_INPUTS = ("netlist", "techfile", "stdcell", "pcell_bundle",
                   "paramfile", "process_view")


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Config:
    panda_root: Path
    pr_lib_dir: Path | None = None
    shell: str = "/bin/bash"

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        if path is None:
            path = Path(__file__).with_name("config.json")
        try:
            value = __import__("json").loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EngineError("engine_unavailable", "PANDA config is unavailable") from exc
        root = value.get("panda_root")
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise EngineError("engine_unavailable", "panda_root must be an absolute path")
        lib = value.get("pr_lib_dir")
        if lib is not None and (not isinstance(lib, str) or not Path(lib).is_absolute()):
            raise EngineError("engine_unavailable", "pr_lib_dir must be an absolute path")
        shell = value.get("shell", "/bin/bash")
        if not isinstance(shell, str) or not Path(shell).is_file():
            raise EngineError("engine_unavailable", "shell is unavailable")
        return cls(Path(root), Path(lib) if lib else None, shell)

    @property
    def placement(self) -> Path:
        return self.panda_root / "pr_kernel/placement"

    @property
    def routing(self) -> Path:
        return self.panda_root / "pr_kernel/routing"

    @property
    def placement_runner(self) -> Path:
        return self.panda_root / "run_placement.sh"

    @property
    def routing_runner(self) -> Path:
        return self.panda_root / "run_route.sh"


def _absolute(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise EngineError("input_invalid", label + " must be an absolute path")
    return Path(value)


def _artifact(path: Path, workspace: Path, role: str, *, format: str = "gds") -> dict[str, Any]:
    resolved = path.resolve()
    root = workspace.resolve()
    if root not in resolved.parents or not resolved.is_file() or not resolved.stat().st_size:
        raise EngineError("output_invalid", "artifact is missing or empty: " + str(path))
    return {
        "role": role,
        "path": str(resolved.relative_to(root)),
        "format": format,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "size": resolved.stat().st_size,
    }


def _input(request: dict[str, Any], name: str, *, directory: bool = False) -> Path:
    inputs = request.get("inputs")
    value = inputs.get(name) if isinstance(inputs, dict) else None
    if value is None and name == "netlist":
        source = request.get("source")
        value = source.get("path") if isinstance(source, dict) else None
    path = _absolute(value, name)
    if directory and not path.is_dir():
        raise EngineError("input_invalid", name + " is not a directory")
    if not directory and not path.is_file():
        raise EngineError("input_invalid", name + " is not a file")
    return path


def _engine_ready(config: Config, action: str) -> list[str]:
    files = [config.placement_runner, config.routing_runner]
    binaries = [config.placement, config.routing]
    if action in {"place", "place_and_route"}:
        files.append(config.placement_runner)
        binaries.append(config.placement)
    if action in {"route", "place_and_route"}:
        files.append(config.routing_runner)
        binaries.append(config.routing)
    missing = [str(path) for path in files + binaries if not path.is_file()]
    if not os.access(config.shell, os.X_OK):
        missing.append(config.shell)
    return missing


def validate(request: dict[str, Any], config: Config) -> dict[str, Any]:
    action = request.get("action")
    source = request.get("source")
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(action, str) or action not in SUPPORTED_ACTIONS:
        return {
            "ok": True,
            "accepted": False,
            "unsupported_required": ["action"],
            "unsupported_preferred": [],
            "diagnostics": [{"code": "unsupported_action",
                             "message": "unsupported action " + str(action)}],
            "required_inputs": [],
            "estimated_resources": {},
        }
    if not isinstance(source, dict) or source.get("kind") != "netlist":
        diagnostics.append({"code": "unsupported_source",
                            "message": "panda-paprika accepts netlist sources only"})
    for path in _engine_ready(config, action):
        diagnostics.append({"code": "engine_unavailable",
                            "message": "missing executable: " + path})
    required = list(REQUIRED_INPUTS)
    if action == "route":
        required.append("placement_result")
    for name in REQUIRED_INPUTS:
        if name in {"techfile", "stdcell", "paramfile", "netlist", "process_view"}:
            try:
                _input(request, name)
            except EngineError as exc:
                diagnostics.append({"code": exc.code, "message": str(exc)})
        else:
            try:
                _input(request, name, directory=True)
            except EngineError as exc:
                diagnostics.append({"code": exc.code, "message": str(exc)})
    if action == "route":
        try:
            _input(request, "placement_result", directory=True)
        except EngineError as exc:
            diagnostics.append({"code": exc.code, "message": str(exc)})
    accepted = not diagnostics
    return {
        "ok": True,
        "accepted": accepted,
        "unsupported_required": [],
        "unsupported_preferred": [],
        "diagnostics": diagnostics,
        "required_inputs": required if not accepted else [],
        "estimated_resources": {"cpu": 4, "memory_gb": 16},
    }


def _environment(
    request: dict[str, Any], config: Config, workspace: Path, stage: str,
) -> dict[str, str]:
    env = dict(os.environ)
    env["ANALOG_AGENT_PROJECT_ROOT"] = str(config.panda_root)
    env["ANALOG_AGENT_RUN_DIR"] = str(workspace)
    if config.pr_lib_dir is not None:
        env["PR_LIB_DIR"] = str(config.pr_lib_dir)
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = str(config.pr_lib_dir) + (
            ":" + existing if existing else ""
        )
    prefix = "PLACE" if stage == "place" else "ROUTE"
    env[prefix + "_KERNEL_BIN"] = str(
        config.placement if stage == "place" else config.routing
    )
    env[prefix + "_TECHFILE"] = str(_input(request, "techfile"))
    env[prefix + "_PARAMFILE"] = str(_input(request, "paramfile"))
    env[prefix + "_NETLIST"] = str(_input(request, "netlist"))
    env[prefix + "_PCELLDIR"] = str(_input(request, "pcell_bundle", directory=True))
    env[prefix + "_STDCELL"] = str(_input(request, "stdcell"))
    result_name = "placement_result" if stage == "place" else "route_result"
    env[prefix + "_RESULT"] = str(workspace / "output" / result_name)
    env[prefix + "_LOG"] = str(workspace / "output" / ("run_" + stage + ".log"))
    for name in ("sym", "placepr", "multi_sym", "symmetric_net"):
        value = request.get("inputs", {}).get(name)
        if isinstance(value, str):
            env[prefix + "_" + name.upper()] = value
    if stage == "route":
        result = request.get("inputs", {}).get("placement_result")
        result_dir = Path(result) if isinstance(result, str) else (
            workspace / "output/placement_result"
        )
        env["ROUTE_PLACEPR"] = str(result_dir / "placepr.json")
        env["ROUTE_DB_SIZE_JSON"] = str(result_dir / "db_size_information.json")
    return env


def _run_stage(
    request: dict[str, Any], config: Config, workspace: Path, stage: str,
    process_ref: list[subprocess.Popen],
) -> None:
    runner = config.placement_runner if stage == "place" else config.routing_runner
    log_path = workspace / "output" / ("run_" + stage + ".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [config.shell, str(runner)],
            cwd=str(workspace),
            env=_environment(request, config, workspace, stage),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        process_ref.append(process)
        try:
            code = process.wait()
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            raise
    if code != 0:
        raise EngineError(
            "engine_failed",
            f"{stage} returned {code}; see {log_path.relative_to(workspace)}",
        )


def _gds(directory: Path, workspace: Path, role: str) -> dict[str, Any]:
    files = sorted(directory.glob("*.gds"))
    if not files:
        files = sorted(directory.glob("**/*.gds"))
    if not files:
        raise EngineError("output_invalid", "no GDS artifact in " + str(directory))
    return _artifact(files[0], workspace, role)


def _netlist_top(netlist: Path) -> str:
    try:
        text = netlist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "TOP"
    match = re.search(r"(?im)^\s*\.subckt\s+(\S+)", text)
    return match.group(1) if match else "TOP"


def _process_units(request: dict[str, Any]) -> dict[str, Any]:
    path = _input(request, "process_view")
    try:
        view = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineError("input_invalid", "process_view is not valid JSON") from exc
    database_unit = view.get("database_unit")
    dbu_per_um = view.get("dbu_per_um")
    stream_unit = view.get("stream_unit_m")
    if not isinstance(database_unit, (int, float)) or database_unit <= 0:
        raise EngineError("input_invalid",
                          "process_view.database_unit is required")
    if not isinstance(dbu_per_um, (int, float)) or dbu_per_um <= 0:
        raise EngineError("input_invalid",
                          "process_view.dbu_per_um is required")
    if stream_unit is None:
        stream_unit = float(database_unit) * float(dbu_per_um)
    if not isinstance(stream_unit, (int, float)) or stream_unit <= 0:
        raise EngineError("input_invalid",
                          "process_view.stream_unit_m is invalid")
    resolved_dbu = float(stream_unit) / float(database_unit)
    if abs(resolved_dbu - float(dbu_per_um)) > 1e-9:
        raise EngineError(
            "input_invalid",
            "process_view units disagree: dbu_per_um vs stream_unit_m/database_unit",
        )
    return {
        "database_unit": float(database_unit),
        "dbu_per_um": int(round(float(dbu_per_um))),
        "stream_unit_m": float(stream_unit),
        "stream_precision_m": float(database_unit),
        "grid_um": view.get("grid_um"),
        "source": str(path),
    }


def _placement_bbox_um(result_dir: Path, dbu_per_um: int) -> list[float]:
    """Return the placed-cell bbox in um from PANDA's gp/db json.

    PANDA writes gp.json positions and db_size_information dimensions in nm.
    """
    try:
        gp = json.loads((result_dir / "gp.json").read_text(encoding="utf-8"))
        db = json.loads(
            (result_dir / "db_size_information.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return [0.0, 0.0, 0.0, 0.0]
    cells = {
        str(row.get("name")): row
        for row in db.get("cells", [])
        if isinstance(row, dict) and row.get("name")
    }
    xs: list[float] = []
    ys: list[float] = []
    for name, origin in gp.items():
        if not isinstance(origin, list) or len(origin) < 2:
            continue
        cell = cells.get(str(name), {})
        x = float(origin[0])
        y = float(origin[1])
        w = float(cell.get("width", 0))
        h = float(cell.get("height", 0))
        xs.extend((x, x + w))
        ys.extend((y, y + h))
    if not xs or not ys:
        return [0.0, 0.0, 0.0, 0.0]
    scale = float(dbu_per_um)
    return [min(xs) / scale, min(ys) / scale,
            max(xs) / scale, max(ys) / scale]


def _terminal_map(request: dict[str, Any], workspace: Path) -> Path | None:
    inputs = request.get("inputs")
    value = inputs.get("terminal_map") if isinstance(inputs, dict) else None
    if not isinstance(value, str) or not Path(value).is_file():
        return None
    target = workspace / "output" / "terminal_map.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(value, target)
    return target


def _write_geometry_manifest(
    request: dict[str, Any],
    workspace: Path,
    result_dir: Path,
    *,
    stage: str,
) -> tuple[Path, Path | None]:
    units = _process_units(request)
    terminal = _terminal_map(request, workspace)
    manifest = {
        "schema": "sico.layout.geometry.v1",
        "top": _netlist_top(_input(request, "netlist")),
        "format": "gds",
        "dbu_per_um": units["dbu_per_um"],
        "bbox_um": _placement_bbox_um(result_dir, units["dbu_per_um"]),
        "hierarchy": "flat",
        "terminal_map": (
            str(terminal.relative_to(workspace)) if terminal is not None else None
        ),
        "stage": stage,
    }
    path = result_dir / "geometry_manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path, terminal


def execute(
    request: dict[str, Any],
    config: Config,
    workspace: Path,
    *,
    process_ref: list[subprocess.Popen],
) -> list[dict[str, Any]]:
    action = request.get("action")
    if action not in SUPPORTED_ACTIONS:
        raise EngineError("unsupported_action", "unsupported action")
    output = workspace / "output"
    output.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    if action in {"place", "place_and_route"}:
        _run_stage(request, config, workspace, "place", process_ref)
        result_dir = output / "placement_result"
        geometry_manifest, terminal = _write_geometry_manifest(
            request, workspace, result_dir, stage="place"
        )
        artifact = _gds(result_dir, workspace, "placement_layout")
        artifact["metadata"] = {
            "geometry_manifest": str(geometry_manifest.relative_to(workspace)),
        }
        artifacts.append(artifact)
        if terminal is not None:
            artifacts.append(_artifact(terminal, workspace, "terminal_map",
                                       format="json"))
        artifacts.append(_artifact(geometry_manifest, workspace, "report",
                                   format="json"))
    if action in {"route", "place_and_route"}:
        _run_stage(request, config, workspace, "route", process_ref)
        result_dir = output / "route_result"
        geometry_manifest, terminal = _write_geometry_manifest(
            request, workspace, result_dir, stage="route"
        )
        artifact = _gds(result_dir, workspace, "layout")
        artifact["metadata"] = {
            "geometry_manifest": str(geometry_manifest.relative_to(workspace)),
        }
        artifacts.append(artifact)
        if terminal is not None:
            artifacts.append(_artifact(terminal, workspace, "terminal_map",
                                       format="json"))
        artifacts.append(_artifact(geometry_manifest, workspace, "report",
                                   format="json"))
    for name in ("run_place.log", "run_route.log"):
        path = output / name
        if path.is_file() and path.stat().st_size:
            artifacts.append(_artifact(path, workspace, "log", format="text"))
    return artifacts


__all__ = ["SUPPORTED_ACTIONS", "Config", "EngineError", "execute", "validate"]
