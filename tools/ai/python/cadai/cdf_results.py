"""CDF tagged-value normalization; preserve nil, false, missing and expressions."""

import hashlib
import json


def raw(value):
    if value is None:
        return {"status": "not_applicable", "type": None, "value": None}
    typ, item = value
    if typ == "unsupported":
        return {"status": "unsupported", "type": None, "value": None, "reason": item}
    return {
        "status": "known",
        "type": typ,
        "value": [raw(v) for v in item or []] if typ == "list" else item,
    }


def condition(value):
    result = raw(value)
    item = result["value"]
    state = None
    if result["type"] == "nil":
        status = "absent"
    elif result["type"] in ("symbol", "string") and item in ("t", "nil"):
        status, state = "static", item == "t"
    elif result["type"] == "string" and item:
        status = "context_required"
    else:
        status = "unknown"
    return {"status": status, "value": state, "raw": result, "evaluated": False}


def parameters(rows, instance):
    output = []
    for r in rows or []:
        present, prop_type, prop_value = r[12]
        output.append(
            dict(
                name=r[0],
                cdf_type=r[1],
                prompt=r[2],
                default=raw(r[3]),
                current=raw(r[4]) if instance else {"status": "not_applicable"},
                effective_cell_value=raw(r[4]) if not instance else {"status": "not_applicable"},
                choices=raw(r[5]),
                units=raw(r[6]),
                editable=condition(r[7]),
                display=condition(r[8]),
                callback=raw(r[9]),
                parse_as_number=raw(r[10]),
                parse_as_cel=raw(r[11]),
                instance_property=dict(
                    status="present" if present else "absent" if instance else "not_applicable",
                    type=prop_type,
                    value=raw(prop_value),
                ),
            )
        )
    return output


def properties(tagged):
    """Read a Cadence disembodied plist without evaluating symbol/value expressions."""
    if tagged.get("type") != "list":
        return None
    items = tagged["value"]
    if not items or items[0].get("type") != "nil" or len(items) % 2 != 1:
        return None
    result = {}
    for index in range(1, len(items), 2):
        key = items[index]
        if key.get("type") != "symbol" or key["value"] in result:
            return None
        result[key["value"]] = items[index + 1]
    return result


def normalize(reply, name):
    result = dict(reply)
    result["schema_version"] = (
        "cad.cdf.inspection.v1" if name == "inspect_cdf" else "cad.cdf.probe.v1"
    )
    if name == "inspect_cdf":
        result["parameters"] = parameters(reply.get("parameters"), reply["instance"] is not None)
        result["missing_parameters"] = reply.get("missing_parameters") or []
        result["hooks"] = dict(zip(("form_init", "done"), map(raw, reply["hooks"])))
        result["sim_info"] = raw(reply["sim_info"])
        sims = properties(result["sim_info"])
        result["simulators"] = {
            "status": "parsed" if sims is not None else "raw_only",
            "items": [
                {"name": name, "attributes": properties(value), "raw": value}
                for name, value in (sims or {}).items()
            ],
        }
        result["expressions_evaluated"] = False
        result["revision_scope"] = "returned_page_and_context; cdf_ref binds full frozen capture"
    else:
        result["parameters"] = parameters(reply.get("parameters"), True)
        result["raw_properties"] = [
            dict(name=r[0], type=r[1], value=raw(r[2])) for r in reply.get("raw_properties") or []
        ]
        final = {p["name"]: p for p in result["parameters"]}
        matches = dict(reply.get("requested_matches") or [])
        result["requested_matches"] = [
            dict(
                name=r[0],
                requested=r[2],
                matched=matches.get(r[0]),
                effective=final.get(r[0], {}).get("current", {"status": "unavailable"}),
                instance_property=final.get(r[0], {}).get(
                    "instance_property", {"status": "unavailable"}
                ),
            )
            for r in reply.get("requested") or []
        ]
        result["all_requested_match"] = (
            all(matches.values())
            if len(matches) == len(reply.get("requested") or []) and reply.get("readback_verified")
            else None
        )
        previous, trace = {}, []
        for label, rows in reply.get("trace") or []:
            current = {n: raw(v) for n, v in rows or []}
            changes = (
                [
                    dict(
                        name=n,
                        before=previous.get(n, {"status": "absent"}),
                        after=current.get(n, {"status": "absent"}),
                    )
                    for n in sorted(set(previous) | set(current))
                    if previous.get(n) != current.get(n)
                ]
                if trace
                else []
            )
            trace.append(dict(label=label, changes=changes, values=current))
            previous = current
        result["trace"] = trace
        result["trace_coverage"] = (
            "builtin_parameter_checkpoints"
            if any(t["label"].startswith("builtin:") for t in trace)
            else "project_checkpoints" if any(t["label"].startswith("project:") for t in trace)
            else "boundary_only"
        )
        result["record_semantics"] = "historical_probe_outcome; not live view validation"
        result["baseline_parameters"] = trace[0]["values"] if trace else {}
    result["content_digest"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return result
