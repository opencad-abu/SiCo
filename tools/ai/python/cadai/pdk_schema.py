"""Public, read-only current-session PDK discovery contract (no PDK rules)."""
from __future__ import annotations

import json
from .pdk_errors import PdkArgumentError, PdkUnavailable

SCHEMA = "cad.pdk.session.v1"
SECTIONS = ("identity", "ports", "parameters", "geometry", "simulators", "callbacks")
MAX_RESPONSE_BYTES = 90_000
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOTS = 8
MAX_CACHE_BYTES = 32 * 1024 * 1024


def _text(maximum=256):
    return {"type": "string", "minLength": 1, "maxLength": maximum}


def _enum(*values):
    return {"type": "string", "enum": list(values), "minLength": 1, "maxLength": 64}


_PAGE = {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
_REF = _text(128)
_COMMON = {"snapshot_ref": _REF, "cursor": _text(4096), "page_size": _PAGE}
_SEARCH = {key: _text() for key in ("library", "cell", "view", "query", "parameter", "kind", "family")}
_SEARCH["view"] = {**_text(), "default": "symbol"}
_SEARCH["category"] = _text()  # PDK category registry name (ddCat*)
_SEARCH["tier"] = _enum("interface", "derived", "auxiliary")
_SEARCH["range"] = _text()  # parameter with a published min/max range
_SEARCH["readiness"] = _enum("ready_for_design", "partial")
TIERS = ("interface", "all")


def _tool(name, description, properties, required=()):
    return {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "properties": properties,
                        "required": list(required), "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": False, "openWorldHint": False},
    }


PDK_TOOLS = [
    _tool("get_pdk_preparation", "Inspect PDK data availability before circuit design. Read library "
          "bindings without scanning devices. analogLib/basic use built-in data without configuration. "
          "For process libraries prefer SICO_PDK_DATA, then the tool-managed private "
          "workspace data. Supply target_library for an explicit existing design library. "
          "Report pdk_path_changed notices and reuse intact relocated data without asking permission or collecting. "
          "Use its live technology binding directly; ask only for an unbound/new design whose "
          "process is undetermined. Existing instance/CDF queries do not require this tool.",
          {"view": _SEARCH["view"], "target_library": _text(),
           "include_base_libraries": {"type": "boolean", "default": False}}),
    _tool("prepare_pdk_data", "Prepare a separate PDK collection subtask before device search/design. "
          "analogLib/basic always use the built-in database, including refresh. "
          "Reuse qualified standard publications without collection. A capture-ready result is not design readiness. "
          "Use a separate generation workflow to complete and publish missing data. "
          "Reuse complete validated metadata first; a changed PDK installation path produces a notice "
          "and reuses the data with current paths, without collection. Otherwise automatically collect the directory, "
          "full parameters, ports, geometry, simulator and callback metadata into the tool-managed private workspace store. "
          "Missing/stale configured data does not require CAD support. Return a collection_ref to advance in "
          "bounded batches. With multiple PDKs, library and choice_confirmed require an actual user "
          "choice unless the target library already has a live technology binding. "
          "This does not select foreground/background. "
          "Interrupted read-only collection resumes automatically with bounded retries. "
          "refresh restarts collection; respect user cancellation.",
          {"library": _text(), "view": _SEARCH["view"], "target_library": _text(),
           "choice_confirmed": {"type": "boolean", "default": False},
           "refresh": {"type": "boolean", "default": False}}),
    _tool("advance_pdk_collection", "Advance one bounded batch of the separate PDK data subtask. "
          "Repeat with the returned next_batch only while collecting; ready permits device search. "
          "Reusing a completed batch token returns progress without replaying it. Failure or "
          "cancellation never publishes incomplete device coverage. Retry transient failures using "
          "prepare_pdk_data; cancelled tasks require deliberate refresh. Metadata limitations stay explicit.",
          {"collection_ref": _REF, "next_batch": {"type": "integer", "minimum": 0,
          "maximum": 40000}},
          ("collection_ref", "next_batch")),
    _tool("cancel_pdk_collection", "Cancel the retained PDK collection subtask without publishing "
          "partial data or changing the circuit task's presentation mode. Does not stop Virtuoso.",
          {"collection_ref": _REF}, ("collection_ref",)),
    _tool("search_pdk_devices", "Legacy session-adapter metadata discovery; results do not grant design usage. Use get_pdk_data for standard candidate selection. "
          "Search a prepared PDK directory without scanning libraries. "
          "First get_pdk_preparation and prepare_pdk_data; finish the collection subtask if needed. "
          "Missing data automatically starts a collection subtask; follow next_action until ready. "
          "An ambiguous PDK requires the user's process selection before device scans. "
          "Search summaries by exact library/cell/view/parameter/kind/family or substring query. "
          "Once the database is prepared, category (PDK registry), tier (parameter tier, alone or "
          "with parameter), range (parameter with a published min/max) and readiness filters use the "
          "collected device index. Only devices included by the PDK inclusion rule are listed; an "
          "excluded cell answers not_indexed with its reason. Repeat normalized filters and "
          "snapshot_ref with each cursor. Classification stays unknown without sourced project "
          "annotations. Does not run callbacks or modify OA.",
          {**_SEARCH, **_COMMON}),
    _tool("get_pdk_device", "Read selected effective cell CDF, ports, callback metadata, simulation "
          "interfaces and local Symbol pin figures. Served from the prepared workspace database "
          "(cache_status describes the source) so agents do not need a live device read; live capture "
          "is the fallback and revalidate_pdk_bindings is the live check. tier=interface (default) "
          "returns the design interface layer only, tier=all returns every CDF parameter. The first "
          "detail read freezes a separate revision; later pages use that revision. No expressions or "
          "callbacks are evaluated.",
          {**_COMMON, "device_ref": _REF,
           "sections": {"type": "array", "items": {"enum": list(SECTIONS)},
                        "minItems": 1, "maxItems": len(SECTIONS), "uniqueItems": True},
           "tier": {**_enum(*TIERS), "default": "interface"}},
          ("snapshot_ref", "device_ref")),
    _tool("revalidate_pdk_bindings", "Recollect each selected device from the live session and compare "
          "session generation, library resolution, master identity, CDF, ports, geometry and simInfo. "
          "Requires a prior detail read. valid means collected metadata matches, not permission or "
          "simulation readiness; unverified dependencies are reported. Never uses cached live data.",
          {"snapshot_ref": _REF, "device_refs": {"type": "array", "items": _REF,
                                                "minItems": 1, "maxItems": 20,
                                                "uniqueItems": True}},
          ("snapshot_ref", "device_refs")),
]
from .pdk_standard.tools import TOOLS as STANDARD_TOOLS, NAMES as STANDARD_NAMES

