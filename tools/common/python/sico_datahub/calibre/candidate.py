"""Prepare a bounded DRC check against an already-exported candidate layout."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from ..codec import ArtifactRef, artifact_ref
from ..errors import HubError
from ._validation import bbox
from .context import FlowRequest, load_flow_config
from .deck import PreparedFlow, prepare_flow
from .jobs import JobStore

_LAYOUT_SYSTEMS = {
    ".gds": "GDSII",
    ".gds2": "GDSII",
    ".oas": "OASIS",
    ".oasis": "OASIS",
}
_SOURCE_SUFFIXES = {".cdl", ".cir", ".sp", ".spice"}
_VISIBLE_SIDECAR_SUFFIX = ".sico-visible.json"


@dataclass(frozen=True)
class CandidateSpec:
    flow: FlowRequest
    prepared: PreparedFlow
    command: tuple
    deck: Path
    candidate: ArtifactRef
    source: Optional[ArtifactRef]
    region: Optional[tuple]


def _candidate_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise HubError("invalid_arguments", "Candidate layout must be an absolute file")
    system = _LAYOUT_SYSTEMS.get(path.suffix.lower())
    if system is None:
        raise HubError("invalid_arguments", "Candidate layout format is unsupported")
    return path.resolve(), system


def _source_path(value):
    path = Path(value).expanduser()
    if (
        not path.is_absolute()
        or not path.is_file()
        or path.suffix.lower() not in _SOURCE_SUFFIXES
    ):
        raise HubError("invalid_arguments", "Candidate source netlist is unavailable")
    return path.resolve()


def normalize_bbox(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise HubError("invalid_arguments", "Candidate bbox must be an array")
    if len(value) == 4 and all(type(item) in {int, float} for item in value):
        points = ((value[0], value[1]), (value[2], value[3]))
    elif (
        len(value) == 2
        and all(isinstance(point, (list, tuple)) and len(point) == 2 for point in value)
        and all(type(item) in {int, float} for point in value for item in point)
    ):
        points = (tuple(value[0]), tuple(value[1]))
    else:
        raise HubError("invalid_arguments", "Candidate bbox must contain four numbers")
    if (
        not all(math.isfinite(float(item)) for point in points for item in point)
        or points[1][0] < points[0][0]
        or points[1][1] < points[0][1]
    ):
        raise HubError("invalid_arguments", "Candidate bbox is invalid")
    return bbox(points)


def candidate_artifact(value):
    """Validate one candidate path and return its read-only artifact identity."""
    path, _system = _candidate_path(value)
    return artifact_ref(path)


def source_artifact(value):
    """Validate one candidate source netlist and return its artifact identity."""
    return artifact_ref(_source_path(value))


def _is_visible_fragment(path):
    sidecar = Path(str(path) + _VISIBLE_SIDECAR_SUFFIX)
    if not sidecar.is_file():
        return False
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return (
        not isinstance(value, dict)
        or value.get("schema") == "cad.visible_layout_fragment.v1"
        or value.get("lvs_eligible") is False
    )


def _toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if type(value) in {int, float}:
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise HubError("invalid_arguments", "Candidate config contains an unsupported value")


def _render_table(table, prefix=()):
    lines = []
    subtables = []
    for key, value in table.items():
        if value is None:
            continue
        if isinstance(value, dict):
            subtables.append((key, value))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    for key, value in subtables:
        name = ".".join((*prefix, key))
        lines.append(f"[{name}]")
        lines.extend(_render_table(value, (*prefix, key)))
    return lines


def _candidate_config(raw, candidate, source, run_dir, top_cell, source_cell=""):
    raw = json.loads(json.dumps(raw))
    raw.setdefault("run", {})["run_dir"] = str(run_dir)
    section = raw.setdefault("input", {})
    layout = dict(section.get("layout") or {})
    lib = layout.get("lib") or section.get("schematic", {}).get("lib", "")
    layout.update({"cell": top_cell, "view": layout.get("view", "layout")})
    section["type"] = "CDL+GDS"
    section["layout"] = layout
    section["gds"] = {"lib": lib, "cell": top_cell, "file": str(candidate)}
    section["cdl"] = {
        "lib": lib,
        "cell": source_cell or top_cell,
        "file": str(source) if source is not None else "",
    }
    return "\n".join(_render_table(raw)) + "\n"


def _flow_modules():
    try:
        from caddefaults import Defaults
        from drcpy.generator import generate_drc
        from rcepy.default_files import default_files
        from rcepy.gen_lvs import generate_lvs
    except ImportError:
        root = Path(__file__).resolve().parents[4]
        for relative in ("drc/python", "rce/python", "common/python"):
            source = root / relative
            if source.is_dir() and str(source) not in sys.path:
                sys.path.insert(0, str(source))
        from caddefaults import Defaults
        from drcpy.generator import generate_drc
        from rcepy.default_files import default_files
        from rcepy.gen_lvs import generate_lvs
    return Defaults, generate_drc, generate_lvs, default_files


def prepare_candidate(
    flow,
    *,
    store: JobStore,
    run_ref,
    candidate_path,
    source_path=None,
    region=None,
    calibre_executable,
    timeout=180.0,
) -> CandidateSpec:
    """Build a candidate-only deck; the candidate artifact must already be clipped."""
    if flow.kind not in {"drc", "lvs"}:
        raise HubError("invalid_arguments", "Unsupported candidate check kind")
    candidate, layout_system = _candidate_path(candidate_path)
    source = None
    if flow.kind == "lvs":
        if not source_path:
            raise HubError("invalid_arguments", "LVS candidate requires a source netlist")
        if _is_visible_fragment(candidate):
            raise HubError(
                "invalid_arguments",
                "Visible-window fragments are not eligible for LVS",
            )
        source = _source_path(source_path)
    normalized_region = normalize_bbox(region)
    run_dir = store.candidate_run_dir(run_ref)
    for path in (run_dir / "db/drc", run_dir / "log"):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    options = dict(flow.options)
    options["scope"] = "candidate"
    if normalized_region is not None:
        options["candidate_bbox"] = [
            list(normalized_region[0]),
            list(normalized_region[1]),
        ]
    candidate_flow = replace(
        flow,
        run_dir=run_dir,
        log_dir=run_dir / "log",
        db_dir=run_dir / "db",
        layout_cell=flow.top_cell,
        layout_path=str(candidate),
        source_path=str(source) if source is not None else flow.source_path,
        options=options,
    )
    prepared = prepare_flow(
        candidate_flow,
        store=store,
        run_ref=run_ref,
        calibre_executable=calibre_executable,
        timeout=timeout,
        layout_system=layout_system,
    )
    base_config = load_flow_config(prepared.config.path)
    config_text = _candidate_config(
        base_config.to_dict(),
        candidate,
        source,
        run_dir,
        candidate_flow.top_cell,
        candidate_flow.source_cell,
    )
    config_path = store.candidate_config_path(run_ref)
    config_ref = store.write_text(config_path, config_text)
    candidate_config = load_flow_config(config_path)
    try:
        Defaults, generate_drc, generate_lvs, default_files = _flow_modules()
        defaults = Defaults.load(default_files(candidate_config, flow.kind))
        context = candidate_config.context()
        if flow.kind == "drc":
            deck = generate_drc(
                candidate_config,
                context,
                defaults=defaults,
                layout_system=layout_system,
            )
        else:
            deck = generate_lvs(
                candidate_config, context, standalone=True, defaults=defaults
            )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HubError("read_failed", "Candidate DRC deck preparation failed") from exc
    command = [
        str(calibre_executable),
        "-drc" if flow.kind == "drc" else "-lvs",
    ]
    if candidate_config.calibre_run_mode(flow.kind) == "Hier":
        command.extend(["-hier", "-turbo"])
        if flow.kind == "drc":
            command.append(candidate_config.text("runtime", "cpus", default="1"))
        else:
            command.append(candidate_config.lvs_cpus)
    command.append(str(deck))
    preparation = dict(prepared.preparation)
    preparation.update({
        "candidate": True,
        "layout_system": layout_system,
    })
    return CandidateSpec(
        flow=replace(candidate_flow, layout_path=str(candidate)),
        prepared=PreparedFlow(
            config=config_ref,
            effective_deck=prepared.effective_deck,
            preparation=preparation,
        ),
        command=tuple(command),
        deck=deck,
        candidate=artifact_ref(candidate),
        source=artifact_ref(source) if source is not None else None,
        region=normalized_region,
    )


__all__ = [
    "CandidateSpec",
    "candidate_artifact",
    "normalize_bbox",
    "prepare_candidate",
    "source_artifact",
]
