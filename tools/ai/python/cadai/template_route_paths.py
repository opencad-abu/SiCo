"""Transform and validate physical paths, including bounded terminal branch repair."""

from .circuit_geometry_schema import EPS, MATRICES, bounds, inside, on_grid, overlap, transform
from .circuit_spec_schema import canonical
from .circuit_wiring import orthogonal_candidates
from .template_terminal_contacts import mapping_gaps
from .template_terminal_contacts import target_endpoint as target_endpoint
from .template_wire_graph import connect_net


def same(left, right):
    return len(left) == len(right) and all(abs(a - b) <= EPS for a, b in zip(left, right))


def instance_frame(source, selection, layout):
    old = source["positions"][selection["source_device"]]
    new = layout["instances"][selection["instance"]]
    for orient in MATRICES:
        if all(same(transform(transform(axis, old["orient"]), orient),
                    transform(axis, new["orientation"])) for axis in ([1, 0], [0, 1])):
            origin = transform([p / source["dbu_per_uu"] for p in old["relative_xy"]], orient)
            return orient, [new["xy"][k] - origin[k] for k in (0, 1)]
    return None


def wire_paths(edges, members, net):
    paths = []
    for edge in edges:
        points = edge["points"]
        owners = sorted({w["endpoint"]["instance"] for w in members
                         if "instance" in w["endpoint"]
                         and any(same(w["points"][0], p) for p in points)})
        paths.append({"endpoint": members[0]["endpoint"], "net": net, "points": points,
                      "label": None, "kind": "route", "owners": owners,
                      "template_segment": True,
                      "template_source": {k: edge[k] for k in ("shape", "segment", "chain")
                                          if k in edge}})
    if paths:
        paths[0]["label"] = {"text": net, "xy": paths[0]["points"][0]}
    return paths


def _outward_contact(points, member):
    """Only a pin's selected outward ray may use its owner's body exemption."""
    anchor = member["points"][0]
    escape = [member["points"][-1][k] - anchor[k] for k in (0, 1)]
    for index, point in enumerate(points):
        if same(anchor, point):
            delta = [points[1 - index][k] - point[k] for k in (0, 1)]
            return (abs(delta[0] * escape[1] - delta[1] * escape[0]) <= EPS
                    and sum(delta[k] * escape[k] for k in (0, 1)) > EPS)
    return False


def path_bends(paths, grid):
    """Count degree-two corners across split template edges, excluding junctions."""
    graph = {}
    for wire in paths:
        if not wire.get("template_segment"):
            continue
        a, b = [tuple(round(v / grid) for v in p) for p in wire["points"]]
        graph.setdefault((wire["net"], a), set()).add(b)
        graph.setdefault((wire["net"], b), set()).add(a)
    count = 0
    for (_, point), neighbors in graph.items():
        if len(neighbors) == 2:
            a, b = neighbors
            count += (a[0] == point[0]) != (b[0] == point[0])
    return count


def obstacle_gaps(paths, members, layout, placed, pins, reserved=()):
    """Common geometric guards, also usable while assembling a complete graph."""
    gaps = set()
    if not (all(on_grid(p, layout["grid"]) for w in paths for p in w["points"])
            and all(on_grid(w["points"][0], layout["grid"]) for w in members)):
        return ["template_path_off_grid"]
    for wire in paths:
        for a, b in zip(wire["points"], wire["points"][1:]):
            box = bounds([a, b])
            clearance = layout.get("clearance", 0)
            for key, inst in placed.items():
                if key not in wire["owners"] and overlap(box, inst["bbox"], clearance):
                    gaps.add("template_path_hits_instance")
                elif key in wire["owners"] and _crosses_interior(a, b, inst["bbox"]):
                    contacts = [m for m in members if m.get("endpoint", {}).get("instance") == key]
                    if not any(_outward_contact([a, b], m) for m in contacts):
                        gaps.add("template_path_crosses_owner_body")
            if any(pin["net"] != wire["net"] and inside(pin["xy"], box) for pin in pins):
                gaps.add("template_path_hits_other_net_pin")
            for other in reserved:
                if other["net"] != wire["net"] and any(
                    overlap(box, bounds([c, d]), clearance)
                    for c, d in zip(other["points"], other["points"][1:])
                ):
                    gaps.add("template_paths_different_nets_touch")
    return sorted(gaps)


def path_gaps(paths, members, layout, placed, pins, reserved=()):
    gaps = set(obstacle_gaps(paths, members, layout, placed, pins, reserved))
    if "template_path_off_grid" in gaps:
        return sorted(gaps)
    checked = connect_net(
        [{"shape": i, "segment": 0,
          "points": [[round(v / layout["grid"]) for v in p] for p in w["points"]]}
         for i, w in enumerate(paths)],
        [{"xy": [round(v / layout["grid"]) for v in w["points"][0]],
          "endpoint": w.get("endpoint")} for w in members], shared_terminals=True,
    )
    gaps.update(checked["gaps"])
    return sorted(gaps)


