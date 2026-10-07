"""Place ports from source relations, with default bands only for missing evidence."""

from collections import defaultdict

from .circuit_geometry_schema import bounds, transform, transform_box
from .circuit_pin_style import INWARD_ESCAPE
from .circuit_spec_schema import CircuitSpecError
from .template_pg import GROUND_STEMS, POWER_STEMS, _stem


def _role(port, explicit):
    if port["name"] in explicit:
        return explicit[port["name"]]
    name = _stem(port["name"])
    return "power" if name in POWER_STEMS else "ground" if name in GROUND_STEMS else "signal"


def _band(port, role):
    if role in {"power", "ground"}:
        return 1 if role == "power" else 4
    return 2 if port["direction"] == "inputOutput" else 3


def _axis_map(value, pairs, axis, dbu):
    """Interpolate source row/column anchors; extrapolate in source user units."""
    groups = defaultdict(list)
    for old, new in pairs:
        groups[old[axis]].append(new[axis])
    levels = sorted((old, sum(new) / len(new)) for old, new in groups.items())
    if not levels:
        raise CircuitSpecError("template port placement requires mapped instance positions")
    if value <= levels[0][0]:
        return levels[0][1] + (value - levels[0][0]) / dbu
    if value >= levels[-1][0]:
        return levels[-1][1] + (value - levels[-1][0]) / dbu
    for (a, x), (b, y) in zip(levels, levels[1:]):
        if a <= value <= b:
            return x + (value - a) * (y - x) / (b - a)


def plan_ports(spec, geometry, placed, reference, device_map, options, *, port_map=None):
    grid = options.get("grid", 0.0625)
    def snap(value):
        return round(value / grid) * grid
    length = options.get("stub_length", 0.5)
    # Both ends may deliberately retain stubs. Reserve both lengths so a
    # same-net device stub cannot merge with the external pin's short wire.
    gap = max(options.get("port_gap", 0.5), 2 * length + options.get("clearance", 0.25) + grid)
    pitch = max(options.get("port_pitch", 0.5), 2 * grid)
    if options.get("spacing", "loose") == "loose":
        gap = max(gap, 1.0)
    boxes = [transform_box(geometry[k].get("annotation_bbox", geometry[k]["occupied_bbox"]),
                           v["orientation"], v["xy"]) for k, v in placed.items()]
    body = bounds([p for box in boxes for p in box])
    roles = {p["name"]: _role(p, options.get("port_roles") or {}) for p in spec["ports"]}
    sources = {p["name"]: p for p in reference.get("ports", [])}
    target_names = {p["name"] for p in spec["ports"]}
    mapping = dict(port_map) if port_map is not None else {
        name: name for name in sources if name in target_names}
    if len(set(mapping.values())) != len(mapping) or set(mapping.values()) - target_names:
        raise CircuitSpecError("port_map requires distinct declared target ports")
    if set(mapping) - set(sources):
        raise CircuitSpecError("port_map references unknown template ports")
    reverse = {target: source for source, target in mapping.items()}
    pairs = [(r["xy"], placed[device_map[r["device"]]]["xy"])
             for r in reference.get("instances", [])
             if r.get("device") in device_map and device_map[r["device"]] in placed
             and r.get("xy") is not None]
    dbu = reference.get("dbu_per_uu") or 1
    result, reports = {}, []
    for port in spec["ports"]:
        source = sources.get(reverse.get(port["name"]), {})
        if source.get("xy") is None:
            continue
        if not pairs:
            raise CircuitSpecError(
                "template pin positions exist but have no mapped placement frame")
        side = source["side"]
        xy = [snap(_axis_map(source["xy"][k], pairs, k, dbu)) for k in (0, 1)]
        # Keep the source side and relative row/column, reserving a full inward
        # stub outside target annotation extents. Never substitute a default band.
        axis, index = (0, 0) if side == "left" else (0, 1) if side == "right" else (
            (1, 1) if side == "top" else (1, 0))
        limit = body[index][axis] + (gap if index else -gap)
        xy[axis] = snap(max(xy[axis], limit) if index else min(xy[axis], limit))
        result[port["name"]] = dict(xy=xy, escape=INWARD_ESCAPE[side], side=side,
                                    role=roles[port["name"]], placement_source="template")
        reports.append(dict(port=port["name"], side=side, y=xy[1], source="template",
                            source_port=source["name"], source_xy=source["xy"]))
    # Equal source rows stay equal across opposite sides. On each side reserve
    # a minimum pitch while keeping source order if grid snapping compresses it.
    for side in INWARD_ESCAPE:
        axis = 1 if side in {"left", "right"} else 0
        rows = sorted((r for r in reports if r["side"] == side),
                      key=lambda r: (-r["source_xy"][axis], r["port"]))
        previous = None
        for row in rows:
            xy = result[row["port"]]["xy"]
            if previous is not None:
                xy[axis] = snap(min(xy[axis], previous - pitch))
            previous = xy[axis]
            row["y"] = xy[1]
    net_rows = defaultdict(list)
    for instance in spec["instances"]:
        key = instance["id"]
        for term in geometry[key]["terminals"]:
            if term["name"] in instance["connections"] and len(term["anchors"]) == 1:
                point = transform(term["anchors"][0]["xy"], placed[key]["orientation"],
                                  placed[key]["xy"])
                net_rows[instance["connections"][term["name"]]].append(
                    (snap(point[1]), key + "." + term["name"]))
    for side in ("left", "right"):
        missing = [p for p in spec["ports"] if p["name"] not in result
                   and (p["direction"] == "output") == (side == "right")]
        missing.sort(key=lambda p: (_band(p, roles[p["name"]]), spec["ports"].index(p)))
        previous = None
        for i, port in enumerate(missing):
            slot = snap(body[1][1] - i * pitch)
            candidates = [(y, t) for y, t in net_rows[port["net"]]
                          if abs(y - slot) <= max(pitch / 2, 2 * grid)
                          and (previous is None or y < previous - 2 * grid)]
            match = min(candidates, key=lambda r: abs(r[0] - slot)) if candidates else None
            y = match[0] if match else slot
            if previous is not None:
                y = min(y, previous - 2 * grid)
            # A missing pin cannot displace a pin whose position came from the template.
            occupied = [result[r["port"]]["xy"][1] for r in reports
                        if r["side"] == side and r["source"] == "template"]
            while any(abs(y - v) < pitch - 1e-9 for v in occupied):
                y = snap(y - pitch)
            previous = y
            x = body[0][0] - gap if side == "left" else body[1][0] + gap
            result[port["name"]] = dict(xy=[snap(x), snap(y)], escape=INWARD_ESCAPE[side],
                                        role=roles[port["name"]], placement_source="default")
            reports.append(dict(port=port["name"], side=side, y=snap(y), band_slot=slot,
                                source="terminal_row" if match and y == match[0] else "band_slot",
                                terminal=match[1] if match and y == match[0] else None,
                                reason="template_port_position_unavailable"))
    bands = {side: [r["port"] for r in sorted(reports, key=lambda r: -r["y"])
                    if r["side"] == side] for side in INWARD_ESCAPE}
    return result, {"ports": bands, "port_rows": reports}
