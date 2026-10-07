"""Circuit workflow contracts: reference lookup, design contexts and RC recipes."""
from __future__ import annotations

import json
import math
import re
import uuid

from .circuit_schema import PRESENTATION, string_schema, tool
from .circuit_recipe import RECIPE_TOOLS
from .circuit_gate import GATE_TOOLS
from .circuit_config import CONFIG_TOOLS, CONFIG_NAMES
from .circuit_opamp import OPAMP_TOOLS, OPAMP_NAMES
from .circuit_rebuild import REBUILD_TOOLS, REBUILD_NAMES
from .circuit_matrix import MATRIX_TOOLS, MATRIX_NAMES


class CircuitArgumentError(ValueError):
    pass


TARGET = {k: string_schema() for k in ("library", "cell", "view")}
TARGET["view_type"] = {"type": "string", "enum": ["schematic", "schematicSymbol", "maestro"]}
CONTEXT = {"context_ref": string_schema(128)}
LOOKUP_PROPERTIES = {
    "library": string_schema(), "cell": string_schema(), "query": string_schema(),
    "parameter": string_schema(), "include_parameters": {"type": "boolean", "default": False},
    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
    "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
    "parameter_limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
    "parameter_offset": {"type": "integer", "minimum": 0, "maximum": 10000, "default": 0},
}
CIRCUIT_TOOLS = [
    tool("query_device_catalog", "Query the local analogLib/basic reference snapshot. Returns provenance, "
         "available views, symbol pins and bounded base CDF metadata; not live effective CDF or simulation qualification.",
         LOOKUP_PROPERTIES),
    tool("preflight_circuit_flow", "Read the launching Virtuoso's target/library paths and API/GUI capabilities. "
         "Does not open/create a view, run callbacks, check out licenses or certify simulation readiness.",
         {**TARGET, "presentation": PRESENTATION}, ("library", "cell", "view", "view_type", "presentation")),
    tool("open_design_view", "Open an EXISTING schematic/symbol/Maestro by explicit target. Foreground retains "
         "the editor window; background retains an owned reference until release. Returns context_ref. "
         "Does not create/save a view or run simulation. Reuse request_id after unknown transport status.",
         {**TARGET, "presentation": PRESENTATION,
          "access": {"type": "string", "enum": ["read", "edit"], "default": "read"},
          "request_id": string_schema(96, pattern=r"^[A-Za-z0-9_-]+$")},
         ("library", "cell", "view", "view_type", "request_id", "presentation"), True),
    tool("inspect_design_context", "Revalidate a context_ref against its bound window/session and target. "
         "Does not follow current focus, reopen a closed target, or certify unchanged design contents.",
         CONTEXT, ("context_ref",)),
    tool("release_design_context", "Release only an owned background reference or detach from a foreground "
         "window. Never saves, closes a GUI window or cancels simulation; modified background data is retained.",
         CONTEXT, ("context_ref",), True),
]
CIRCUIT_TOOLS += RECIPE_TOOLS
CIRCUIT_TOOLS += GATE_TOOLS
CIRCUIT_TOOLS += CONFIG_TOOLS
CIRCUIT_TOOLS += OPAMP_TOOLS
CIRCUIT_TOOLS += REBUILD_TOOLS
CIRCUIT_TOOLS += MATRIX_TOOLS
# Keep qualified examples available, with their limited scope visible to agents.
REFERENCE_RECIPE_TOOL_NAMES = frozenset(
    t['name'] for group in (RECIPE_TOOLS, GATE_TOOLS, OPAMP_TOOLS, REBUILD_TOOLS, MATRIX_TOOLS)
    for t in group if t['name'] != 'begin_circuit_task'
)
CIRCUIT_TOOLS = [dict(t, description='Reference validation recipe only. ' + t['description'])
                 if t['name'] in REFERENCE_RECIPE_TOOL_NAMES else t for t in CIRCUIT_TOOLS]
