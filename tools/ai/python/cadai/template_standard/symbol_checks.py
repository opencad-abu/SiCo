"""Symbol interface/geometry checks, independent of schematic reuse eligibility."""

import math

from .codec import fail, logical
from .versions import SYMBOL_INTERFACE_SUFFIX

DIRECTIONS = {"input", "output", "inputOutput", "unknown"}
ESCAPES = {"left", "right", "up", "down", "any", "unknown"}


def validate(doc, top=None):
    from .semantics import box, point
    from .validate import keys

    frame = doc["frame"]
    keys(frame, "unit dbu_per_uu anchor_method")
    if (
        frame["unit"] != "source_dbu"
        or frame["anchor_method"] != "instance-min-or-view-origin-v1"
        or type(frame["dbu_per_uu"]) is not int
        or frame["dbu_per_uu"] <= 0
    ):
        fail("invalid symbol frame")
    names, mapped = set(), set()
    for row in doc["terminals"].values():
        keys(row, "name direction width", "port")
        if (
            not isinstance(row["name"], str)
            or not row["name"]
            or row["name"] in names
            or row["direction"] not in DIRECTIONS
            or type(row["width"]) is not int
            or row["width"] < 1
        ):
            fail("invalid symbol terminal")
        names.add(row["name"])
        if "port" in row:
            if not top or row["port"] not in top["ports"] or row["port"] in mapped:
                fail("invalid/duplicate symbol port reference")
            mapped.add(row["port"])
    for row in doc["anchors"].values():
        keys(row, "terminal xy escape")
        if row["terminal"] not in doc["terminals"] or row["escape"] not in ESCAPES:
            fail("invalid symbol anchor reference")
        point(row["xy"])
    for row in doc["outline"].values():
        kind = row["kind"]
        if kind in {"ellipse", "arc"}:
            keys(row, "kind ellipse_bbox" + (" start_angle stop_angle" if kind == "arc" else ""))
            box(row["ellipse_bbox"])
            if kind == "arc" and any(
                type(row[k]) not in (int, float) or not math.isfinite(row[k])
                for k in ("start_angle", "stop_angle")
            ):
                fail("invalid symbol arc angles")
            continue
        keys(row, "kind points")
        if kind not in {"polyline", "polygon", "rectangle"}:
            fail("unsupported symbol primitive")
        points = row["points"]
        if not isinstance(points, list) or len(points) < (3 if row["kind"] == "polygon" else 2):
            fail("incomplete symbol primitive")
        for xy in points:
            point(xy)
        if row["kind"] == "rectangle":
            box(points)
    for row in doc["labels"].values():
        keys(row, "text xy bbox kind")
        if not isinstance(row["text"], str) or row["kind"] not in {"literal", "dynamic_literal"}:
            fail("invalid symbol label")
        point(row["xy"])
        box(row["bbox"])


def interface_evidence(symbol, capture_digest):
    """Bind the saved terminal interface to its retained capture."""
    terminals = sorted(
        (
            dict(name=row["name"], direction=row["direction"], width=row["width"])
            for row in symbol["terminals"].values()
        ),
        key=lambda row: row["name"],
    )
    return "symbol-interface:" + logical(
        dict(capture=capture_digest, terminals=terminals)
    )


def _interface_evidence_issues(symbol, sources):
    if symbol.get("source") != "symbol_interface":
        return ["symbol_independent_interface_source_mismatch"]
    if not sources:
        return ["symbol_independent_interface_evidence_missing"]
    source = sources.get("symbol_interface")
    if (
        not isinstance(source, dict)
        or source.get("kind") != "derived"
        or source.get("method") != "saved-symbol-interface-v1"
        or set(source.get("inputs", [])) != {"capture"}
    ):
        return ["symbol_independent_interface_evidence_missing"]
    capture = sources.get("capture") or {}
    capture_digest = capture.get("scope_digest")
    if not capture_digest:
        return ["symbol_independent_interface_evidence_missing"]
    if source.get("evidence_ref") != interface_evidence(symbol, capture_digest):
        return ["symbol_independent_interface_evidence_mismatch"]
    if set(symbol.get("depends_on", [])) != {"capture"}:
        return ["symbol_independent_interface_dependency_mismatch"]
    return []


def issues(docs, source_ports=None, *, sources=None, evidence_required=False):
    symbol = docs.get("symbol")
    if symbol is None:
        return ["symbol_asset_missing"]
    top = docs.get("topology")
    validate(symbol, top)
    reasons = []
    terminals = symbol["terminals"]
    if {r["terminal"] for r in symbol["anchors"].values()} != set(terminals):
        reasons.append("symbol_anchor_coverage_incomplete")
    if any(r["direction"] == "unknown" for r in terminals.values()):
        reasons.append("symbol_direction_unknown")
    from ..template_schema import TemplateError
    from ..template_symbol_plan import members

    expanded = set()
    for row in terminals.values():
        try:
            bits, _ = members(row["name"])
            if len(bits) != row["width"] or expanded.intersection(bits):
                reasons.append("symbol_bus_width_or_members_differ")
            expanded.update(bits)
        except TemplateError:
            reasons.append("symbol_port_notation_unsupported")
    # Empty ports are allowed only when a captured complete interface proves that absence.
    if top is not None:
        if {r.get("port") for r in terminals.values()} != set(top["ports"]):
            reasons.append("symbol_schematic_interface_coverage_differs")
        for row in terminals.values():
            port = top["ports"].get(row.get("port"))
            if port and any(row[k] != port[k] for k in ("name", "direction", "width")):
                reasons.append("symbol_schematic_interface_differs")
    else:
        if evidence_required:
            reasons.extend(_interface_evidence_issues(symbol, sources))
        elif "capture" not in symbol["depends_on"]:
            # Historical receipts retain their original capture-dependency check.
            reasons.append("symbol_independent_interface_evidence_missing")
        if source_ports is not None:
            expected = {
                r["name"]: (r.get("direction"), r.get("numBits", 1)) for r in source_ports
            }
            actual = {r["name"]: (r["direction"], r["width"]) for r in terminals.values()}
            if expected != actual or len(expected) != len(source_ports):
                reasons.append("symbol_saved_interface_differs")
    if any(g["part"] == "symbol.json" for g in docs["collection"]["gaps"].values()):
        reasons.append("symbol_capture_has_gaps")
    return sorted(set(reasons))


def validate_receipt(docs, *, sources=None, strict=True):
    if "symbol" in docs and strict:
        validate(docs["symbol"], docs.get("topology"))
    check = docs["qualification"]["checks"]["symbol-reference-v1"]
    evidence_required = bool(
        strict and check.get("validator", "").endswith(SYMBOL_INTERFACE_SUFFIX)
    )
    if check["result"] == "passed" and issues(
        docs, sources=sources, evidence_required=evidence_required
    ):
        fail("passed symbol has incomplete interface or geometry")
