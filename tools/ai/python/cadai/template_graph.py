"""Deterministic reference normalization and bounded typed graph matching."""

from __future__ import annotations

import copy
import time
from collections import Counter, defaultdict

from .template_classification import SEMANTICS, classification_rules
from .template_coords import Coordinates, instance_anchor
from .template_schema import (
    COORDINATE_SPACE,
    TemplateError,
    TemplateUnavailable,
    canonical,
    digest,
)

MATCHER_VERSION = "typed-vf2.networkx-3.2.1.v2"


def normalize_schematic(header, rows, classifications=None):
    instances = sorted((r for r in rows if r["kind"] == "instance"), key=lambda r: r["name"])
    classify, rule_version = classification_rules(header, instances, classifications)
    net_rows = sorted((r for r in rows if r["kind"] == "net"), key=lambda r: r["name"])
    terms = sorted((r for r in rows if r["kind"] == "terminal"), key=lambda r: r["name"])
    dbu = header.get("dbu_per_uu")
    anchor = instance_anchor(instances, dbu)
    endpoints, properties = defaultdict(list), defaultdict(list)
    for r in rows:
        if r["kind"] == "instance_terminal":
            endpoints[r["instance"]].append(r)
        elif r["kind"] == "instance_property":
            properties[r["instance"]].append(
                {k: v for k, v in r.items() if k not in {"kind", "instance"}}
            )
    net_ids = {r["name"]: "n" + str(i) for i, r in enumerate(net_rows)}
    nets = [
        {
            "id": net_ids[r["name"]],
            "source_name": r["name"],
            "num_bits": r.get("numBits", 1),
            "is_global": r.get("is_global", False),
            "sig_type": r.get("sigType"),
        }
        for r in net_rows
    ]
    devices, decorative, places, gaps = [], [], [], []
    coordinates = Coordinates(dbu, anchor, gaps)
    if dbu is None:
        gaps.append({"code": "coordinate_scale_missing"})
    for inst in instances:
        name = inst["name"]
        eps = sorted(endpoints[name], key=lambda r: r["name"])
        role, kind, attrs = classify(inst, {e["name"] for e in eps})
        if role in {"port_graphic", "decoration", "no_connect", "supply_marker"}:
            decorative.append({"source_name": name, "role": role, "kind": kind})
            if header.get("classification_semantics") == SEMANTICS:
                decorative[-1].update(master={"library": inst["libName"],
                                              "cell": inst["cellName"], "view": inst["viewName"]},
                                      builtin_library=inst["builtin_library"],
                                      master_view_type=inst["master_view_type"])
            continue
        key = "d" + str(len(devices))
        inventory = inst.get("terminal_names") or []
        eps = eps + [
            {"name": n, "net": None} for n in inventory if n not in {e["name"] for e in eps}
        ]
        pins = [
            {"name": e["name"], "net": net_ids[e["net"]] if e.get("net") is not None else None}
            for e in eps
        ]
        devices.append(
            {
                "id": key,
                "source_name": name,
                "role": role,
                "kind": kind,
                "attributes": attrs,
                "master": {
                    "library": inst["libName"],
                    "cell": inst["cellName"],
                    "view": inst["viewName"],
                },
                "pins": pins,
                "observed_property_names": [p["name"] for p in properties[name]],
                "master_available": inst.get("master_available", False),
            }
        )
        if classifications and name in classifications:
            devices[-1]["classification_evidence"] = copy.deepcopy(classifications[name])
        places.append(
            {
                "device": key,
                "source_name": name,
                "relative_xy": coordinates.point(inst.get("xy")),
                "orient": inst.get("orient"),
                "bbox": coordinates.box(inst.get("bbox")),
            }
        )
        if role == "unknown":
            gaps.append({"code": "unknown_device_type", "instance": name})
        if role == "hierarchy":
            gaps.append({"code": "hierarchy_not_expanded", "instance": name})
        if inst.get("terminal_names") is None:
            gaps.append({"code": "terminal_inventory_unverified", "instance": name})
        if not pins:
            gaps.append({"code": "no_saved_instance_terminals", "instance": name})
        if not inst.get("master_available"):
            gaps.append({"code": "missing_master", "instance": name})
    ports = [
        {
            "id": "p" + str(i),
            "name": r["name"],
            "net": net_ids[r["net"]] if r.get("net") is not None else None,
            "direction": r.get("direction"),
            "num_bits": r.get("numBits", 1),
        }
        for i, r in enumerate(terms)
    ]
    for gap in header.get("incomplete_features", []):
        gaps.append({"code": gap})
    topology = {
        "level": "direct",
        "devices": devices,
        "nets": nets,
        "ports": ports,
        "ignored_graphics": decorative,
        "rule_version": rule_version,
        "net_global_semantics": header.get("net_global_semantics", "legacy_boolean"),
        "gaps": gaps,
    }
    topology["fingerprint"] = fingerprint(topology)
    topology["counts"] = {
        "devices": len(devices),
        "nets": len(nets),
        "ports": len(ports),
        "device_kinds": dict(sorted(Counter(d["kind"] for d in devices).items())),
    }
    rounding_gap = coordinates.rounded_gap()
    if rounding_gap:
        gaps.append(rounding_gap)
    placement_bbox = coordinates.box(header.get("bbox"))
    placement = {
        "coordinate_space": COORDINATE_SPACE,
        "dbu_per_uu": dbu,
        "anchor": {"kind": "instance_min", "source_xy_dbu": list(anchor)},
        "rounding": coordinates.report(),
        "bbox": placement_bbox,
        "instances": places,
        "shapes": [coordinates.convert(r) for r in rows if r["kind"] == "shape"],
        "properties": [coordinates.convert(r) for r in rows if r["kind"] == "instance_property"],
        "pins": [coordinates.convert(r) for r in rows if r["kind"] == "pin"],
        "masters": [coordinates.convert(r) for r in rows if r["kind"] == "master_geometry"],
        "gaps": gaps,
    }
    return topology, placement


