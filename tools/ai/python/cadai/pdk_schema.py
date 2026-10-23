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


def _tool(name, description, properties, required=(), *, help_text=None, mutating=False):
    """One definition; ``help_text`` is on-demand detail and stays off the wire."""
    row = {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "properties": properties,
                        "required": list(required), "additionalProperties": False},
        "annotations": {"readOnlyHint": not mutating, "destructiveHint": False,
                        "idempotentHint": mutating, "openWorldHint": False},
    }
    if help_text:
        row["help"] = help_text
    return row


PDK_TOOLS = [
    _tool("init_pdk", "Initialize the selected or named PDK library in this Virtuoso "
          "session (loads libInit.il once; idempotent). Call after choosing the process or "
          "before design/simulation work. Failures block design tools; paths are never guessed.",
          {"library": _text()},
          mutating=True),
    _tool("get_pdk_preparation", "Inspect PDK data availability before circuit design without scanning "
          "devices. analogLib/basic use built-in data. Prefer SICO_PDK_DATA, then workspace data. "
          "Supply target_library to bind an existing design library; a relocated path reuses data "
          "with a pdk_path_changed notice. Technology binding wins; ask only when undetermined.",
          {"view": _SEARCH["view"], "target_library": _text(),
           "include_base_libraries": {"type": "boolean", "default": False}}),
    _tool("prepare_pdk_data", "Start a separate PDK collection subtask before device search. "
          "analogLib/basic use built-in data. Reuse qualified publications and validated metadata "
          "first; a changed PDK path reuses relocated data with a notice. Otherwise collect "
          "directory, parameters, ports, geometry, simulator and callback metadata into the "
          "workspace store; return collection_ref for bounded batches. With multiple PDKs, "
          "library and choice_confirmed need a real user choice unless a live technology "
          "binding exists.",
          {"library": _text(), "view": _SEARCH["view"], "target_library": _text(),
           "choice_confirmed": {"type": "boolean", "default": False},
           "refresh": {"type": "boolean", "default": False}},
          help_text=(
              "A capture-ready result is not design readiness; publishing uses the generation "
              "workflow. Missing or stale configured data needs no CAD support. Interrupted "
              "read-only collection resumes automatically with bounded retries; refresh restarts "
              "it and user cancellation is respected. This tool does not choose "
              "foreground/background; advance_pdk_collection carries the bounded batches.")),
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
    _tool("search_pdk_devices", "Legacy session-adapter discovery; results grant no design usage "
          "(use get_pdk_data). Search a prepared PDK directory; missing data starts a collection "
          "subtask, then follow next_action. An ambiguous PDK needs the user's process selection. "
          "Match exact fields or a substring; category/tier/range/readiness filters use the "
          "collected index. Only PDK-included cells are listed; excluded cells answer not_indexed. "
          "Repeat filters and snapshot_ref per cursor; no callbacks run.",
          {**_SEARCH, **_COMMON}),
    _tool("get_pdk_device", "Read selected cell CDF, ports, callback metadata, simulation "
          "interfaces and Symbol pin figures from the prepared database (cache_status names the "
          "source); live capture is a fallback. tier=interface (default) returns the design layer, "
          "tier=all every CDF parameter. First detail read freezes a revision later pages reuse; "
          "no expressions or callbacks run.",
          {**_COMMON, "device_ref": _REF,
           "sections": {"type": "array", "items": {"enum": list(SECTIONS)},
                        "minItems": 1, "maxItems": len(SECTIONS), "uniqueItems": True},
           "tier": {**_enum(*TIERS), "default": "interface"}},
          ("snapshot_ref", "device_ref")),
    _tool("revalidate_pdk_bindings", "Recollect each selected device from the live session and compare "
          "generation, library, master identity, CDF, ports, geometry and simInfo. Requires a "
          "prior detail read. valid means metadata matches, not permission or simulation "
          "readiness; unverified dependencies are reported; never cached.",
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
