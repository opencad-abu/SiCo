"""Source-preserving metadata normalization; no naming heuristics or evaluation."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def unknown():
    return {"status": "unknown", "value": None}


def raw_value(raw):
    if not isinstance(raw, dict) or raw.get("status") != "known":
        return None
    if raw.get("type") == "list":
        return [raw_value(v) for v in raw["value"]]
    return raw.get("value")


def condition(raw):
    if not isinstance(raw, dict) or raw.get("status") != "known":
        return {**unknown(), "raw": raw}
    typ, value = raw.get("type"), raw.get("value")
    if typ == "nil":
        # CDF can expose nil for an unset optional condition.
        # Keep it distinct from the expression string "nil"; do not invent a
        # form default or confuse raw absence with the result of evaluation.
        return {"status": "unknown", "value": None, "raw": raw,
                "reason": "nil_metadata_is_not_an_evaluated_form_condition"}
    if typ in {"symbol", "string"} and value == "nil":
        status, value = "known", False
    elif typ in {"symbol", "string"} and value == "t":
        status, value = "known", True
    else:
        status, value = "context_required", None
    return {"status": status, "value": value, "raw": raw}


def classification():
    return {"kind": "unknown", "family": "unknown", "polarity": None, "tags": [],
            "status": "unknown", "source": "no_project_classification_provider"}


def complete_raw(value):
    if isinstance(value, dict):
        if value.get("status") in {"unavailable", "unsupported"}:
            return False
        return all(complete_raw(v) for v in value.values())
    if isinstance(value, list):
        return all(complete_raw(v) for v in value)
    return True


def section(status="not_requested", items=None, source=None, **extra):
    items = items or []
    if status == "complete" and not complete_raw(items):
        status = "partial"
    return {"status": status, "items": items, "count": len(items), "truncated": False,
            "issues": [] if status == "complete" else [status], "source": source, **extra}


def context(raw):
    raw = deepcopy(raw)
    libraries = sorted(raw.pop("libraries"), key=lambda x: (x["name"], x["resolved_path"] or ""))
    paths = {lib["name"]: lib["resolved_path"] for lib in libraries}
    for lib in libraries:
        lib["library_ref"] = "library:" + digest([lib["name"], lib["resolved_path"]])
        tech = lib.get("technology_library")
        lib["technology_binding"] = {"name": tech, "resolved_path": paths.get(tech),
                                     "status": "known" if paths.get(tech) else "unknown"}
        lib["revision"] = digest({k: v for k, v in lib.items() if k != "library_ref"})
    raw.update({"libraries": libraries, "library_bindings_digest": digest(libraries),
                "session_generation": digest([raw["session_ref"], raw.get("bridge_generation"),
                                               raw["collector_revision"]]),
                "project_ref": "project:" + digest(raw.get("workspace") or raw["working_directory"]),
                "captured_at": now()})
    return raw


def device_ref(identity, library):
    return "device:" + digest([identity["target"], library["resolved_path"]])


def summary(record, library):
    identity = record["identity"]
    names = record["cdf_summary"].get("names", [])
    return {"device_ref": device_ref(identity, library), "target": identity["target"],
            "library_ref": library["library_ref"], "identity": identity,
            "classification": classification(), "summary_revision": digest(record),
            "parameters": {**section(record["cdf_summary"]["status"], names[:32],
                                     "cdfGetCellCDF/parameter_names", total=len(names)),
                           "count": len(names), "truncated": len(names) > 32},
            "cdf_presence": record["cdf_summary"]["presence"],
            "ports": section(), "geometry": section(), "detail_available": True}


def _callback(raw, owner):
    if raw is None or raw.get("status") != "known":
        presence = "unknown"
    elif raw.get("type") == "nil" or (raw.get("type") == "string" and raw.get("value") == ""):
        presence = "absent"
    else:
        presence = "present"
    return {"callback_ref": "callback:" + digest([owner, raw]), "owner": owner,
            "presence": presence, "raw": raw, "digest": digest(raw),
            "execution_policy": "never_execute_during_discovery"}


def _parameter(raw, ref):
    name = raw_value(raw.get("name"))
    default = raw.get("defValue")
    callback = _callback(raw.get("callback"), {"device_ref": ref, "parameter": name})
    editable = condition(raw.get("editable"))
    cdf_type = raw_value(raw.get("paramType"))
    default_value = raw_value(default)
    if cdf_type == "boolean" and default and default.get("status") == "known":
        if default.get("type") == "nil":
            default_value = False
        elif default.get("type") == "symbol" and default.get("value") == "t":
            default_value = True
    value_type = ("null" if default_value is None else "boolean" if type(default_value) is bool else
                  "string" if isinstance(default_value, str) else "integer" if type(default_value) is int else
                  "number" if type(default_value) is float else "array" if isinstance(default_value, list) else "unknown")
    if cdf_type in {"radio", "cyclic", "boolean", "button"}:
        editable = {"status": "not_applicable", "value": None, "raw": raw.get("editable")}
    return {"name": name, "prompt": raw_value(raw.get("prompt")),
            "description": raw_value(raw.get("description")),
            "cdf_type": cdf_type,
            "value_type": value_type if default and complete_raw(default) else None,
            "parameter_kind": "action" if cdf_type == "button" else "value",
            "default": {"status": "known" if default and complete_raw(default) else "unknown",
                        "value": default_value, "raw": default},
            "current": {"status": "not_applicable", "value": None},
            "choices": raw.get("choices"),
            "units": {"cdf_raw": raw.get("units"), "physical_unit": None, "status": "unknown"},
            "editable": editable, "display": condition(raw.get("display")),
            "parse_as_number": raw.get("parseAsNumber"), "parse_as_cel": raw.get("parseAsCEL"),
            "callback_ref": callback["callback_ref"], "semantic_role": unknown(),
            "constraints": unknown(), "raw": raw}, callback


def _figure(item, ref):
    result = deepcopy(item)
    result["port_ref"] = "port:" + digest([ref, item["terminal"]])
    result["figure_ref"] = "figure:" + digest([result["port_ref"], item["pin_index"], item["figure_index"]])
    box = raw_value(item["raw"].get("bBox"))
    result["bbox"] = box
    result["shape_type"] = raw_value(item["raw"].get("objType"))
    if result["shape_type"] not in {"rect", "polygon", "line", "path"}:
        result["status"] = "partial" if item["status"] == "complete" else item["status"]
        result["shape_coverage"] = "unsupported_shape_metadata_subset"
    # A geometric center is a labelled candidate, never an asserted electrical anchor.
    candidates = []
    if (isinstance(box, list) and len(box) == 2
            and all(isinstance(p, list) and len(p) == 2 for p in box)
            and all(type(x) in (int, float) for p in box for x in p)):
        candidates = [{"point": [(box[0][i] + box[1][i]) / 2 for i in (0, 1)],
                       "method": "figure_bbox_center", "status": "candidate",
                       "source_ref": result["figure_ref"]}]
    result["anchors"] = {"status": "context_required", "selected": None, "candidates": candidates}
    return result


def detail(data, ctx):
    identity = data["identity"]
    lib = next(x for x in ctx["libraries"] if x["name"] == identity["target"]["library"])
    ref = device_ref(identity, lib)
    cdf, db = data["cdf"], data["database"]
    params, callbacks = [], []
    for raw in cdf["parameters"]["items"]:
        param, callback = _parameter(raw, ref)
        params.append(param)
        callbacks.append(callback)
    metadata = cdf["cell_metadata"]
    if cdf["presence"] != "absent":
        for hook in ("formInitProc", "doneProc"):
            callbacks.append(_callback(metadata.get(hook), {"device_ref": ref, "cell_hook": hook}))
    ports = deepcopy(db["ports"]["items"])
    for port in ports:
        port["port_ref"] = "port:" + digest([ref, port["name"]])
        port["direction_raw"] = port["direction"]
        port["direction"] = raw_value(port["direction"])
        port["bus"] = {"expression": port["name"], "members": port.pop("members"),
                       "status": port.pop("members_status"), "source": "dbGetMemName"}
        port["role"] = unknown()
    ports_complete = all(p.get("width", 0) and p["bus"]["status"] == "complete"
                         and p.get("net_expression_status") == "known" for p in ports)
    geometry = deepcopy(db["geometry"])
    geometry["items"] = [_figure(i, ref) for i in geometry["items"]]
    result = {"device_ref": ref, "target": identity["target"], "library_ref": lib["library_ref"],
              "library": lib, "classification": classification(),
              "identity": section("complete", [identity], identity["source"]),
              "ports": section("partial" if db["ports"]["status"] == "complete" and not ports_complete
                               else db["ports"]["status"], ports, db["ports"]["source"]),
              "parameters": section(cdf["parameters"]["status"], params, cdf["parameters"]["source"],
                                    cdf_scope="effective_cell", presence=cdf["presence"]),
              "geometry": {**geometry, "count": len(geometry["items"]), "truncated": False},
              "simulators": section(cdf["simulators"]["status"], cdf["simulators"]["items"],
                                    cdf["simulators"]["source"], simulation_readiness="unverified",
                                    raw_fingerprint="sha256:" + digest(cdf["simulators"])),
              "callbacks": section(
                  "complete" if cdf["cell_metadata_status"] == cdf["parameters"]["status"] == "complete"
                  else "partial", callbacks, "cdfGetCellCDF", cell_metadata=metadata),
              "project_models": data["project_models"], "master_state": db.get("master_state"),
              "captured_at": now()}
    if not complete_raw(geometry):
        result["geometry"]["status"] = "partial" if geometry["status"] == "complete" else geometry["status"]
    if geometry["status"] == "complete" and any(i["status"] != "complete" for i in geometry["items"]):
        result["geometry"]["status"] = "partial"
        result["geometry"]["issues"].append("pin_figure_geometry_incomplete")
    if geometry.get("parameterized_master") or geometry.get("hierarchical_instance_count"):
        result["geometry"]["status"] = "partial"
        result["geometry"]["issues"].append("parameterized_or_hierarchical_master_not_expanded")
    if any(c["presence"] == "unknown" for c in callbacks) or not complete_raw(metadata):
        result["callbacks"]["status"] = "partial"
        result["callbacks"]["issues"].append("callback_metadata_incomplete")
    # Hash raw observations, preserving nil/empty and ordered lists; timestamps excluded.
    result["dependency_digests"] = {key: digest(data[key]) for key in ("identity", "cdf", "database")}
    result["dependency_digests"]["library"] = digest(lib)
    result["revision"] = digest(result["dependency_digests"])
    return result