def graph_data(topology, port_map=None):
    """Explicit terminal nodes preserve multigraph connectivity and pin identity."""
    labels, edges = {}, []
    port_map = port_map or {}
    net_ids = {n["id"] for n in topology["nets"]}
    for net in topology["nets"]:
        # Global names remain electrical identities, unlike internal local net names.
        labels["n:" + net["id"]] = (
            "net",
            net["num_bits"],
            net["is_global"],
            net["sig_type"],
            net["source_name"] if net["is_global"] else None,
        )
    for dev in topology["devices"]:
        d = "d:" + dev["id"]
        labels[d] = ("device", dev["kind"], dev["role"], canonical(dev["attributes"]))
        for pin in dev["pins"]:
            p = d + ":" + pin["name"]
            labels[p] = ("terminal", pin["name"], pin["net"] is None)
            edges.append((d, p))
            if pin["net"] is not None:
                if pin["net"] not in net_ids:
                    raise TemplateError("dangling device endpoint")
                edges.append((p, "n:" + pin["net"]))
    for port in topology["ports"]:
        p = "p:" + port["id"]
        labels[p] = (
            "port",
            port_map.get(port["name"], port["name"]),
            port["direction"],
            port["num_bits"],
            port["net"] is None,
        )
        if port["net"] is not None:
            if port["net"] not in net_ids:
                raise TemplateError("dangling port endpoint")
            edges.append((p, "n:" + port["net"]))
    return labels, edges


def fingerprint(topology):
    labels, edges = graph_data(topology)
    adjacent = defaultdict(list)
    for a, b in edges:
        adjacent[a].append(b)
        adjacent[b].append(a)
    colors = {n: digest(label) for n, label in labels.items()}
    for _ in range(4):
        colors = {n: digest([colors[n], sorted(colors[x] for x in adjacent[n])]) for n in colors}
    return digest(sorted(colors.values()))


def match(left, right, port_map=None, timeout=2.0, max_states=50000):
    """Use the tested VF2 implementation; never equate a digest with proof."""
    if any(
        any(n["num_bits"] > 1 for n in top["nets"])
        and top.get("net_global_semantics") != "uniform_boolean_v1"
        for top in (left, right)
    ):
        return {
            "status": "inconclusive",
            "matched": False,
            "reason": "recapture_bus_template_with_templates_v4_global_semantics",
        }
    try:
        import networkx as nx
    except ImportError as exc:
        raise TemplateUnavailable("Install cadai[templates] for networkx==3.2.1 matching") from exc
    if nx.__version__ != "3.2.1":
        raise TemplateUnavailable("Qualified topology matcher requires networkx==3.2.1")
    names = {p["name"] for p in left["ports"]}
    target_names = {p["name"] for p in right["ports"]}
    port_map = port_map or {}
    if set(port_map) - names or set(port_map.values()) - target_names:
        raise TemplateError("port_map refers to absent ports")
    renamed = [port_map.get(n, n) for n in names]
    if len(set(renamed)) != len(renamed):
        raise TemplateError("port_map is not one-to-one")
    graphs = []
    for topology, rename in ((left, port_map), (right, {})):
        labels, edges = graph_data(topology, rename)
        if len(labels) > 512:
            return {"status": "inconclusive", "reason": "graph_exceeds_512_nodes", "matched": False}
        g = nx.Graph()
        g.add_nodes_from((n, {"label": canonical(label)}) for n, label in labels.items())
        g.add_edges_from(edges)
        graphs.append(g)
    deadline = time.monotonic() + timeout

    class BoundedMatcher(nx.algorithms.isomorphism.GraphMatcher):
        states = 0

        def candidate_pairs_iter(self):
            # Preserve VF2 order; reject only necessary label/degree mismatches
            # before spending a search state on syntactic feasibility.
            for a, b in super().candidate_pairs_iter():
                if time.monotonic() > deadline:
                    raise TimeoutError
                if (self.G1.nodes[a]["label"] == self.G2.nodes[b]["label"]
                        and self.G1.degree[a] == self.G2.degree[b]):
                    yield a, b

        def syntactic_feasibility(self, a, b):
            self.states += 1
            if self.states > max_states or time.monotonic() > deadline:
                raise TimeoutError
            return super().syntactic_feasibility(a, b)

    matcher = BoundedMatcher(*graphs, node_match=lambda a, b: a["label"] == b["label"])
    try:
        mapping = next(matcher.isomorphisms_iter(), None)
    except TimeoutError:
        return {
            "status": "inconclusive",
            "matched": False,
            "reason": "search_budget_exceeded",
            "states": matcher.states,
        }
    finally:
        matcher.reset_recursion_limit()
    if mapping is None:
        return {"status": "different", "matched": False, "states": matcher.states}
    return {
        "status": "matched",
        "matched": True,
        "states": matcher.states,
        "mapping": {
            "devices": {d["id"]: mapping["d:" + d["id"]][2:] for d in left["devices"]},
            "nets": {n["id"]: mapping["n:" + n["id"]][2:] for n in left["nets"]},
            "ports": {p["name"]: port_map.get(p["name"], p["name"]) for p in left["ports"]},
        },
        "scope": "saved_direct_level_graph_only; symmetry_may_allow_alternatives",
        "electrical_equivalence_qualified": False,
        "gap_counts": {
            "reference": len(left.get("gaps", [])),
            "target": len(right.get("gaps", [])),
        },
        "ignored": [
            "numeric_parameter_values",
            "source_instance_names",
            "local_net_names",
            "coordinates",
        ],
    }
