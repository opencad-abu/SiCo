"""Resolve source wires against captured physical pin centers and immutable topology."""

import math
from collections import defaultdict

from .circuit_geometry_schema import transform
from .template_coords import Coordinates
from .template_schema import TemplateError, digest
from .template_wire_contacts import (
    LEGACY_VERSION,
    VERSION,
    capture_contact_evidence,
    has_contact_evidence,
    source_contacts,
)
from .template_wire_graph import connect_net


def _point(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e12 for v in value)
    )


def _fail(message):
    raise TemplateError("routing_reference_malformed: " + message)


def capture_reference(header, rows, placement):
    """Normalize master-local geometry separately from source-relative coordinates."""
    anchor = placement.get("anchor") or {}
    if not isinstance(anchor, dict) or not isinstance(anchor.get("source_xy_dbu"), list):
        _fail("source placement anchor missing")
    coords = Coordinates(header["dbu_per_uu"], anchor["source_xy_dbu"])
    local = Coordinates(header["dbu_per_uu"])
    if not isinstance(placement.get("instances"), list):
        _fail("source placement instances missing")
    try:
        devices = {i["source_name"]: i["device"] for i in placement["instances"]}
    except (KeyError, TypeError):
        _fail("source placement identity incomplete")
    geometry, ports, gaps = [], [], []
    for row in rows:
        if row["kind"] == "instance_geometry" and row.get("instance") in devices:
            observation = row.get("observation")
            if not isinstance(observation, dict) or type(observation.get("ok")) is not bool:
                _fail("instance geometry observation missing")
            if not observation["ok"]:
                gaps.append(
                    {"code": "source_geometry_unavailable", "device": devices[row["instance"]]}
                )
                continue
            geometry.append(
                {
                    "device": devices[row["instance"]],
                    "library_path": row["library_path"],
                    "revision": digest(observation),
                    "occupied_bbox": local.box(observation["occupied_bbox"]),
                    "terminals": [
                        {
                            "name": name,
                            "anchors": [{"id": "a" + str(i), "xy": local.point(p)}
                                        for i, p in enumerate(points)],
                        }
                        for name, points in observation.get("pin_centers", [])
                    ],
                }
            )
        elif row.get("kind") == "port_anchor":
            ports.append(
                {"port": row["terminal"], "id": row["anchor_id"], "xy": coords.point(row["xy"])}
            )
    if local.rounded_values or coords.rounded_values or placement.get("rounding", {}).get(
        "rounded_values", 0
    ):
        gaps.append({"code": "source_wire_coordinate_rounded"})
    guard = next((r for r in rows if r.get("kind") == "dependency_guard"), None)
    if not guard or not isinstance(guard.get("before"), str):
        _fail("dependency guard missing")
    contacts = capture_contact_evidence(header, placement.get("shapes", []))
    return {
        "schema": VERSION if contacts else LEGACY_VERSION,
        **contacts,
        "dependency_digest": digest(guard["before"]),
        "geometry": geometry,
        "ports": ports,
        "gaps": gaps,
    }


