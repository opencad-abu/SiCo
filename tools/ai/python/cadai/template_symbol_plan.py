"""Turn a persistent schematic interface into a bounded, deterministic OA symbol plan."""

from __future__ import annotations

import re

from .template_circuit_nets import mapped_nets, scope_evidence
from .template_preview import _write_once, source_svg, symbol_plan
from .template_schema import (
    PIN_POLICY,
    TemplateError,
    TemplateUnavailable,
    canonical,
    digest,
    require_legacy_consumer,
)

VERSION = "cad.template.symbol.v1"
PORT_PATTERN = r"[A-Za-z_][A-Za-z0-9_!]*(?:<[0-9]+(?::[0-9]+)?>)?"
SIGNALS = {
    "analog",
    "clock",
    "ground",
    "supply",
    "reset",
    "scan",
    "signal",
    "testLatch",
    "tieHi",
    "tieLo",
    "tieOff",
}


def members(name):
    if not isinstance(name, str) or len(name) > 96 or not re.fullmatch(PORT_PATTERN, name):
        raise TemplateError("symbol v1 requires simple ASCII scalar or <index[:index]> port names")
    if "<" not in name:
        return [name], ""
    base, suffix = name.split("<", 1)
    indices = [int(s) for s in suffix[:-1].split(":")]
    start, end = indices[0], indices[-1]
    if abs(start - end) >= 4096 or max(indices) > 1000000:
        raise TemplateError("symbol bus exceeds member/index budget")
    step = 1 if end >= start else -1
    return [f"{base}<{i}>" for i in range(start, end + step, step)], "<" + suffix


def make_plan(record, args):
    require_legacy_consumer(record)
    topology = record.get("topology")
    if not topology:
        raise TemplateUnavailable("symbol creation requires a schematic topology template")
    ports, nets = topology["ports"], {n["id"]: n for n in topology["nets"]}
    if (
        any(n["num_bits"] > 1 for n in nets.values())
        and record["source"].get("net_global_semantics") != "uniform_boolean_v1"
    ):
        raise TemplateUnavailable("recapture bus template with templates v4 global semantics")
    if not 1 <= len(ports) <= 128:
        raise TemplateUnavailable("symbol creation supports 1..128 ports")
    names = {p["name"] for p in ports}
    if len(names) != len(ports) or set(args["port_map"]) - names:
        raise TemplateError("duplicate source ports or unknown port_map key")
    scopes = args.get("net_scope_map", {})
    net_names = {key: net["source_name"] for key, net in nets.items()}
    if scopes:
        for key in scopes:
            boundary = [p for p in ports if p["net"] == key]
            targets = {args["port_map"].get(p["name"], p["name"]) for p in boundary}
            if len(targets) != 1 or any(p["num_bits"] != 1 for p in boundary):
                raise TemplateError("localized symbol net requires one named scalar boundary")
            net_names[key] = next(iter(targets))
        mapped_nets(nets, {p["name"]: p for p in ports}, net_names, scopes)
    mapped, used = [], set()
    for port in ports:
        name = args["port_map"].get(port["name"], port["name"])
        original, suffix = members(port["name"])
        expanded, mapped_suffix = members(name)
        if mapped_suffix != suffix or len(original) != port["num_bits"]:
            raise TemplateError("bus indices/order and captured width must be preserved")
        if port["net"] not in scopes and ("!" in port["name"]) != ("!" in name):
            raise TemplateError("port_map cannot add/remove the global name marker")
        if used.intersection(expanded):
            raise TemplateError("mapped ports have overlapping names or bus members")
        used.update(expanded)
        if len(used) > 4096:
            raise TemplateError("symbol exceeds 4096 total member terminals")
        net = nets.get(port["net"])
        if not net or type(net.get("is_global")) is not bool or net.get("sig_type") not in SIGNALS:
            raise TemplateUnavailable("template port net/global/signal semantics are incomplete")
        if net["is_global"]:
            members(net["source_name"])
        if port["direction"] not in PIN_POLICY:
            raise TemplateUnavailable("unsupported symbol direction: " + str(port["direction"]))
        mapped.append(
            {
                "name": name,
                "source_name": port["name"],
                "direction": port["direction"],
                "num_bits": port["num_bits"],
                "sig_type": net["sig_type"],
                "global_net": net["source_name"]
                if net["is_global"] and port["net"] not in scopes
                else None,
            }
        )
    layout = symbol_plan(mapped, args["cell"])
    if not all(
        layout["checks"][k]
        for k in ("unique_anchors", "all_ports_placed", "unique_port_names", "on_grid")
    ):
        raise TemplateUnavailable("symbol geometry checks failed")
    # 5 preview units = one 0.0625 UU grid step. OA y points upwards.
    width, height = layout["width"] / 80, layout["height"] / 80
    rows = []
    for p in layout["pins"]:
        right = p["side"] == "right"
        rows.append(
            [
                p["name"],
                p["direction"],
                p["num_bits"],
                p["global_net"],
                p["sig_type"],
                width + 0.3125 if right else -0.3125,
                -p["y"] / 80,
                right,
            ]
        )
    plan = {
        "schema_version": VERSION,
        "template_ref": record["template_ref"],
        "target": [args["library"], args["cell"], "symbol"],
        "title": args["cell"],
        "source": {k: record["source"][k] for k in ("lib", "cell", "library_path")},
        "port_map": args["port_map"],
        "ports": mapped,
        "rows": rows,
        "width": width,
        "height": height,
        "grid": 0.0625,
        "font_height": 0.125,
        "pin_pitch": 0.375,
        "fixed_annotations": ["cdsTerm(terminal_name)", "[@instanceName]"],
        "pin_policy": PIN_POLICY,
        "style": "rectangular_module",
        "interface_basis": "schematic_template",
        "source_symbol_differences": record.get("assets", {})
        .get("symbol", {})
        .get("interface_differences", []),
        "global_policy": "verify_target_schematic_global_nets; symbol_nets_use_terminal_names",
        "qualification": "planned_geometry; inspect_actual_Cadence_rendering",
    }
    if scopes:
        plan["net_scope_map"] = scopes
        plan["net_scope_changes"] = scope_evidence(
            nets,
            {p["name"]: p for p in ports},
            {
                "net_scope_map": scopes,
                "net_map": net_names,
                "port_map": {p["name"]: args["port_map"].get(p["name"], p["name"]) for p in ports},
            },
        )
    if len(canonical(plan).encode()) > 75000:
        raise TemplateUnavailable("symbol plan exceeds response budget")
    if args.get("style") == "template_outline":
        from .template_symbol_outline import outline_plan

        return outline_plan(record, plan, args.get("outline_scale", 1), args.get("port_roles", {}))
    if args.get("port_roles"):
        raise TemplateError("port_roles requires style=template_outline")
    return {**plan, "preview_digest": digest(plan)}


