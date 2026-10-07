"""JSON Schema for successful PDK discovery responses; extensible sourced records."""


def obj(properties, required=(), **extra):
    return {"type": "object", "properties": properties, "required": list(required), **extra}


def arr(items):
    return {"type": "array", "items": items}


def ref(name):
    return {"$ref": "#/$defs/" + name}


TEXT = {"type": "string"}
NULLABLE_TEXT = {"type": ["string", "null"]}
BOOL = {"type": "boolean"}
COUNT = {"type": "integer", "minimum": 0}
STATUS = {"enum": ["complete", "partial", "unavailable", "not_requested", "not_applicable"]}
RAW = obj({"type": {"enum": ["nil", "symbol", "string", "integer", "float", "list",
                              "opaque_or_limit", "unavailable"]},
           "status": {"enum": ["known", "unsupported", "unavailable"]},
           "value": {"anyOf": [{"type": ["null", "string", "number"]}, arr(ref("raw"))]}},
          ("type", "status", "value"), additionalProperties=False)
RAW_MAP = {"type": "object", "additionalProperties": ref("raw")}
FIELD = obj({"status": {"enum": ["known", "unknown", "context_required", "not_applicable"]},
             "value": {}, "raw": {"anyOf": [ref("raw"), {"type": "null"}]}}, ("status", "value"))
TARGET = obj({k: TEXT for k in ("library", "cell", "view")}, ("library", "cell", "view"),
             additionalProperties=False)
CLASSIFICATION = obj({"kind": TEXT, "family": TEXT, "polarity": NULLABLE_TEXT,
                      "status": TEXT, "source": TEXT, "tags": arr(TEXT)},
                     ("kind", "family", "status", "source"))
LIBRARY = obj({"library_ref": TEXT, "revision": TEXT, "name": TEXT, "resolved_path": NULLABLE_TEXT,
               "status": STATUS, "technology_library": NULLABLE_TEXT, "technology_binding": {"type": "object"},
               "pdk_ref": NULLABLE_TEXT, "pdk_version": NULLABLE_TEXT, "pdk_status": TEXT, "source": TEXT},
              ("library_ref", "revision", "name", "resolved_path", "status", "pdk_status", "source"))
SECTION = obj({"status": STATUS, "source": NULLABLE_TEXT, "items": arr({}), "count": COUNT,
               "truncated": BOOL, "returned": COUNT, "issues": arr(TEXT)}, ("status", "items"))
IDENTITY = obj({"target": ref("target"), "available_views": arr(TEXT), "resolved_view_path": NULLABLE_TEXT,
                "view_type": NULLABLE_TEXT, "master_file_modified": {"anyOf": [ref("raw"), {"type": "null"}]},
                "master_version_source": TEXT, "source": TEXT},
               ("target", "available_views", "resolved_view_path", "view_type", "source"))
PARAMETER = obj({"name": NULLABLE_TEXT, "cdf_type": NULLABLE_TEXT, "value_type": NULLABLE_TEXT,
                 "prompt": NULLABLE_TEXT, "description": NULLABLE_TEXT, "raw": RAW_MAP,
                 "default": ref("field"), "current": ref("field"), "editable": ref("field"),
                 "display": ref("field"), "callback_ref": TEXT, "semantic_role": ref("field"),
                 "units": {"type": "object"}, "constraints": ref("field")},
                ("name", "cdf_type", "value_type", "raw", "default", "current", "editable", "display",
                 "callback_ref", "semantic_role"))
PORT = obj({"name": TEXT, "port_ref": TEXT, "direction": NULLABLE_TEXT, "direction_raw": ref("raw"),
            "width": {"type": ["integer", "null"]}, "pin_count": COUNT, "bus": obj({
                "expression": TEXT, "members": arr(TEXT), "status": STATUS, "source": TEXT},
                ("expression", "members", "status", "source")), "role": ref("field"),
            "net_expression_status": TEXT, "net_expression": {"anyOf": [ref("raw"), {"type": "null"}]}},
           ("name", "port_ref", "direction", "direction_raw", "width", "bus", "role"))
POINT = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
FIGURE = obj({"terminal": TEXT, "port_ref": TEXT, "figure_ref": TEXT, "pin_index": COUNT,
              "figure_index": {"type": ["integer", "null"]}, "raw": RAW_MAP, "shape_type": NULLABLE_TEXT,
              "anchors": obj({"status": {"const": "context_required"}, "selected": {"type": "null"},
                              "candidates": arr(obj({"point": POINT, "method": TEXT, "status": TEXT,
                                                     "source_ref": TEXT}, ("point", "method", "status", "source_ref")))},
                             ("status", "selected", "candidates"))},
             ("terminal", "port_ref", "figure_ref", "pin_index", "figure_index", "raw", "anchors"))
CALLBACK = obj({"callback_ref": TEXT, "owner": {"type": "object"}, "presence": {"enum": ["present", "absent", "unknown"]},
                "raw": {"anyOf": [ref("raw"), {"type": "null"}]}, "digest": TEXT,
                "execution_policy": {"const": "never_execute_during_discovery"}},
               ("callback_ref", "owner", "presence", "raw", "digest", "execution_policy"))
SIMULATOR = obj({"name": TEXT, "raw": RAW_MAP}, ("name", "raw"))


def part(item):
    return {"allOf": [ref("section"), obj({"items": arr(ref(item))})]}


