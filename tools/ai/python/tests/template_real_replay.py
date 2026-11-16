"""Replay fixed real-design labels through production exact/core/adaptation checks."""

import copy
from collections import Counter

from template_real_labels import MASTERS, expected_topology

from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError, digest
from cadai.template_adaptation import adapt, verify_use
from cadai.template_core_match import match_core
from cadai.template_index import build_index, necessary_candidates
from cadai.template_placement_layout import plan_layout
from cadai.template_relations import relations
from cadai.template_rules import rule_ref
from cadai.template_search import search_exact
from cadai.template_topology import evidence_gaps


def expected_mapping(case, record, rename=False):
    return dict(
        device_map={d["id"]: "U_" + d["source_name"] for d in record["topology"]["devices"]},
        terminal_map={d["id"]: {p["name"]: p["name"] for p in d["pins"]}
                      for d in record["topology"]["devices"]},
        net_map={n["id"]: ("N_" if rename else "") + n["source_name"]
                 for n in record["topology"]["nets"]},
        port_map={p.split()[0]: ("N_" if rename else "") + p.split()[0]
                  for p in case["ports"]}, omitted_groups=[])


def labeled_target(case, rename=False, *, masters=MASTERS):
    target = expected_topology(case, masters=masters)
    for d in target["devices"]:
        d["id"] = "U_" + d["id"]
        for p in d["pins"]:
            p["net"] = ("N_" if rename else "") + p["net"]
    for n in target["nets"]:
        n["id"] = ("N_" if rename else "") + n["id"]
        n["source_name"] = n["id"]
    for p in target["ports"]:
        p["name"] = ("N_" if rename else "") + p["name"]
        p["net"] = ("N_" if rename else "") + p["net"]
    return target


def replay(case, record, records, directory, *, target_factory=labeled_target):
    pool = [dict(template_ref=r["template_ref"], topology=r["topology"], origins=["project"])
            for r in records]
    index = build_index(dict(snapshot_ref="review_" + digest(records)), records,
                        directory / "index.json")
    report = []
    for variant in ("identity", "rename", "wrong_connection", "wrong_kind", "swap_ds",
                    "wrong_port_direction", "unknown"):
        target = target_factory(case, variant == "rename")
        if variant == "wrong_connection":
            name, pin, net = case["changed_pin"]
            d = next(d for d in target["devices"] if d["id"] == "U_" + name)
            next(p for p in d["pins"] if p["name"] == pin)["net"] = net
        elif variant == "wrong_kind":
            target["devices"][0]["kind"] = "resistor"
        elif variant == "swap_ds":
            d = next(d for d in target["devices"] if d["kind"] == "pmos")
            pins = {p["name"]: p for p in d["pins"]}
            pins["D"]["net"], pins["S"]["net"] = pins["S"]["net"], pins["D"]["net"]
        elif variant == "wrong_port_direction":
            next(p for p in target["ports"] if p["direction"] == "output")["direction"] = "input"
        elif variant == "unknown":
            target["gaps"] = [dict(code="terminal_inventory_unverified")]
        mapping = expected_mapping(case, record, variant == "rename")
        pm = {r["template_ref"]: mapping["port_map"] if r == record else {} for r in records}
        options = dict(max_query_time_ms=10000, max_match_states=200000)
        full = search_exact(target, pool, options, port_maps=pm)
        expected = ("needs_mapping" if variant in {"identity", "rename"} else
                    "inconclusive" if variant == "unknown" else "no_match")
        assert full["status"] == expected, (case["id"], variant, full)
        if expected == "needs_mapping":
            assert full["best_match"]["template_ref"] == record["template_ref"]
            assert full["best_match"]["mapping"]["devices"] == mapping["device_map"]
        kinds = Counter(d["kind"] for d in target["devices"])
        selected = set(necessary_candidates(index, device_kinds=kinds,
                       min_devices=len(target["devices"]), max_devices=len(target["devices"])))
        if evidence_gaps(target):
            selected = {r["template_ref"] for r in records}
        individual = {row["template_ref"]: search_exact(target, [row], options,
                      port_maps={row["template_ref"]: pm[row["template_ref"]]})["status"]
                      for row in pool}
        assert {ref for ref, status in individual.items() if status != "no_match"} <= selected
        indexed = (search_exact(target, [r for r in pool if r["template_ref"] in selected],
                                options, port_maps={ref: pm[ref] for ref in selected})
                   if selected else None)
        if indexed:
            assert indexed["status"] == full["status"]
        else:
            assert full["status"] == "no_match"
        core = match_core(record, target, port_map=mapping["port_map"], timeout=10)
        expected_core = ("matched" if variant == "identity" else
                         "needs_adaptation" if variant == "rename" else
                         "different" if expected == "no_match" else expected)
        assert core["status"] == expected_core, (variant, core)
        if core["status"] in {"matched", "needs_adaptation"}:
            assert mapping in core["mappings"]
        report.append(dict(variant=variant, expected=expected, full=full, indexed=indexed,
                           individual=individual, selected=sorted(selected), core=core))
    return report


