"""Retain public relative geometry while dropping labels and annotations."""

from .codec import fail
from .projection_content import label


def geometry(raw, docs, ids, ref, part, take):
    for role in ("placement", "symbol"):
        if role not in raw:
            continue
        old = raw[role]
        frame = take(old["frame"], "unit dbu_per_uu anchor_method")
        if frame["anchor_method"] != "instance-min-or-view-origin-v1":
            fail("unsupported public coordinate frame")
        out = part(role, frame=frame, anchors={})
        if role == "placement":
            out.update(
                instances={
                    ref("topology", "devices", k): take(r, "xy orient bbox")
                    for k, r in old["instances"].items()
                },
                annotations={},
            )
            if "rounding" in old:
                out["rounding"] = dict(
                    method="nearest-dbu-half-up-v1", max_error_dbu=0.5, objects=[]
                )
        else:
            out.update(terminals={}, outline={}, labels={})
            for key, ident in ids(role, "terminals", "t").items():
                row = old["terminals"][key]
                value = take(row, "direction width") | dict(name=label(row["name"]))
                if "port" in row:
                    value["port"] = ref("topology", "ports", row["port"])
                out["terminals"][ident] = value
            for key, ident in ids(role, "outline", "o").items():
                out["outline"][ident] = take(
                    old["outline"][key], "kind points ellipse_bbox start_angle stop_angle"
                )
        for key, ident in ids(role, "anchors", "a").items():
            row = old["anchors"][key]
            value = take(row, "xy escape")
            if role == "symbol":
                value["terminal"] = ref(role, "terminals", row["terminal"])
            elif "device" in row:
                value.update(
                    device=ref("topology", "devices", row["device"]),
                    terminal=label(row["terminal"]),
                )
            else:
                value["port"] = ref("topology", "ports", row["port"])
            out["anchors"][ident] = value
    if "routing" not in raw:
        return
    old = raw["routing"]
    out = part("routing", placement_ref="placement.json", nodes={}, segments={}, crossings={})
    for field, prefix in [("nodes", "v"), ("segments", "s"), ("crossings", "x")]:
        ids("routing", field, prefix)
    for key, row in old["nodes"].items():
        out["nodes"][ref("routing", "nodes", key)] = (
            dict(anchors=[ref("placement", "anchors", a) for a in row["anchors"]])
            if "anchors" in row
            else take(row, "xy")
        )
    for key, row in old["segments"].items():
        if "width" in row and type(row["width"]) not in (int, float):
            fail("invalid public segment width")
        value = take(row, "width") | dict(net=ref("topology", "nets", row["net"]))
        value.update({k: ref("routing", "nodes", row[k]) for k in ("from", "to")})
        out["segments"][ref("routing", "segments", key)] = value
    for key, row in old["crossings"].items():
        value = dict(
            relation=row["relation"],
            segments=[ref("routing", "segments", s) for s in row["segments"]],
        )
        if "node" in row:
            value["node"] = ref("routing", "nodes", row["node"])
        out["crossings"][ref("routing", "crossings", key)] = value
