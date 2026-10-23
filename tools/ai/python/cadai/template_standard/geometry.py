"""Project observed geometry only; incomplete geometry remains a reference."""

from ..circuit_geometry_schema import transform
from ..template_coords import valid_box


def frame(asset):
    dbu = asset.get("dbu_per_uu")
    if type(dbu) not in (int, float) or dbu <= 0 or int(dbu) != dbu:
        return None
    return dict(
        unit="source_dbu", dbu_per_uu=int(dbu), anchor_method="instance-min-or-view-origin-v1"
    )


def placement(record, env, gap):
    asset = record["assets"]["schematic"]
    coord = frame(asset)
    if coord is None:
        gap("placement", "frame", "Source DBU is unavailable")
        return None
    out = dict(**env, frame=coord, instances={}, anchors={}, annotations={})
    wire = asset.get("wire_reference", {})
    geometries = {r["device"]: r for r in wire.get("geometry", [])}
    for place in asset["instances"]:
        key = place["device"]
        xy, orient = place.get("relative_xy"), place.get("orient")
        geom = geometries.get(key, {})
        box = geom.get("occupied_bbox")
        if xy is None or orient is None:
            gap("placement", key, "Source transform is incomplete")
            continue
        if valid_box(box):
            corners = [
                transform([x, y], orient, xy)
                for x in (box[0][0], box[1][0])
                for y in (box[0][1], box[1][1])
            ]
            box = [
                [min(p[k] for p in corners) for k in (0, 1)],
                [max(p[k] for p in corners) for k in (0, 1)],
            ]
        else:
            box = place.get("bbox")
        if valid_box(box):
            out["instances"][key] = dict(xy=xy, orient=orient, bbox=box)
        else:
            gap("placement", key, "Source occupancy is unavailable")
        for term in geom.get("terminals", []):
            for anchor in term["anchors"]:
                out["anchors"]["a" + str(len(out["anchors"]))] = dict(
                    device=key,
                    terminal=term["name"],
                    xy=transform(anchor["xy"], orient, xy),
                    escape="unknown",
                )
    ports = {p["name"]: p["id"] for p in record["topology"]["ports"]}
    for row in wire.get("ports", []):
        out["anchors"]["a" + str(len(out["anchors"]))] = dict(
            port=ports[row["port"]], xy=row["xy"], escape="unknown"
        )
    if not wire or wire.get("gaps"):
        gap("placement", "anchors", "Endpoint geometry is missing or has capture gaps")
    rounding = asset.get("rounding", {})
    if rounding.get("rounded_values") or any(
        r.get("code") == "source_wire_coordinate_rounded" for r in wire.get("gaps", [])
    ):
        # Half-DBU is a safe bound for this explicitly versioned rounding method.
        out["rounding"] = dict(
            method="nearest-dbu-half-up-v1",
            max_error_dbu=0.5,
            objects=sorted([*out["instances"], *out["anchors"]]),
        )
        gap("placement", "rounding", "Rounding impact has not been qualified")
    return out


def routing(record, place, env, gap):
    out = dict(**env, placement_ref="placement.json", nodes={}, segments={}, crossings={})
    nets = {n["source_name"]: n["id"] for n in record["topology"]["nets"]}
    by_xy = {}
    for key, row in place["anchors"].items():
        by_xy.setdefault(tuple(row["xy"]), []).append(key)
    nodes = {}

    def node(xy, net):
        key = (tuple(xy), net)
        if key not in nodes:
            ident = "v" + str(len(nodes))
            nodes[key] = ident
            anchors = sorted(by_xy.get(tuple(xy), []))
            out["nodes"][ident] = dict(anchors=anchors) if anchors else dict(xy=xy)
        return nodes[key]

    for row in record["assets"]["schematic"].get("shapes", []):
        figure = row.get("figure") or {}
        if (figure.get("lpp") or [""])[0] != "wire" or figure.get("objType") == "label":
            continue
        points, net = figure.get("points"), nets.get(row.get("net"))
        if not net or figure.get("objType") not in {"line", "path"} or not points:
            gap("routing", "segments", "Unsupported wire shape or unknown OA net ownership")
            continue
        for a, b in zip(points, points[1:]):
            if a == b or (a[0] != b[0] and a[1] != b[1]):
                gap("routing", "segments", "Non-orthogonal or zero length source segment")
                continue
            out["segments"]["s" + str(len(out["segments"]))] = dict(
                net=net, **{"from": node(a, net), "to": node(b, net)}
            )

    # Never infer contact from coordinates. Keep uncertain crossings explicit.
    def xy(key):
        row = out["nodes"][key]
        return row["xy"] if "xy" in row else place["anchors"][row["anchors"][0]]["xy"]

    segments = list(out["segments"].items())
    if len(segments) > 512:
        gap(
            "routing",
            "crossings",
            "Crossing analysis exceeds this collector's work budget",
            "unsupported_feature",
            "upgrade_reader",
        )
        return out
    for i, (left, a) in enumerate(segments):
        p, q = xy(a["from"]), xy(a["to"])
        for right, b in segments[i + 1 :]:
            r, s = xy(b["from"]), xy(b["to"])
            if all(
                max(min(p[k], q[k]), min(r[k], s[k])) <= min(max(p[k], q[k]), max(r[k], s[k]))
                for k in (0, 1)
            ):
                out["crossings"]["x" + str(len(out["crossings"]))] = dict(
                    segments=[left, right], relation="unknown"
                )
    gap("routing", "crossings", "Full source path/contact qualification is not implemented")
    return out