def target_inputs(case, bound, *, target_factory=labeled_target, parameters=None):
    first = next(iter(bound.values()))
    cells = sorted({r.split()[1] for r in case["devices"]})
    bindings = dict(schema="cad.circuit.bindings.v1", project_ref=first["project_ref"],
                    snapshot_ref=first["context"]["snapshot_ref"],
                    source_ref=first["context"]["session_ref"], evidence_kind="session_capture",
                    masters=[copy.deepcopy(bound[cell]["master"]) for cell in cells])
    target = target_factory(case, rename=True)
    cells_by_name = {r.split()[0]: r.split()[1] for r in case["devices"]}
    instances, geometry = [], []
    for d in target["devices"]:
        cell = cells_by_name[d["source_name"]]
        params = (copy.deepcopy(parameters[d["kind"]]) if parameters is not None else
                  dict(l="100n", w="2u" if d["kind"] == "pmos" else "1u", fingers="1", m="1"))
        instances.append(dict(id=d["id"], name=d["id"], master=cell, role="device",
                              parameters=params, connections={p["name"]: p["net"]
                                                              for p in d["pins"]}, unconnected={}))
        geometry.append(dict(copy.deepcopy(bound[cell]["geometry"]), instance=d["id"],
                             parameters_digest=digest(params)))
    spec = dict(schema="cad.circuit.spec.v1", kind="circuit", project_ref=bindings["project_ref"],
                binding_snapshot=bindings["snapshot_ref"],
                target=dict(library="genericWork", cell=case["id"] + "_review", view="schematic"),
                instances=instances,
                nets=[dict(name=n["id"], scope="local") for n in target["nets"]],
                ports=[dict(name=p["name"], net=p["net"], direction=p["direction"])
                       for p in target["ports"]])
    geo = dict(schema="cad.circuit.geometry.v1", project_ref=bindings["project_ref"],
               binding_snapshot=bindings["snapshot_ref"], binding_digest=digest(bindings),
               source_ref=bindings["source_ref"], evidence_kind="session_capture",
               coordinate_system="master_local_schematic_uu", instances=geometry)
    spec = preview_circuit(spec, bindings)["plan"]["spec"]
    return spec, bindings, geo


def target_request(case, record, bound, **input_options):
    spec, bindings, geo = target_inputs(case, bound, **input_options)
    mapping = expected_mapping(case, record, True)
    rules = [rule_ref("rename")]
    use = adapt(record, spec, bindings, mapping, rules)
    assert verify_use(record, spec, use, bindings) == use
    assert [a["rule_ref"]["id"] for a in use["adaptations"]] == ["rename"]
    try:
        adapt(record, spec, bindings, mapping, [])
    except CircuitSpecError:
        pass
    else:
        raise AssertionError("rename must require explicit rule authorization")
    layout, report = plan_layout(spec, bindings, geo, relations(record), mapping["device_map"],
                                dict(spacing="loose", clearance=0.125, stub_length=0.75,
                                     routing=dict(mode="stub")), port_map=mapping["port_map"])
    return dict(spec=spec, bindings=bindings, geometry=geo, layout=layout,
                template_ref=record["template_ref"], mapping=mapping, allowed_rule_refs=rules,
                options=dict(match_mode="core", allowed_tiers=["project"],
                             return_alternatives=0)), report
