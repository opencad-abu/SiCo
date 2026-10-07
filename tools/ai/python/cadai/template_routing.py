"""Reuse or deform proven template paths after target mapping and geometry validation."""

import os

from .circuit_geometry_schema import MATRICES, bounds, transform
from .circuit_port_routing import port_obstacles, stub_port
from .circuit_spec_schema import CircuitSpecError, canonical, digest
from .circuit_wiring import routing_metrics
from .template_adapt_schema import device_rows, is_v2
from .template_catalog import TemplateCatalog
from .template_route_coordinate import MAX_PENDING, coordinate_routes
from .template_route_deform import deform_paths
from .template_route_finalize import finalize_routes
from .template_route_paths import (
    path_bends,
    path_gaps,
    repair_branches,
    transformed_edges,
    wire_paths,
)
from .template_route_paths import same as _same
from .template_route_paths import target_endpoint as _target_endpoint
from .template_route_ports import device_reference
from .template_schema import TemplateError
from .template_terminal_contacts import mapping_gaps
from .template_wire_attachment import compile_reference

VERSION = "cad.template.routing.v1"


def _rigid_transform(anchors, targets, source, use, plan, geometry, layout, record):
    dbu = source["dbu_per_uu"]
    mapped = [(_target_endpoint(a["endpoint"], use), [p / dbu for p in a["xy"]]) for a in anchors]
    if not mapped:
        return None
    masters = {m["id"]: m for m in plan["bindings"]["masters"]}
    devices = {d["id"]: d for d in record["topology"]["devices"]}
    for orient in MATRICES:
        endpoint, point = mapped[0]
        rotated = transform(point, orient)
        actual = targets.get(canonical(endpoint))
        if actual is None:
            return None
        offset = [actual["points"][0][k] - rotated[k] for k in (0, 1)]
        if not all(
            canonical(ep) in targets
            and _same(transform(p, orient, offset), targets[canonical(ep)]["points"][0])
            for ep, p in mapped
        ):
            continue
        valid = True
        for device in {a["endpoint"]["device"] for a in anchors if "device" in a["endpoint"]}:
            row = next(d for d in use["devices"] if d["source_device"] == device)
            master = masters[row["master"]]
            body = source["geometry"][device]
            target_geo = geometry[row["instance"]]
            if (
                master["target"] != devices[device]["master"]
                or os.path.realpath(master["library_path"])
                != os.path.realpath(body["library_path"])
                or not _same(
                    [v / dbu for p in body["occupied_bbox"] for v in p],
                    [v for p in target_geo["occupied_bbox"] for v in p],
                )
            ):
                valid = False
                break
            placement = source["positions"][device]
            target_place = layout["instances"][row["instance"]]
            if not _same(
                transform([p / dbu for p in placement["relative_xy"]], orient, offset),
                target_place["xy"],
            ):
                valid = False
                break
            for axis in ([1, 0], [0, 1]):
                if not _same(
                    transform(transform(axis, placement["orient"]), orient),
                    transform(axis, target_place["orientation"]),
                ):
                    valid = False
        if valid:
            return orient, offset
    return None


