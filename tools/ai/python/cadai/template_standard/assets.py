"""Source symbol geometry and native layout references, without reconstruction claims."""

import math

from ..template_coords import valid_box
from .geometry import frame


def symbol(record, env, gap, *, current=False):
    asset = record["assets"]["symbol"]
    coord = frame(asset)
    if coord is None:
        gap("symbol", "frame", "Symbol DBU is unavailable")
        return None
    out = dict(**env, frame=coord, terminals={}, anchors={}, outline={}, labels={})
    if current:
        for field in ("instances", "mosaics"):
            if asset.get(field):
                gap(
                    "symbol",
                    field,
                    "Hierarchical/array symbol graphics are not expanded",
                    "unsupported_feature",
                    "collect_source",
                )
        if asset.get("rounding", {}).get("rounded_values"):
            gap("symbol", "frame", "Symbol rounding impact has not been qualified")
        # Collector scope declarations: the five unconditional ones plus the
        # conditional unnamed-property note. Direct symbol reference neither
        # expands masters/connections nor evaluates labels. Instance absence,
        # literal interface widths and primitive coverage are checked separately.
        scope_limits = {
            "hierarchy_not_expanded",
            "inherited_connections_not_resolved",
            "bus_members_not_expanded",
            "dynamic_text_not_evaluated",
            "master_geometry_not_captured",
            "unnamed_properties_not_recorded",
        }
        for reason in asset.get("source", {}).get("incomplete_features", []):
            if reason not in scope_limits:
                gap("symbol", "source", reason)
    topology_ports = {r["name"]: r for r in record.get("topology", {}).get("ports", [])}
    ids = {}
    for i, row in enumerate(asset["ports"]):
        key = "p" + str(i)
        ids[row["name"]] = key
        port = dict(
            name=row["name"],
            direction=row.get("direction") or "unknown",
            width=row.get("numBits", 1),
        )
        if current and row.get("numBits") is None:
            gap("symbol", key, "Symbol terminal width is unobserved")
            return None
        if current and port["direction"] not in {"input", "output", "inputOutput"}:
            port["direction"] = "unknown"
            gap("symbol", key, "Symbol port direction is unknown or unsupported")
        if row["name"] in topology_ports:
            target = topology_ports[row["name"]]
            port["port"] = target["id"]
            if (port["direction"], port["width"]) != (target["direction"], target["num_bits"]):
                gap("symbol", key, "Symbol direction/width differs from schematic")
        elif "topology" in record:
            gap("symbol", key, "Symbol terminal has no schematic port")
        out["terminals"][key] = port
    if topology_ports.keys() - ids.keys():
        gap("symbol", "terminals", "Schematic ports are missing from symbol")
    for row in asset["pins"]:
        figure = row.get("figure") or {}
        box = figure.get("bBox")
        if current and figure.get("objType") not in {"rect", "ellipse"}:
            gap(
                "symbol",
                row["terminal"],
                "Nonrectangular pin anchor is unsupported",
                "unsupported_feature",
                "upgrade_reader",
            )
            continue
        if not box:
            gap("symbol", row["terminal"], "Symbol pin geometry unavailable")
            continue
        xy = [(box[0][k] + box[1][k]) / 2 for k in (0, 1)]
        if any(int(v) != v for v in xy):
            gap("symbol", row["terminal"], "Pin center requires unqualified DBU rounding")
            continue
        out["anchors"]["a" + str(len(out["anchors"]))] = dict(
            terminal=ids[row["terminal"]], xy=[int(v) for v in xy], escape="unknown"
        )
    for row in asset["shapes"]:
        figure = row.get("figure") or {}
        kind = figure.get("objType")
        if kind in {"line", "path", "polygon", "rect"}:
            points = figure.get("bBox") if kind == "rect" else figure.get("points")
            required = 3 if kind == "polygon" else 2
            if points and (not current or len(points) >= required):
                out["outline"]["o" + str(len(out["outline"]))] = dict(
                    kind="rectangle"
                    if kind == "rect"
                    else "polygon"
                    if kind == "polygon"
                    else "polyline",
                    points=points,
                )
                continue
        elif kind in {"ellipse", "arc"} and current:
            ellipse = figure.get("bBox") if kind == "ellipse" else figure.get("ellipseBBox")
            angles = (figure.get("startAngle"), figure.get("stopAngle"))
            if valid_box(ellipse) and (
                kind == "ellipse"
                or all(type(a) in (int, float) and math.isfinite(a) for a in angles)
            ):
                entry = dict(kind=kind, ellipse_bbox=ellipse)
                if kind == "arc":
                    entry["start_angle"], entry["stop_angle"] = angles
                out["outline"]["o" + str(len(out["outline"]))] = entry
                continue
        elif kind == "label" and all(figure.get(k) is not None for k in ("text", "xy", "bBox")):
            out["labels"]["l" + str(len(out["labels"]))] = dict(
                text=figure["text"],
                xy=figure["xy"],
                bbox=figure["bBox"],
                kind="dynamic_literal" if figure.get("labelType") == "NLPLabel" else "literal",
            )
            continue
        gap(
            "symbol",
            "outline",
            "Unsupported or incomplete symbol drawing retained privately",
            "unsupported_feature",
            "upgrade_reader",
        )
    if current:
        covered = {r["terminal"] for r in out["anchors"].values()}
        if covered != set(out["terminals"]):
            gap("symbol", "anchors", "Every source terminal requires a captured anchor")
        if "topology" not in record:
            # This is an independently captured symbol interface, not an inferred schematic.
            out["source"] = "symbol_interface"
    return out


def native_layout(record, dependencies, resources):
    source = record["assets"]["layout"]["source"]
    target = dict(library=source["lib"], cell=source["cell"], view=source["view"])
    from .topology import unknown

    dependencies["layout"] = dict(
        kind="layout",
        target=target,
        fingerprint=unknown("Native layout file content is not fingerprinted"),
        method="oa-layout-identity-v1",
    )
    resources["layout"] = dict(
        kind="native_asset",
        root="source_library",
        path=source["cell"] + "/" + source["view"],
        purpose="Original OA layout view; source reference only",
    )
    return dict(
        source="capture",
        depends_on=["capture", "layout"],
        asset="layout",
        dependency="layout",
        binding_status="unavailable",
        bindings={},
    )
