"""Canonical JSON request data and its stable digest."""

from __future__ import annotations

from typing import Any
from .model_request import NetlistRequest
from .model_entries import CornerExport
from .artifacts import request_digest


def _corner_dict(value: CornerExport) -> dict[str, Any]:
    return {"mode": value.mode, "variable": value.variable, "profiles": [
        {"name": profile.name, "models": [
            {"file": str(m.file), "section": m.section, "label": m.label, "enabled": m.enabled}
            for m in profile.models
        ]} for profile in value.profiles
    ]}


def request_to_dict(request: NetlistRequest) -> dict[str, Any]:
    """Return canonical JSON-compatible data (all paths absolute)."""

    value = request.validate()
    payload = {
        "format": "sico-mts-netlistor-request",
        "schema_version": 1,
        "source": {
            "cds_lib": str(value.source.cds_lib),
            "library": value.source.library,
            "cell": value.source.cell,
            "view": value.source.view,
            "startup_file": "" if value.source.startup_file is None else str(value.source.startup_file),
            "simrc": "" if value.source.simrc is None else str(value.source.simrc),
        },
        "simulator": {"dialect": value.dialect},
        "models": [
            {"enabled": item.enabled, "file": str(item.file), "section": item.section, "label": item.label}
            for item in value.models
        ],
        "process_options": {name: getattr(value.process_options, name) for name in ("temp", "tnom", "scale", "scalem", "reltol", "gmin")},
        "simulator_options": [
            {"enabled": item.enabled, "name": item.name, "value_type": item.value_type, "value": item.value, "enum_values": list(item.enum_values)}
            for item in value.simulator_options
        ],
        "publish": {
            "generate_symbol_view": value.target.generate_symbol_view,
            "generate_netlist_view": value.target.generate_netlist_view,
            "target_library": value.target.library or "",
            "target_cell": value.target.cell or "",
            "overwrite": value.target.overwrite,
            **(
                {"overwrite_symbol_view": True}
                if value.target.overwrite_symbol_view
                else {}
            ),
            **(
                {"overwrite_netlist_view": True}
                if value.target.overwrite_netlist_view
                else {}
            ),
        },
    }
    if value.corner_export != CornerExport():
        payload["corner_export"] = _corner_dict(value.corner_export)
    if value.temperature_mode != "fixed":
        payload["temperature_mode"] = value.temperature_mode
    if value.cell_specs:
        payload["cells"] = [
            {
                "library": spec.library,
                "cell": spec.cell,
                "view": spec.view,
                "dialect": spec.dialect,
                **({"corner_export": _corner_dict(spec.corner_export)} if spec.corner_export != CornerExport() else {}),
                **({"temperature_mode": spec.temperature_mode} if spec.temperature_mode != "fixed" else {}),
                "models": [
                    {"enabled": item.enabled, "file": str(item.file), "section": item.section, "label": item.label}
                    for item in spec.models
                ],
                "process_options": {
                    name: getattr(spec.process_options, name)
                    for name in ("temp", "tnom", "scale", "scalem", "reltol", "gmin")
                },
                "simulator_options": [
                    {"enabled": item.enabled, "name": item.name, "value_type": item.value_type, "value": item.value, "enum_values": list(item.enum_values)}
                    for item in spec.simulator_options
                ],
                "publish": {
                    "generate_symbol_view": spec.target.generate_symbol_view,
                    "generate_netlist_view": spec.target.generate_netlist_view,
                    "target_library": spec.target.library or "",
                    "target_cell": spec.target.cell or "",
                    "overwrite": spec.target.overwrite,
                    **({"overwrite_symbol_view": True} if spec.target.overwrite_symbol_view else {}),
                    **({"overwrite_netlist_view": True} if spec.target.overwrite_netlist_view else {}),
                },
            }
            for spec in value.cell_specs
        ]
    return payload


def canonical_request_json(request: NetlistRequest) -> str:
    import json

    return json.dumps(request_to_dict(request), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def canonical_request_digest(request: NetlistRequest) -> str:
    return request_digest(request_to_dict(request))