def route_template(plan, geometry, layout, stubs, placed, pins, *, workspace=None):
    """Fail closed per net. Returned paths use the existing draw-wire payload/backend."""
    use = plan.get("template_use")
    if use is None:
        raise CircuitSpecError("template routing requires verified template_use")
    routing = layout["routing"]
    if "pg" in routing:
        raise CircuitSpecError("template routing routes all nets; pg override is unsupported")
    if layout.get("pin_links"):
        raise CircuitSpecError("template routing cannot combine unverified pin_links")
    record = TemplateCatalog(workspace=workspace).get(use["template_ref"])
    if is_v2(use):
        # Geometry consumes a transient row view only after attach_template has
        # revalidated the full v2 proof. The persisted use remains v2.
        use = {"template_ref": use["template_ref"], "devices": device_rows(use, plan["spec"]),
               "net_map": use["net_map"], "port_map": use["port_map"]}
    by_net = {}
    for wire in stubs:
        by_net.setdefault(wire["net"], []).append(wire)
    planned, report, issues, pending = {}, [], [], {}
    try:
        source = compile_reference(record)
    except TemplateError as exc:
        source, source_error = None, str(exc)
    else:
        source_error = None
        if source["reference_digest"] != plan["template_evidence"].get("wire_reference_digest"):
            raise CircuitSpecError("wire reference changed since structural preview")
    reverse_nets = {target: src for src, target in use["net_map"].items()}
    if any(stub_port(w, layout) for w in stubs):
        body = bounds([p for row in placed.values() for p in row["bbox"]])
        placed = {**placed, **{k: {"bbox": box} for k, box in
                              port_obstacles(plan, layout, body, stubs).items()}}
    port_stubs = {}
    for net, members in sorted(by_net.items()):
        port_stubs[net] = [w for w in members if stub_port(w, layout)]
        members = [w for w in members if not stub_port(w, layout)]
        if len(members) < 2 and port_stubs[net]:
            planned[net] = members
            report.append(dict(net=net, status="stub", terminals=len(members),
                               reasons=["port_stub_policy"]))
            continue
        src = source["nets"].get(reverse_nets.get(net)) if source else None
        if src and port_stubs[net]:
            src = device_reference(src)
        if src and any(
            ("device" in a["endpoint"] and a["endpoint"]["device"] not in
             {d["source_device"] for d in use["devices"]})
            or ("port" in a["endpoint"] and a["endpoint"]["port"] not in use["port_map"])
            for a in src["anchors"]
        ):
            # A removed branch cannot be reused as if it were still connected.
            src = {**src, "anchors": [], "gaps": ["adapted_source_endpoint_removed"]}
        reasons = list(src["gaps"]) if src else [source_error or "source_net_unavailable"]
        wanted = {canonical(w["endpoint"]): w for w in members}
        contact_gaps = mapping_gaps(src["anchors"], use, members) if src else []
        reasons.extend(contact_gaps)
        rigid = (
            None
            if reasons
            else _rigid_transform(
                src["anchors"], wanted, source, use, plan, geometry, layout, record
            )
        )
        if not reasons and rigid is None:
            reasons.append("source_target_geometry_not_rigid_compatible")
        paths, status, method = [], "reused", "rigid"
        if rigid is not None:
            paths = wire_paths(transformed_edges(src, source, rigid), members, net)
            reasons.extend(path_gaps(paths, members, layout, placed, pins))
        # Repairs require complete source attachment evidence and the same PDK
        # masters. A corrupt source or an unproved endpoint never becomes a hint.
        deform_diagnostics = []
        if (reasons and src and not src["gaps"] and not contact_gaps
                and _same_masters(source, use, plan, record)):
            coverage = {canonical(_target_endpoint(a["endpoint"], use)) for a in src["anchors"]}
            if coverage == set(wanted):
                repaired = repair_branches(src, source, use, members, layout, placed, pins,
                                           [w for ww in planned.values() for w in ww])
                if repaired:
                    paths, rigid = repaired
                    reasons, status, method = [], "rerouted", "terminal_branch_repair"
                else:
                    candidates = []
                    deformed = deform_paths(src, source, use, members, geometry, layout,
                                            placed, pins,
                                            [w for ww in planned.values() for w in ww],
                                            deform_diagnostics, pending=candidates)
                    if candidates and not deformed:
                        if len(pending) < MAX_PENDING:
                            pending[net] = candidates[0]
                        else:
                            deform_diagnostics.append(dict(code="coordinated_pending_budget_exhausted"))
                    if deformed:
                        paths, reasons, status = deformed, [], "rerouted"
                        method = ("orthogonal_deformation_conflict_component" if any(
                            d["code"] == "target_conflict_component_applied"
                            for d in deform_diagnostics
                        ) else "orthogonal_deformation_branch_chain" if any(
                            d["code"] == "target_branch_chain_applied" for d in deform_diagnostics
                        ) else "orthogonal_deformation_free_track" if any(
                            d["code"] == "target_free_track_applied" for d in deform_diagnostics
                        ) else "orthogonal_deformation_detour" if any(
                            d["code"] == "target_local_detour_applied" for d in deform_diagnostics
                        ) else "local_order_deformation" if any(
                            d["code"] == "target_local_order_graph_verified"
                            for d in deform_diagnostics) else "orthogonal_deformation")
        if reasons:
            planned[net] = members
            status = "stub" if routing.get("fallback", "reject") == "stub" else "rejected"
            row = {
                    "net": net,
                    "status": status,
                    "terminals": len(members),
                    "reasons": sorted(set(reasons)),
                }
            if deform_diagnostics:
                row["diagnostics"] = deform_diagnostics[:32]
            report.append(row)
            if status == "rejected":
                issues.append(
                    {"code": "template_routing_failed", "net": net, "reasons": sorted(set(reasons))}
                )
        else:
            planned[net] = paths
            row = {
                    "net": net,
                    "status": status,
                    "reasons": [],
                    "method": method,
                    **({"transform": {"orientation": rigid[0], "offset": rigid[1]}}
                       if method in {"rigid", "terminal_branch_repair"} else {}),
                    "terminals": len(members),
                    "segments": len(paths),
                }
            if deform_diagnostics:
                row["diagnostics"] = deform_diagnostics[:32]
            report.append(row)
    # A rigid/repaired graph can pass its own geometry but fail only once all
    # nets exist. Preserve that proved complete graph before final fallback;
    # never reconstruct a pending candidate from the resulting member stubs.
    verified = {r["net"]: planned[r["net"]] for r in report
                if r["status"] in {"reused", "rerouted"}}
    finalize_routes(planned, report, by_net, port_stubs, layout, placed, pins)
    for row in report:
        net = row["net"]
        if net in verified and net not in pending and row["status"] not in {"reused", "rerouted"}:
            if len(pending) < MAX_PENDING:
                pending[net] = verified[net]
            else:
                row["diagnostics"] = (row.get("diagnostics", []) + [dict(
                    code="coordinated_pending_budget_exhausted")])[:32]
    coordinate_routes(planned, report, by_net, port_stubs, pending, layout, placed, pins)
    issues = [{"code": "template_routing_failed", "net": r["net"], "reasons": r["reasons"]}
              for r in report if r["status"] == "rejected"]
    result = [w for rows in planned.values() for w in rows]
    result += [w for rows in port_stubs.values() for w in rows]
    fallbacks = [r for r in report if r["status"] not in {"reused", "rerouted"}
                 and r["reasons"] != ["port_stub_policy"]]
    routed = sum(r["terminals"] for r in report if r["status"] in {"reused", "rerouted"})
    terminal_fallbacks = [w for r in fallbacks for w in by_net[r["net"]]
                          if not stub_port(w, layout)]
    metrics = routing_metrics(routed, terminal_fallbacks, result)
    metrics["bends"] = path_bends(result, layout["grid"])
    return result, {
        "mode": "end_to_end", "engine": "template", "version": VERSION,
        "template_ref": use["template_ref"], "record_digest": digest(record),
        "reference_digest": source["reference_digest"] if source else digest(None),
        "fallback": routing.get("fallback", "reject"), "nets": report,
        "port_style": routing.get("ports", "stub"),
        "port_stub_terminals": sum(len(rows) for rows in port_stubs.values()),
        "reused_nets": sum(r["status"] == "reused" for r in report),
        "rerouted_nets": sum(r["status"] == "rerouted" for r in report),
        "fallbacks": fallbacks, "issues": issues,
        "metrics": metrics,
    }


def _same_masters(source, use, plan, record):
    masters = {m["id"]: m for m in plan["bindings"]["masters"]}
    devices = {d["id"]: d for d in record["topology"]["devices"]}
    return all(d["source_device"] in source["geometry"]
               and masters[d["master"]]["target"] == devices[d["source_device"]]["master"]
               and os.path.realpath(masters[d["master"]]["library_path"]) == os.path.realpath(
                   source["geometry"][d["source_device"]]["library_path"])
               for d in use["devices"])
