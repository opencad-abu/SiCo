"""Build the canonical, model-facing context payload from entry facts."""

from __future__ import annotations

from .completeness import STATES, aggregate, section
from .contracts import PAYLOAD_SCHEMA, TARGET_STATES, TURN_STEPS, string


def _dict(value):
    return value if isinstance(value, dict) else {}


def _int(value):
    return value if type(value) is int else None


def _cellview(entry):
    design = _dict(entry.get("design"))
    source = _dict(entry.get("source"))
    value = design.get("cellview")
    if not isinstance(value, dict):
        value = source.get("display_cellview")
    if not isinstance(value, dict):
        value = source.get("edit_cellview")
    return _dict(value)


def _view_name(cellview):
    value = cellview.get("view_name")
    return value if isinstance(value, str) and value else None


def _oa_view_type(cellview):
    value = cellview.get("oa_view_type")
    return value if isinstance(value, str) and value else None


def _oa_view_type_source(cellview):
    value = cellview.get("oa_view_type_source")
    return value if value in {"platform_api", "unknown"} else "unknown"


def _bbox(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        first, second = value
        if all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in (first, second)):
            x1, y1 = first[0], first[1]
            x2, y2 = second[0], second[1]
            if all(type(item) in (int, float) for item in (x1, y1, x2, y2)):
                return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1, "units": "unknown"}
    return None


def _parts(value):
    rows = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            rows.append({"name": str(item[0]), "direction": str(item[1])})
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            rows.append(dict(item))
    return rows


def _section(design, list_name, count_name):
    items = design.get(list_name)
    total = _int(design.get(count_name))
    if not isinstance(items, list):
        items = []
    if total is None:
        return section(0, None, expand="inspect")
    expand = "inspect" if total > len(items) else "none"
    return section(len(items), total, expand=expand)


def _target(entry, context_record, cellview):
    source = _dict(entry.get("source"))
    snapshot = _dict(context_record.get("snapshot"))
    if source.get("kind") == "ade" or entry.get("ade"):
        state = "ade_session"
    elif cellview:
        state = "cellview" if _oa_view_type(cellview) else "invalid_view"
    else:
        state = "unavailable"
    target_ref = context_record.get("target_id") or source.get("window_ref") or ""
    if state == "cellview":
        return {
            "target_ref": target_ref,
            "lib": cellview.get("lib") or None,
            "cell": cellview.get("cell") or None,
            "view_name": _view_name(cellview),
            "oa_view_type": _oa_view_type(cellview),
            "oa_view_type_source": _oa_view_type_source(cellview),
            "target_state": "cellview",
            "generation": context_record.get("generation"),
            "mode": cellview.get("mode") or None,
            "dirty": cellview.get("modified") is True,
        }
    if state == "invalid_view":
        return {
            "target_ref": target_ref,
            "lib": cellview.get("lib") or None,
            "cell": cellview.get("cell") or None,
            "view_name": _view_name(cellview),
            "oa_view_type": None,
            "oa_view_type_source": "unknown",
            "target_state": "invalid_view",
            "generation": context_record.get("generation"),
            "mode": cellview.get("mode") or None,
            "dirty": cellview.get("modified") is True,
        }
    return {
        "target_ref": target_ref or None,
        "lib": None,
        "cell": None,
        "view_name": None,
        "oa_view_type": None,
        "oa_view_type_source": "unknown",
        "target_state": state if state in TARGET_STATES else "unavailable",
        "generation": context_record.get("generation") or snapshot.get("generation"),
        "mode": None,
        "dirty": None,
    }


