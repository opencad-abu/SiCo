"""New Spectre config with explicit top-level instance bindings and readback."""

import json

from .circuit_gate import skill_value
from .circuit_recipe import NAME, REF, REQUEST
from .circuit_schema import tool
from .circuit_spec_schema import CircuitSpecError, validate

BINDING = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "instance": NAME,
        "library": NAME,
        "cell": NAME,
        "view": {
            **NAME,
            "description": "A saved schematic-type view of this instance's existing master. "
            "Simulator stop views are resolved through switch_views/stop_views, "
            "not explicit bindings.",
        },
    },
    "required": ["instance", "library", "cell", "view"],
}
PROPERTIES = {
    "library": NAME,
    "cell": NAME,
    "view": {**NAME, "default": "config"},
    "top_library": NAME,
    "top_cell": NAME,
    "top_view": {**NAME, "default": "schematic"},
    "bindings": {"type": "array", "items": BINDING, "maxItems": 64, "default": []},
    "switch_views": {
        "type": "array",
        "items": NAME,
        "minItems": 1,
        "maxItems": 16,
        "default": ["spectre", "schematic"],
    },
    "stop_views": {
        "type": "array",
        "items": NAME,
        "minItems": 1,
        "maxItems": 16,
        "default": ["spectre"],
    },
    "task_ref": REF,
    **REQUEST,
}
CONFIG_TOOLS = [
    tool(
        "create_circuit_config",
        "Create a NEW Spectre config in an existing writable work cell. "
        "Accepts switch_views/stop_views; defaults are spectre/schematic and stop spectre. "
        "Up to 64 direct instance bindings to saved schematic-type views within the same master "
        "lib/cell. Omit simulator primitives from bindings; resolve them through the view lists. "
        "Check master interfaces and hdbBind resolution, save/reopen. In foreground "
        "mode show a read-only top schematic bound to this config, without an open-choice dialog. "
        "Does not run, overwrite or certify complete hierarchy. Retry identical request_id only.",
        PROPERTIES,
        ("library", "cell", "top_library", "top_cell", "task_ref", "request_id"),
        True,
    ),
    tool(
        "inspect_circuit_config",
        "Revalidate the created config, bound foreground window, saved top "
        "schematic and direct instance resolutions. Returns direct binding rows and incomplete "
        "full-hierarchy coverage. Does not save user edits, change focus or netlist.",
        {"config_ref": REF},
        ("config_ref",),
    ),
]
CONFIG_NAMES = frozenset(t["name"] for t in CONFIG_TOOLS)


def checked_config(name, args):
    from .circuit_tools import CircuitArgumentError

    schema = next(t["inputSchema"] for t in CONFIG_TOOLS if t["name"] == name)
    try:
        validate(args, schema)
    except CircuitSpecError as exc:
        raise CircuitArgumentError(str(exc)) from exc
    result = {
        key: args.get(key, rule.get("default"))
        for key, rule in schema["properties"].items()
        if key in args or "default" in rule
    }
    if name == "create_circuit_config":
        bindings = result["bindings"]
        if len({row["instance"] for row in bindings}) != len(bindings):
            raise CircuitArgumentError("duplicate instance binding")
        if result["library"] in {"gpdk045", "basic", "analogLib"}:
            raise CircuitArgumentError("config requires a separate work library")
        for key in ("switch_views", "stop_views"):
            if len(set(result[key])) != len(result[key]):
                raise CircuitArgumentError("duplicate " + key)
        if not set(result["stop_views"]) <= set(result["switch_views"]):
            raise CircuitArgumentError("stop_views must occur in switch_views")
        if result["top_view"] in result["stop_views"]:
            raise CircuitArgumentError("top schematic cannot be a stop view")
        if result["top_view"] in ("layout", "config") or any(
            row["view"] in ("layout", "config") for row in bindings
        ):
            raise CircuitArgumentError(
                "schematic design bindings only; actual view type verified live"
            )
    return result


def build_config_skill(name, args):
    a = checked_config(name, args)
    if name == "inspect_circuit_config":
        return "aiConfigInspect(" + json.dumps(a["config_ref"]) + ")"
    values = [
        a["task_ref"],
        a["request_id"],
        a["library"],
        a["cell"],
        a["view"],
        [a["top_library"], a["top_cell"], a["top_view"]],
        [[r["instance"], r["library"], r["cell"], r["view"]] for r in a["bindings"]],
    ]
    if any(key in args for key in ("switch_views", "stop_views")):
        values.extend([a["switch_views"], a["stop_views"]])
    return "aiConfigCreate(" + " ".join(skill_value(v) for v in values) + ")"
