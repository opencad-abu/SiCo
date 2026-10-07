"""Explicit template pin roles and deterministic symbol pin/label placement."""

import math

from .template_schema import TemplateError


def supply_pins(ports, anchors, body_box, stubs, roles):
    names = {p["source_name"] for p in ports}
    if set(roles) - names:
        raise TemplateError("port_roles keys must be source schematic terminal names")
    resolved = {
        p["source_name"]: roles.get(
            p["source_name"], {"supply": "power", "ground": "ground"}.get(p["sig_type"], "signal")
        )
        for p in ports
    }
    for name, role in roles.items():
        if role not in {"power", "ground", "signal"}:
            raise TemplateError("unknown symbol port role: " + name)
    old = [xy for name, xy in anchors.items() if resolved[name] in {"power", "ground"}]
    stubs[:] = [s for s in stubs if not any(p in old for p in (s[1][0], s[1][-1]))]
    center = sum(p[0] for p in body_box) / 2
    result = {}
    for role, side in [("power", "top"), ("ground", "bottom")]:
        selected = [p for p in ports if resolved[p["source_name"]] == role]
        top = side == "top"
        edge = (
            math.ceil(body_box[1][1] / 0.0625) if top else math.floor(body_box[0][1] / 0.0625)
        ) * 0.0625
        y = edge + (0.3125 if top else -0.3125)
        for index, port in enumerate(selected):
            x = round((center + (index - (len(selected) - 1) / 2) * 0.375) / 0.0625) * 0.0625
            xy = [x, y]
            anchors[port["source_name"]] = xy
            stubs.append(["line", [xy, [x, edge]], None])
            result[port["source_name"]] = side
    return resolved, result


def label_positions(x, y, side):
    """Name and analog annotation positions/justification, in target user units."""
    if side == "top":
        return [[x + 0.0625, y - 0.125], "upperLeft", [x + 0.125, y + 0.375], "lowerLeft"]
    if side == "bottom":
        return [[x + 0.0625, y + 0.125], "lowerLeft", [x + 0.125, y - 0.125], "upperLeft"]
    right = side == "right"
    return [
        [x + (-0.0625 if right else 0.0625), y + 0.0625],
        "lowerRight" if right else "lowerLeft",
        [x + (0.125 if right else -0.125), y - 0.0625],
        "upperLeft" if right else "upperRight",
    ]