PDK_TOOLS += STANDARD_TOOLS
PDK_NAMES = frozenset(tool["name"] for tool in PDK_TOOLS)


def arguments(name, value):
    if name in STANDARD_NAMES:
        from .pdk_standard.tools import arguments as standard_arguments
        return standard_arguments(name, value)
    tool = next((t for t in PDK_TOOLS if t["name"] == name), None)
    if tool is None or not isinstance(value, dict):
        raise PdkArgumentError("unknown PDK tool or invalid arguments")
    schema = tool["inputSchema"]
    if set(value) - set(schema["properties"]) or set(schema["required"]) - set(value):
        raise PdkArgumentError("unknown or missing PDK arguments")
    result = dict(value)
    for key, prop in schema["properties"].items():
        if key not in result:
            if "default" in prop:
                result[key] = prop["default"]
            continue
        item = result[key]
        if prop.get("type") == "string":
            if (not isinstance(item, str) or not 1 <= len(item) <= prop.get("maxLength", 256)
                    or any(ord(c) < 32 for c in item)):
                raise PdkArgumentError(f"invalid {key}")
            if "enum" in prop and item not in prop["enum"]:
                raise PdkArgumentError(f"invalid {key}")
        elif prop.get("type") == "integer":
            if type(item) is not int or not prop["minimum"] <= item <= prop["maximum"]:
                raise PdkArgumentError(f"invalid {key}")
        elif prop.get("type") == "boolean":
            if type(item) is not bool:
                raise PdkArgumentError(f"invalid {key}")
        elif prop.get("type") == "array":
            if (not isinstance(item, list) or not prop["minItems"] <= len(item) <= prop["maxItems"]
                    or any(not isinstance(s, str) or not s or len(s) > 128
                           or any(ord(c) < 32 for c in s) for s in item)
                    or len(set(item)) != len(item)):
                raise PdkArgumentError(f"invalid {key}")
            if "enum" in prop["items"] and set(item) - set(prop["items"]["enum"]):
                raise PdkArgumentError(f"invalid {key}")
    if "cursor" in result and "snapshot_ref" not in result:
        raise PdkArgumentError("cursor requires snapshot_ref and the same query")
    if name == "get_pdk_device":
        result["sections"] = sorted(result.get("sections", SECTIONS))
    return result


def skill_string(value):
    # SKILL does not interpret JSON Unicode escapes; direct Unicode works.
    return "nil" if value is None else json.dumps(value, ensure_ascii=False)
