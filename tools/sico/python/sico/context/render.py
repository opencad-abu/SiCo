"""Render a compact, deterministic context card from a payload."""

from __future__ import annotations

from .payload import validate_payload


def _value(value):
    return "unknown" if value is None or value == "" else str(value)


def _count(value):
    return "unknown" if value is None else str(value)


def _section(name, value):
    if not isinstance(value, dict):
        return f"{name}=unknown"
    state = value.get("state", "unknown")
    returned = value.get("returned")
    total = value.get("total")
    if total is None:
        return f"{name}={state}"
    return f"{name}={state}({returned}/{total})"


def render_card(payload):
    """Render a compact card; storage remains JSON, model input stays bounded."""
    validate_payload(payload)
    session = payload.get("session") or {}
    target = payload.get("active_target") or {}
    window = payload.get("window") or {}
    cellview = payload.get("cellview") or {}
    selection = payload.get("selection") or {}
    completeness = payload.get("completeness") or {}
    sections = completeness.get("sections") or {}
    lines = [
        "[SESSION] bound={} focused={}".format(
            _value(session.get("bound_target_ref")),
            _value(session.get("focused_window_ref")),
        ),
        "[TARGET] {}/{}/{} oa_view_type={} state={} gen={}".format(
            _value(target.get("lib")),
            _value(target.get("cell")),
            _value(target.get("view_name")),
            _value(target.get("oa_view_type")),
            _value(target.get("target_state")),
            _value(target.get("generation")),
        ),
        "[WINDOW] kind={} active={} focused={}".format(
            _value(window.get("kind")),
            _value(window.get("active")),
            _value(window.get("focused")),
        ),
    ]
    counts = cellview.get("counts") or {}
    lines.append(
        "[CELLVIEW] instances={} nets={} ports={} layers={}".format(
            _count(counts.get("instances")),
            _count(counts.get("nets")),
            _count(counts.get("ports")),
            _count(counts.get("layers")),
        )
    )
    lines.append("[SELECTION] count={} refs={}".format(
        _count(selection.get("count")), len(selection.get("refs") or [])
    ))
    stage = payload.get("stage")
    if isinstance(stage, dict):
        lines.append("[STAGE] work={} stage={} objective={}".format(
            _value((payload.get("work") or {}).get("work_id")),
            _value(stage.get("stage_id")),
            _value(stage.get("objective")),
        ))
    lines.append("[COMPLETENESS] {} {}".format(
        _value(completeness.get("state")),
        " ".join(_section(name, value) for name, value in sorted(sections.items())),
    ).rstrip())
    return "\n".join(lines)