def compile_reference(record):
    """Derive attachments from one source of geometry; never trust a stored success flag."""
    try:
        assets = record.get("assets")
        asset = assets.get("schematic", {}) if isinstance(assets, dict) else {}
        top = record.get("topology")
        capture = asset.get("wire_reference") if isinstance(asset, dict) else None
    except (AttributeError, TypeError):
        _fail("missing schematic asset")
    if not isinstance(asset, dict):
        _fail("missing schematic asset")
    capture = asset.get("wire_reference")
    if not isinstance(capture, dict) or capture.get("schema") not in {LEGACY_VERSION, VERSION}:
        raise TemplateError("routing_reference_incomplete")
    observed = has_contact_evidence(capture, asset.get("shapes", []))
    if not isinstance(top, dict) or not isinstance(top.get("devices"), list):
        _fail("topology devices missing")
    if not isinstance(top.get("nets"), list) or not isinstance(asset.get("instances"), list):
        _fail("topology nets or placements missing")
    try:
        places = {i["device"]: i for i in asset["instances"]}
        geometries = {g["device"]: g for g in capture["geometry"]}
        nets = {n["source_name"]: n["id"] for n in top["nets"]}
    except (KeyError, TypeError):
        _fail("incomplete attachment identity")
    if len(places) != len(asset["instances"]) or len(nets) != len(top["nets"]):
        _fail("duplicate attachment identity")
    net_ids = set(nets.values())
    device_ids = {d.get("id") for d in top["devices"]}
    if None in device_ids or len(device_ids) != len(top["devices"]):
        _fail("duplicate topology device identity")
    if set(places) != device_ids:
        _fail("placement/device coverage differs")
    if not isinstance(capture.get("geometry"), list) or not isinstance(capture.get("ports"), list):
        _fail("wire reference geometry/ports missing")
    if len(geometries) != len(capture["geometry"]):
        _fail("duplicate geometry identity")
    for position in places.values():
        if not _point(position.get("relative_xy")) or position.get("orient") not in {
            "R0", "R90", "R180", "MX", "MY", "MXR90", "MYR90", "R270"
        }:
            _fail("invalid source placement")
    for geometry in capture["geometry"]:
        box = geometry.get("occupied_bbox") if isinstance(geometry, dict) else None
        if not isinstance(box, list) or len(box) != 2 or not all(_point(p) for p in box):
            _fail("invalid captured master body")
        for terminal in geometry.get("terminals", []):
            if not isinstance(terminal, dict) or not isinstance(terminal.get("anchors"), list):
                _fail("invalid captured master terminal")
            for anchor in terminal["anchors"]:
                if not isinstance(anchor, dict) or not _point(anchor.get("xy")):
                    _fail("invalid captured master anchor")
    endpoints, gaps, lines = defaultdict(list), defaultdict(set), defaultdict(list)
    device_gaps, global_gaps = defaultdict(set), set()
    capture_gaps = capture.get("gaps", [])
    if not isinstance(capture_gaps, list):
        _fail("invalid capture gaps")
    for gap in capture_gaps:
        if not isinstance(gap, dict) or not isinstance(gap.get("code"), str):
            _fail("invalid capture gap")
        if gap.get("device") in device_ids:
            device_gaps[gap["device"]].add(gap["code"])
        elif gap.get("device") is None:
            global_gaps.add(gap["code"])
        else:
            _fail("capture gap references an unknown device")
    for device in top["devices"]:
        device_id = device.get("id")
        if not isinstance(device.get("pins"), list):
            _fail("device pins missing")
        names = [p.get("name") for p in device["pins"] if isinstance(p, dict)]
        if (len(names) != len(device["pins"]) or any(not isinstance(n, str) or not n for n in names)
                or len(set(names)) != len(names)):
            _fail("duplicate or invalid topology terminal identity")
        geometry = geometries.get(device_id, {})
        if not isinstance(geometry, dict):
            _fail("device geometry missing")
        try:
            terms = {t["name"]: t["anchors"] for t in geometry.get("terminals", [])}
        except (KeyError, TypeError):
            _fail("device terminal geometry malformed")
        if len(terms) != len(geometry.get("terminals", [])):
            _fail("duplicate geometry terminal identity")
        position = places.get(device_id, {})
        for pin in device["pins"]:
            if not isinstance(pin, dict):
                _fail("invalid topology pin")
            net = pin.get("net")
            if net is None:
                continue
            if net not in net_ids:
                _fail("pin references unknown net")
            anchors = terms.get(pin["name"], [])
            if len(anchors) != 1 or position.get("orient") is None:
                gaps[net].add("source_pin_anchor_missing_or_ambiguous")
                gaps[net].update(device_gaps[device_id])
                continue
            xy = transform(anchors[0]["xy"], position["orient"], position["relative_xy"])
            endpoints[net].append(
                {
                    "endpoint": {"device": device_id, "terminal": pin["name"]},
                    "anchor_id": anchors[0]["id"],
                    "xy": xy,
                }
            )
            gaps[net].update(device_gaps[device_id])
    for port in top["ports"]:
        if not isinstance(port, dict) or "net" not in port:
            _fail("port references unknown net")
        if port["net"] is None:
            global_gaps.add("unconnected_source_port")
            continue
        if port["net"] not in net_ids:
            _fail("port references unknown net")
        anchors = [a for a in capture["ports"] if isinstance(a, dict)
                   and a.get("port") == port.get("name")]
        if len(anchors) != 1:
            gaps[port["net"]].add("source_port_anchor_missing_or_ambiguous")
            continue
        if not _point(anchors[0].get("xy")):
            _fail("source port anchor coordinate malformed")
        endpoints[port["net"]].append(
            {
                "endpoint": {"port": port["name"]},
                "anchor_id": anchors[0]["id"],
                "xy": anchors[0]["xy"],
            }
        )
    if not isinstance(asset.get("shapes"), list):
        _fail("schematic shapes missing")
    markers = []
    for index, shape in enumerate(asset["shapes"]):
        if not isinstance(shape, dict):
            _fail("invalid schematic shape")
        figure, net = shape.get("figure") or {}, nets.get(shape.get("net"))
        if (figure.get("lpp") or [None])[0] != "wire":
            continue
        if figure.get("objType") == "label":
            continue  # Capture retains labels; physical connectivity does not follow their names.
        if observed and figure.get("objType") == "ellipse":
            box = figure.get("bBox")
            if (not isinstance(box, list) or len(box) != 2 or not all(_point(p) for p in box)
                    or any(box[0][k] > box[1][k] for k in (0, 1))):
                _fail("invalid source wire marker")
            markers.append(box)
            continue
        if net is None:
            if observed:
                global_gaps.add("source_wire_net_unobserved")
            continue
        points = figure.get("points")
        if not points or len(points) < 2 or figure.get("objType") not in {"line", "path"}:
            gaps[net].add("unsupported_source_wire_shape")
            if observed:
                global_gaps.add("unsupported_source_wire_shape")
            continue
        if not all(_point(point) for point in points):
            _fail("invalid source wire coordinate")
        for segment, (a, b) in enumerate(zip(points, points[1:])):
            if observed and (a == b or (a[0] != b[0] and a[1] != b[1])):
                global_gaps.add("non_orthogonal_source_wire")
            lines[net].append({"shape": index, "segment": segment, "points": [a, b]})
    contacts, isolated = source_contacts(lines, endpoints, observed=observed, markers=markers)
    result = {}
    for net in sorted(set(endpoints) | set(lines) | set(gaps)):
        connected = connect_net(lines[net], endpoints[net], shared_terminals=observed)
        reasons = sorted(gaps[net] | contacts[net] | set(connected["gaps"]) | global_gaps)
        result[net] = {
            **connected,
            "gaps": reasons,
            "anchors": endpoints[net],
            "eligible": not reasons,
            **({"isolated_crossings": isolated[net]} if observed else {}),
        }
    dbu = asset.get("dbu_per_uu")
    if type(dbu) not in (int, float) or not math.isfinite(dbu) or dbu <= 0:
        _fail("invalid source database unit")
    return {
        "schema": "cad.template.wire-attachments.v1",
        "nets": result,
        "reference_digest": digest(capture),
        "dbu_per_uu": dbu,
        "geometry": geometries,
        "positions": places,
    }