CIRCUIT_TOOL_NAMES = frozenset(t["name"] for t in CIRCUIT_TOOLS)
LIVE_CIRCUIT_TOOLS = CIRCUIT_TOOL_NAMES - {"query_device_catalog", "preview_rc_circuit", "preview_gpdk_gate"}


def checked_arguments(name, arguments):
    if name in MATRIX_NAMES:
        from .circuit_matrix import checked_matrix
        return checked_matrix(name, arguments)
    if name in CONFIG_NAMES:
        from .circuit_config import checked_config
        return checked_config(name, arguments)
    definition = next(t for t in CIRCUIT_TOOLS if t["name"] == name)
    schema = definition["inputSchema"]
    if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]):
        raise CircuitArgumentError("unsupported arguments")
    if set(schema["required"]) - set(arguments):
        raise CircuitArgumentError("missing required arguments")
    result = dict(arguments)
    for key, rule in schema["properties"].items():
        if key not in result:
            if "default" in rule:
                result[key] = rule["default"]
            continue
        value = result[key]
        if rule["type"] == "string":
            if (not isinstance(value, str) or not 1 <= len(value) <= rule.get("maxLength", 256)
                    or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise CircuitArgumentError(key + " must be bounded, trimmed text without control characters")
            if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
                raise CircuitArgumentError(key + " has invalid characters")
            if "enum" in rule and value not in rule["enum"]:
                raise CircuitArgumentError(key + " has unsupported value")
        elif rule["type"] == "boolean":
            if type(value) is not bool:
                raise CircuitArgumentError(key + " must be boolean")
        elif rule["type"] == "number":
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not rule["minimum"] <= value <= rule["maximum"]):
                raise CircuitArgumentError(key + " is outside finite numeric bounds")
        elif type(value) is not int or not rule["minimum"] <= value <= rule["maximum"]:
            raise CircuitArgumentError(key + " is outside integer bounds")
    if name in {"open_design_view", "preflight_circuit_flow"}:
        # ddGetObj interprets whitespace in view names as a search list.
        if any(any(c.isspace() for c in result[key]) or "/" in result[key] for key in TARGET):
            raise CircuitArgumentError("target names must be single DD names, not paths/view lists")
    if name == "query_device_catalog" and (result.get("include_parameters") or result.get("parameter")):
        if not result.get("library") or not result.get("cell"):
            raise CircuitArgumentError("parameter details require exact library and cell")
    return result


def build_circuit_skill(name, arguments):
    args = checked_arguments(name, arguments)
    if name in MATRIX_NAMES:
        from .circuit_matrix import build_matrix_skill
        return build_matrix_skill(name, args)
    if name in REBUILD_NAMES:
        from .circuit_rebuild import build_rebuild_skill
        return build_rebuild_skill(name, args)
    if name in OPAMP_NAMES:
        from .circuit_opamp import build_opamp_skill
        return build_opamp_skill(name, args)
    if name in CONFIG_NAMES:
        from .circuit_config import build_config_skill
        return build_config_skill(name, args)
    if name in {t["name"] for t in GATE_TOOLS}:
        from .circuit_gate import build_gate_skill
        return build_gate_skill(name, args)
    if name in {t["name"] for t in RECIPE_TOOLS}:
        from .circuit_recipe import build_recipe_skill
        return build_recipe_skill(name, args)
    if name in {"inspect_design_context", "release_design_context"}:
        function = "aiCircuitInspect" if name.startswith("inspect") else "aiCircuitRelease"
        values = [args["context_ref"]]
    else:
        function = "aiCircuitPreflight" if name.startswith("preflight") else "aiCircuitOpen"
        values = [args[key] for key in TARGET] + [args["presentation"]]
        if name == "open_design_view":
            values += [args["access"], args["request_id"], "design:" + uuid.uuid4().hex]
    return function + "(" + " ".join(json.dumps(v, ensure_ascii=False) for v in values) + ")"
