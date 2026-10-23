"""Explicit template pin roles and deterministic symbol pin/label placement."""

import math
import re

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


_RAIL_WORDS = {
    "power": {
        "vdd",
        "vcc",
        "vee",
        "vpwr",
        "pwr",
        "power",
        "avdd",
        "dvdd",
        "pvdd",
        "vddio",
    },
    "ground": {
        "vss",
        "gnd",
        "agnd",
        "dgnd",
        "avss",
        "dvss",
        "vssa",
        "vssio",
        "vgnd",
        "ground",
        "vsub",
        "sub",
    },
}


def _rail_role(*names):
    for name in names:
        if not isinstance(name, str):
            continue
        words = set(re.findall(r"[a-z0-9]+", name.lower()))
        for role, candidates in _RAIL_WORDS.items():
            if words & candidates:
                return role
    return None


def suggest_port_roles(ports, roles=None):
    """Return reviewable PG hints without applying an unconfirmed role."""
    roles = roles or {}
    result = []
    for port in ports:
        source = port["source_name"]
        name = port.get("name", source)
        explicit = roles.get(source)
        if explicit is not None:
            if explicit != "signal":
                result.append(
                    {
                        "source_name": source,
                        "name": name,
                        "suggested_role": explicit,
                        "basis": "explicit_port_roles",
                        "applied": True,
                        "requires_confirmation": False,
                        "reason": "role was supplied explicitly",
                    }
                )
            continue
        if port.get("sig_type") in {"supply", "ground"}:
            role = "power" if port["sig_type"] == "supply" else "ground"
            result.append(
                {
                    "source_name": source,
                    "name": name,
                    "suggested_role": role,
                    "basis": "source_sig_type",
                    "applied": True,
                    "requires_confirmation": False,
                    "reason": "source net metadata is sig_type=" + port["sig_type"],
                }
            )
            continue
        role = _rail_role(port.get("global_net"), name)
        if role:
            result.append(
                {
                    "source_name": source,
                    "name": name,
                    "suggested_role": role,
                    "basis": "global_rail_name" if port.get("global_net") else "rail_name",
                    "applied": False,
                    "requires_confirmation": True,
                    "reason": "rail name suggests " + role + "; confirm with explicit port_roles",
                }
            )
    return result


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


def separate_side_labels(labels, pins, font_height):
    """Keep crowded left/right pin labels from sharing a horizontal text band."""
    groups = {}
    for index, pin in enumerate(pins):
        if pin["side"] in {"left", "right"}:
            groups.setdefault(pin["side"], []).append(index)
    minimum = max(0.0625, float(font_height) * 1.2)
    for side, indexes in groups.items():
        occupied = []
        for index in sorted(indexes, key=lambda item: labels[item][1][1]):
            y = labels[index][1][1]
            for column, last_y in enumerate(occupied):
                if abs(y - last_y) >= minimum:
                    break
            else:
                column = len(occupied)
                occupied.append(y)
            occupied[column] = y
            if column:
                shift = 0.25 * column * (1 if side == "left" else -1)
                labels[index][1] = [round(labels[index][1][0] + shift, 8), y]