def graph_preserved(edges, anchors, source_vertices, grid):
    """Reject collapsed vertices, new contacts and overlap, even on the same net."""
    vertices = {tuple(p) for e in edges for p in e["points"]}
    if len(vertices) != source_vertices:
        return False
    segments = [{**e, "points": [[round(v / grid) for v in p] for p in e["points"]]}
                for e in edges]
    checked = connect_net(segments, [
        {**a, "xy": [round(v / grid) for v in a["xy"]]} for a in anchors
    ], shared_terminals=True)
    def signature(rows):
        return sorted(tuple(sorted(tuple(p) for p in e["points"])) for e in rows)
    return not checked["gaps"] and signature(segments) == signature(checked["edges"])


def _crosses_interior(a, b, box):
    # Boundary tangents are legitimate schematic connections; only a positive
    # length inside the body requires the selected terminal escape direction.
    axis = 0 if abs(a[1] - b[1]) <= EPS else 1
    fixed = 1 - axis
    return (box[0][fixed] + EPS < a[fixed] < box[1][fixed] - EPS
            and max(min(a[axis], b[axis]), box[0][axis]) + EPS
            < min(max(a[axis], b[axis]), box[1][axis]))


def transformed_edges(src, source, frame):
    return [{**e, "points": [transform([p / source["dbu_per_uu"] for p in xy], *frame)
                             for xy in e["points"]]} for e in src["edges"]]


def repair_branches(src, source, use, members, layout, placed, pins, reserved):
    """Keep the reference trunk; repair moved degree-one terminals only, never junctions.

    Frames follow a mapped instance's actual orientation/origin. Candidate and
    work limits are fixed; each branch uses target escape and source branch end.
    """
    if mapping_gaps(src["anchors"], use, members):
        return None
    targets = {canonical(w["endpoint"]): w for w in members}
    frames = []
    for selection in use["devices"]:
        frame = instance_frame(source, selection, layout)
        if frame and frame not in frames:
            frames.append(frame)
    for frame in frames[:8]:
        edges = transformed_edges(src, source, frame)
        degree = {}
        for edge in edges:
            for p in edge["points"]:
                degree[tuple(p)] = degree.get(tuple(p), 0) + 1
        moved = {}
        for anchor in src["anchors"]:
            old = transform([p / source["dbu_per_uu"] for p in anchor["xy"]], *frame)
            member = targets[canonical(target_endpoint(anchor["endpoint"], use))]
            if not same(old, member["points"][0]):
                moved.setdefault(tuple(old), []).append(member)
        if not moved or len(moved) > 16 or any(degree.get(p) != 1 for p in moved):
            continue
        failed = False
        for old_key, contacts in moved.items():
            old = list(old_key)
            member = sorted(contacts, key=lambda m: canonical(m["endpoint"]))[0]
            # Replacing one branch changes the edge list.  Keep the original
            # branch contact as a stable marker and skip an already replaced
            # branch instead of allowing ``next`` to leak StopIteration when
            # two terminals share a short source edge.
            idx = next((i for i, e in enumerate(edges) if old in e["points"]), None)
            if idx is None:
                failed = True
                break
            edge = edges[idx]
            fixed = next(p for p in edge["points"] if p != old)
            start = member["points"][0]
            guides = {
                "x": [old[0]],
                "y": [old[1]],
            }
            clearance = layout.get("clearance", 0)
            for obstacle in placed.values():
                box = obstacle["bbox"]
                guides["x"].extend([box[0][0] - clearance - layout["grid"],
                                    box[1][0] + clearance + layout["grid"]])
                guides["y"].extend([box[0][1] - clearance - layout["grid"],
                                    box[1][1] + clearance + layout["grid"]])
            candidates = []
            escapes = sorted({tuple(m["points"][-1]) for m in contacts})
            for escape in escapes[:8]:
                escape_end = list(escape)
                candidates.extend(([[escape_end]] if same(escape_end, fixed) else
                                   orthogonal_candidates(escape_end, fixed,
                                       layout["routing"].get("max_bends", 3),
                                       layout["grid"], guides))[:32])
            chosen = None
            for candidate in candidates:
                full = [start, *candidate]
                length = sum(abs(b[0]-a[0])+abs(b[1]-a[1]) for a, b in zip(full, full[1:]))
                direct = abs(start[0]-fixed[0])+abs(start[1]-fixed[1])
                if length > layout["routing"].get("max_detour", 2.5)*max(direct, layout["grid"]):
                    continue
                replacements = [{**edge, "points": [a, b]} for a, b in zip(full, full[1:])]
                paths = wire_paths(replacements, members, member["net"])
                # Per-branch connectivity is checked with its fixed trunk contact.
                endpoints = [*contacts, {"points": [fixed]}]
                if not path_gaps(paths, endpoints, layout, placed, pins, reserved):
                    chosen = replacements
                    break
            if chosen is None:
                failed = True
                break
            edges[idx:idx+1] = chosen
        if not failed:
            paths = wire_paths(edges, members, members[0]["net"])
            if not path_gaps(paths, members, layout, placed, pins, reserved):
                return paths, frame
    return None