def build_payload(entry, context_record, *, turn_step="session_start", snapshot_id="",
                  work=None, stage=None, captured_at=None):
    """Project a ``cad.entry.context.v1`` observation into the canonical payload.

    This is a pure adapter: it does not call EDA, read files or mutate the target.
    """
    if not isinstance(entry, dict) or entry.get("schema") != "cad.entry.context.v1":
        raise ValueError("unsupported entry context schema")
    if turn_step not in TURN_STEPS:
        raise ValueError("invalid turn_step")
    string(snapshot_id, "snapshot_id", allow_empty=False)
    if not isinstance(context_record, dict):
        raise ValueError("context_record must be an object")
    source = _dict(entry.get("source"))
    design = _dict(entry.get("design"))
    cellview = _cellview(entry)
    target = _target(entry, context_record, cellview)
    sections = {
        "ports": _section(design, "ports", "port_count"),
        "instances": _section(design, "instances", "instance_count"),
        "nets": _section(design, "nets", "net_count"),
        "layers": _section(design, "layers", "layer_count"),
    }
    selection_count = _int(source.get("selection_count"))
    if selection_count is None:
        selection_count = _int(design.get("selection_count"))
    if selection_count is None:
        selection_section = section(0, None, expand="none")
    elif selection_count == 0:
        selection_section = section(0, 0)
    else:
        selection_section = section(0, selection_count, expand="none",
                                    next_action="inspect_selection")
    sections["selection"] = selection_section
    returned_cellview = None
    if cellview:
        returned_cellview = {
            "target_ref": target["target_ref"],
            "tech_lib": design.get("technology_library") or None,
            "bbox": _bbox(design.get("bbox")),
            "counts": {
                "ports": _int(design.get("port_count")),
                "instances": _int(design.get("instance_count")),
                "nets": _int(design.get("net_count")),
                "layers": _int(design.get("layer_count")),
            },
            "interface": {"pins": _parts(design.get("ports")), "top_nets": []},
            "capabilities": [] if target["target_state"] != "cellview" else ["read", "inspect"],
            "section_completeness": {
                name: dict(sections[name])
                for name in ("ports", "instances", "nets", "layers")
            },
            "completeness": aggregate(sections)["state"],
        }
    payload = {
        "schema_version": PAYLOAD_SCHEMA,
        "turn_step": turn_step,
        "snapshot_id": snapshot_id,
        "captured_at": captured_at or entry.get("captured_at") or "",
        "source": "live",
        "session": {
            "session_id": context_record.get("instance_id") or entry.get("session_ref") or "",
            "mode": "gui",
            "bound_target_ref": context_record.get("target_id"),
            "focused_window_ref": source.get("window_ref"),
        },
        "active_target": target,
        "window": {
            "window_ref": source.get("window_ref"),
            "kind": source.get("kind"),
            "active": True,
            "focused": source.get("is_current") is True,
            "target_ref": target["target_ref"],
            "view_region": _bbox(source.get("visible_design_bbox")),
        },
        "related_targets": [],
        "cellview": returned_cellview,
        "selection": {
            "count": selection_count,
            "primary_ref": None,
            "refs": [],
        },
        "objects": [],
        "completeness": aggregate(sections),
    }
    if isinstance(work, dict):
        payload["work"] = dict(work)
    if isinstance(stage, dict):
        payload["stage"] = dict(stage)
    validate_payload(payload)
    return payload


def validate_payload(payload):
    """Validate the canonical boundary; legacy view identity fields are rejected."""
    if not isinstance(payload, dict) or payload.get("schema_version") != PAYLOAD_SCHEMA:
        raise ValueError("unsupported context payload schema")
    if payload.get("turn_step") not in TURN_STEPS:
        raise ValueError("invalid payload turn_step")
    target = payload.get("active_target")
    if not isinstance(target, dict):
        raise ValueError("payload active_target must be an object")
    for field in ("view", "view_type"):
        if field in target:
            raise ValueError(f"legacy {field} is not accepted")
    state = target.get("target_state")
    if state not in TARGET_STATES:
        raise ValueError("invalid target_state")
    if state == "cellview":
        if not target.get("view_name") or not target.get("oa_view_type"):
            raise ValueError("cellview target requires view_name and oa_view_type")
    if state == "invalid_view" and target.get("oa_view_type") is not None:
        raise ValueError("invalid_view must not carry oa_view_type")
    if target.get("oa_view_type_source") not in {"platform_api", "unknown"}:
        raise ValueError("invalid oa_view_type_source")
    completeness = payload.get("completeness")
    if not isinstance(completeness, dict) or completeness.get("state") not in STATES:
        raise ValueError("invalid payload completeness")
    sections = completeness.get("sections")
    if not isinstance(sections, dict) or "selection" not in sections:
        raise ValueError("payload completeness sections are incomplete")
    for name, value in sections.items():
        if not isinstance(value, dict) or value.get("state") not in STATES:
            raise ValueError(f"invalid section completeness: {name}")
    return True
