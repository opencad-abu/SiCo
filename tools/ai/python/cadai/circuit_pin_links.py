"""Physical pin links and explicit basic/gnd coverage; no automatic global shortcuts."""

from .circuit_geometry_schema import EPS
from .circuit_spec_schema import CircuitSpecError, Problems, canonical


def pin_links(plan, wires, layout):
    spec = plan["spec"]
    ground_nets = {n["name"] for n in spec["nets"] if n["scope"] == "ground"}
    instances = {i["id"]: i for i in spec["instances"]}
    masters = {m["id"]: m for m in plan["bindings"]["masters"]}
    problems = Problems("pin links")
    grounds = set()
    for key, inst in instances.items():
        target = masters[inst["master"]]["target"]
        if target == dict(library="basic", cell="gnd", view="symbol"):
            if (
                set(inst["connections"]) != {"gnd!"}
                or inst["connections"]["gnd!"] not in ground_nets
            ):
                problems.add("basic/gnd must connect to the explicit ground-scope net", key)
                continue
            grounds.add(key)
    if ground_nets and not grounds:
        problems.add("ground scope requires actual basic/gnd instances")
    by_endpoint = {canonical(w["endpoint"]): w for w in wires}
    used, linked = set(), []
    for pair in layout.get("pin_links", []):
        keys = [canonical(e) for e in pair]
        label = " -> ".join(str(e) for e in pair)
        if keys[0] == keys[1] or any(k in used or k not in by_endpoint for k in keys):
            problems.add("pin_links require unique connected endpoints", label)
            continue
        first, second = (by_endpoint[k] for k in keys)
        if first["net"] != second["net"]:
            problems.add("physical pin link cannot join different nets", label)
            continue
        a, b = first["points"][0], second["points"][0]
        delta = [b[k] - a[k] for k in (0, 1)]
        if sum(abs(v) > EPS for v in delta) != 1:
            problems.add("pin_links currently require distinct collinear pins", label)
            continue
        axis = 0 if abs(delta[0]) > EPS else 1
        misaligned = False
        for wire, sign in ((first, 1), (second, -1)):
            vec = [wire["points"][1][k] - wire["points"][0][k] for k in (0, 1)]
            if abs(vec[1 - axis]) > EPS or vec[axis] * delta[axis] * sign <= 0:
                problems.add("pin link must follow both outward pin escapes", label)
                misaligned = True
                break
        if misaligned:
            continue
        if first["net"] in ground_nets and sum(e["instance"] in grounds for e in pair) != 1:
            problems.add("each grounded terminal needs a direct basic/gnd pin link", label)
            continue
        used.update(keys)
        linked.append(
            dict(
                endpoint=pair[0], end_endpoint=pair[1], net=first["net"], points=[a, b], label=None
            )
        )
    for wire in wires:
        if wire["net"] in ground_nets and canonical(wire["endpoint"]) not in used:
            problems.add("every ground endpoint requires a physical basic/gnd pin link",
                         canonical(wire["endpoint"]))
    problems.raise_if_any()
    return [w for w in wires if canonical(w["endpoint"]) not in used] + linked