def write_preview(plan, root):
    """Render the actual OA plan; never interpret labels taken from a reference."""
    directory = root / (plan["preview_digest"] + "-symbol-v1")
    directory.mkdir(parents=True, exist_ok=True)
    if plan["style"] == "template_outline":
        from .template_symbol_outline import outline_shapes

        lo, hi = plan["selection_box"]
        svg, _ = source_svg(
            {
                "bbox": [[lo[0] - 1, lo[1] - 0.5], [hi[0] + 1, hi[1] + 0.5]],
                "shapes": outline_shapes(plan),
            },
            "symbol_plan",
        )
        return [
            _write_once(directory / "plan.json", canonical(plan) + "\n"),
            _write_once(directory / "symbol.svg", svg),
        ]
    width, height = plan["width"], plan["height"]
    shapes = [{"figure": {"objType": "rect", "bBox": [[0, -height], [width, 0]]}}]

    def label(text, xy, justify="centerLeft"):
        shapes.append(
            {
                "figure": {
                    "objType": "label",
                    "text": text,
                    "xy": xy,
                    "height": 0.125,
                    "justify": justify,
                }
            }
        )

    for name, _, _, _, _, x, y, right in plan["rows"]:
        shapes.extend(
            [
                {"figure": {"objType": "line", "points": [[x, y], [width if right else 0, y]]}},
                {
                    "figure": {
                        "objType": "rect",
                        "bBox": [[x - 0.025, y - 0.025], [x + 0.025, y + 0.025]],
                    }
                },
            ]
        )
        label(
            name, [width - 0.125 if right else 0.125, y], "centerRight" if right else "centerLeft"
        )
    label(plan["title"], [width / 2, 0.375], "centerCenter")
    label("[@instanceName]", [0, -height - 0.375], "upperLeft")
    svg, _ = source_svg(
        {"bbox": [[-0.5, -height - 0.75], [width + 0.5, 0.75]], "shapes": shapes}, "symbol_plan"
    )
    return [
        _write_once(directory / "plan.json", canonical(plan) + "\n"),
        _write_once(directory / "symbol.svg", svg),
    ]