INTERFACE_PARAMETER = obj(
    {key: PARAMETER["properties"][key] for key in (
        "name", "cdf_type", "value_type", "prompt", "description", "default", "units", "semantic_role")},
    ("name", "cdf_type", "value_type", "default", "semantic_role"))
PARAMETERS = {
    "allOf": [ref("section")],
    "if": obj({"projection": {"const": "interface"}}, ("projection",)),
    "then": obj({"items": arr(ref("interface_parameter")), "tier": {"const": "interface"},
                 "tier_applied": {"const": True}, "raw_available": {"const": "tier=all"}},
                ("tier", "tier_applied", "raw_available")),
    "else": obj({"items": arr(ref("parameter"))}),
}


PARTS = {key: part(item) for key, item in {"identity": "identity", "ports": "port", "parameters": "parameter",
         "geometry": "figure", "simulators": "simulator", "callbacks": "callback"}.items()}
PARTS["parameters"] = PARAMETERS
DEVICE = obj({"device_ref": TEXT, "revision": TEXT, "target": ref("target"), "library_ref": TEXT,
              "library": ref("library"), "classification": ref("classification"), **PARTS,
              "dependency_digests": {"type": "object", "additionalProperties": TEXT}, "captured_at": TEXT,
              "project_models": ref("section"), "master_state": {"type": ["object", "null"]}},
             ("device_ref", "revision", "target", "library_ref", "classification", "dependency_digests",
              "captured_at", *PARTS))
SUMMARY = obj({"device_ref": TEXT, "target": ref("target"), "library_ref": TEXT, "library": ref("library"),
               "identity": ref("identity"), "classification": ref("classification"), "summary_revision": TEXT,
               "parameters": ref("section"), "ports": ref("section"), "geometry": ref("section"),
               "cdf_presence": {"enum": ["present", "absent", "unknown"]}, "detail_available": BOOL},
              ("device_ref", "target", "library_ref", "classification", "summary_revision", "parameters"))
VALIDATION = obj({"device_ref": TEXT, "status": {"enum": ["valid", "changed", "unavailable"]},
                  "changes": arr(TEXT), "unverified": arr(TEXT), "outside_scope": arr(TEXT),
                  "expected_revision": TEXT, "observed_revision": TEXT, "checked_at": TEXT,
                  "observed_session_generation": TEXT, "checked_dependencies": arr(TEXT)},
                 ("device_ref", "status", "changes"))
PAGE = obj({"offset": COUNT, "returned": COUNT, "total": COUNT, "next_cursor": NULLABLE_TEXT,
            "truncated": BOOL, "response_limit_bytes": {"const": 90000}},
           ("offset", "returned", "total", "next_cursor", "truncated", "response_limit_bytes"))
CONTEXT = obj({"project_ref": TEXT, "session_ref": TEXT, "session_generation": TEXT, "snapshot_ref": TEXT,
               "process_id": {"type": "integer"}, "bridge_generation": {"type": ["integer", "null"]},
               "collector_revision": TEXT, "captured_at": TEXT, "captured_at_cadence": TEXT,
               "virtuoso_version": TEXT, "library_bindings_digest": TEXT, "directory_digest": TEXT,
               "working_directory": TEXT, "workspace": NULLABLE_TEXT, "source": TEXT},
              ("project_ref", "session_ref", "session_generation", "snapshot_ref", "process_id",
               "collector_revision", "captured_at", "virtuoso_version", "library_bindings_digest"))

OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "cad.pdk.session.v1 successful tool response",
    **obj({"schema_version": {"const": "cad.pdk.session.v1"}, "ok": {"const": True},
           "context": ref("context"), "read_only": {"const": True}, "callbacks_executed": {"const": False},
           "source_status": {"enum": ["live_session_capture", "persisted_directory_live_context"]}, "cache_policy": TEXT},
          ("schema_version", "ok", "context", "read_only", "callbacks_executed", "source_status")),
    "oneOf": [
        obj({"query": {"type": "object"}, "status": STATUS, "directory": {"type": "object"},
             "items": arr(ref("summary")), "page": ref("page")}, ("query", "status", "directory", "items", "page")),
        obj({"device": ref("device"), "page": ref("page")}, ("device", "page")),
        obj({"status": {"enum": ["valid", "changed", "unavailable"]}, "items": arr(ref("validation")),
             "validation_scope": TEXT, "checked_at": TEXT, "cache_used_for_observation": {"const": False},
             "atomic_with_future_write": {"const": False}},
            ("status", "items", "validation_scope", "checked_at", "cache_used_for_observation", "atomic_with_future_write")),
        # D4: an excluded or unpublished library cell is answered, never collected live.
        obj({"status": {"const": "not_indexed"}, "reason": TEXT,
             "next_action": {"enum": ["report_to_pdk_owner", "not_a_front_end_device",
                                      "refresh_pdk_collection"]},
             "index_digest": TEXT, "cell": TEXT, "device_ref": TEXT, "excluded": BOOL},
            ("status", "reason", "next_action")),
    ],
    "$defs": {"raw": RAW, "field": FIELD, "target": TARGET, "classification": CLASSIFICATION,
              "library": LIBRARY, "section": SECTION, "identity": IDENTITY, "parameter": PARAMETER,
              "port": PORT, "figure": FIGURE, "callback": CALLBACK, "simulator": SIMULATOR,
              "device": DEVICE, "summary": SUMMARY, "validation": VALIDATION, "page": PAGE, "context": CONTEXT,
              "interface_parameter": INTERFACE_PARAMETER},
}
