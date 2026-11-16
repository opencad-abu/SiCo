"""Capture transport with a declared series branch and its private net/port."""

import copy

from template_extract_fixture import capture_rows, evidence, recount, request


def series_capture():
    rows, args = capture_rows(), request()
    for row in rows:
        if row.get("net") == "Y":
            row["net"] = "OUT"
        if row["kind"] in {"net", "terminal"} and row["name"] == "Y":
            row["name"] = "OUT"
        if row.get("terminal") == "Y":
            row["terminal"] = "OUT"
    model = copy.deepcopy(rows[2])
    geo = next(r for r in rows if r["kind"] == "instance_geometry")
    for name, kind, pins in [("R1", "resistor", ["OUT", "MID"]),
                             ("C1", "capacitor", ["MID", "BRANCH"])]:
        instance = dict(model, name=name, cellName=kind, terminal_names=["PLUS", "MINUS"])
        rows[-2:-2] = [instance, dict(geo, instance=name, observation=dict(
            ok=True, occupied_bbox=[[-1, -1], [1, 1]],
            pin_centers=[["PLUS", [[-1, 0]]], ["MINUS", [[1, 0]]]], signature="series")),
            *[dict(kind="instance_terminal", instance=name, name=p, net=n)
              for p, n in zip(["PLUS", "MINUS"], pins)]]
        args["classifications"][name] = evidence(kind)
        rows[1]["electrical_observation"]["views"].append(dict(
            rows[1]["electrical_observation"]["views"][1],
            target=[model["libName"], kind, "symbol"]))
    rows[1]["electrical_observation"]["views"][0]["instance_count"] = 4
    next(r for r in rows if r["kind"] == "net" and r["name"] == "OUT")[
        "instance_terminal_count"] = 3
    for net, count in [("MID", 2), ("BRANCH", 1)]:
        rows[-2:-2] = [dict(kind="net", name=net, numBits=1, sigType="signal",
                            is_global=False, instance_terminal_count=count)]
    rows[-2:-2] = [dict(kind="terminal", name="BRANCH", direction="input", numBits=1, net="BRANCH"),
                    dict(kind="port_anchor", terminal="BRANCH", anchor_id="a0", xy=[4, 0])]
    args["reuse"] = dict(mode="core", allow_rename=False, allow_omit_optional_groups=True,
        allow_add_boundary_group=True, core_instances=["M0", "M1"],
        optional_groups=[dict(id="load", instances=["R1", "C1"])], boundary_terminals=[dict(
            endpoint=dict(instance="M0", terminal="D"),
            allowed_device_kinds=["resistor", "capacitor"],
            min_additional_connections=0, max_additional_connections=4)])
    return recount(rows), args
