"""Replay a real series-RC omission against the existing matching/adaptation entry points."""

import copy

from template_optional_labels import OPAMP, expected_devices, expected_nets, expected_ports
from template_real_replay import expected_mapping, target_inputs

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_adaptation import adapt, verify_use
from cadai.template_core_match import match_core
from cadai.template_placement_layout import plan_layout
from cadai.template_relations import relations
from cadai.template_rules import rule_ref
from cadai.template_search import search_exact


def labeled_topology():
    return dict(
        level="direct",
        gaps=[],
        net_global_semantics="uniform_boolean_v1",
        devices=[
            dict(
                id="U_" + name,
                source_name=name,
                role="device",
                kind=r["kind"],
                attributes={"device_class": r["cell"]} if r["library"] == "analogLib" else {},
                pins=[dict(name=p, net=n) for p, n in r["pins"].items()],
            )
            for name, r in expected_devices(OPAMP).items()
        ],
        nets=[
            dict(id=n, source_name=n, num_bits=1, sig_type=t, is_global=g)
            for n, (t, g) in expected_nets(OPAMP).items()
        ],
        ports=[
            dict(id="p" + str(i), name=n, direction=d, net=net, num_bits=1)
            for i, (n, (d, net)) in enumerate(expected_ports(OPAMP).items())
        ],
    )


def omission_mapping(record, rename=False):
    mapping = expected_mapping(OPAMP, record, rename)
    group = OPAMP["optional"]
    for d in record["topology"]["devices"]:
        if d["source_name"] in group["devices"]:
            del mapping["device_map"][d["id"]]
            del mapping["terminal_map"][d["id"]]
    for n in record["topology"]["nets"]:
        if n["source_name"] in group["remove_nets"]:
            del mapping["net_map"][n["id"]]
    mapping["omitted_groups"] = [group["id"]]
    return mapping


def replay(record):
    results = []
    for variant in (
        "retained",
        "whole_omission",
        "undeclared_omission",
        "partial_omission",
        "core_wrong_connection",
        "dangling_internal_net",
        "unknown",
    ):
        top = labeled_topology()
        omitted = (
            [] if variant in {"retained", "undeclared_omission"} else [OPAMP["optional"]["id"]]
        )
        if variant != "retained":
            removed = ["U_R2"] if variant == "partial_omission" else ["U_R2", "U_C0"]
            top["devices"] = [d for d in top["devices"] if d["id"] not in removed]
            if variant not in {"partial_omission", "dangling_internal_net"}:
                top["nets"] = [n for n in top["nets"] if n["id"] != "R2_MINUS"]
        if variant == "core_wrong_connection":
            dev = next(d for d in top["devices"] if d["source_name"] == "NM0")
            next(p for p in dev["pins"] if p["name"] == "B")["net"] = "VSSA"
        if variant == "unknown":
            top["gaps"] = [dict(code="terminal_inventory_unverified")]
        ports = {n: n for n in expected_ports(OPAMP)}
        exact = search_exact(
            top,
            [
                dict(
                    template_ref=record["template_ref"],
                    topology=record["topology"],
                    origins=["project"],
                )
            ],
            dict(max_query_time_ms=10000),
            port_maps={record["template_ref"]: ports},
        )
        assert exact["status"] == (
            "needs_mapping"
            if variant == "retained"
            else "inconclusive"
            if variant == "unknown"
            else "no_match"
        ), exact
        core = match_core(record, top, port_map=ports, omitted_groups=omitted, timeout=10)
        expected = {
            "retained": "matched",
            "whole_omission": "needs_adaptation",
            "unknown": "inconclusive",
        }.get(variant, "different")
        # A partial group may leave a legal boundary residual, but combining it
        # with omission is explicitly unsupported and must never yield a mapping.
        if variant == "partial_omission":
            assert core["status"] == "needs_adaptation", core
            assert core["reason"] == "multiple_topology_adaptations", core
            assert not core["mappings"], core
        else:
            assert core["status"] == expected, (variant, core)
        if variant == "whole_omission":
            assert core["mappings"] == [omission_mapping(record)], core
        results.append(dict(variant=variant, exact=exact, core=core))
    return results


def omission_request(record, bound):
    spec, bindings, geometry = target_inputs(OPAMP, bound)
    spec["target"]["cell"] = "opamp_without_compensation_review"
    mapping = omission_mapping(record, True)
    rules = [rule_ref(n) for n in ("rename", "omit_optional_group")]
    use = adapt(record, spec, bindings, mapping, rules)
    assert verify_use(record, spec, use, bindings) == use
    assert [a["rule_ref"]["id"] for a in use["adaptations"]] == ["rename", "omit_optional_group"]
    diff = use["adaptations"][-1]["diff"]
    group = record["reuse_contract"]["optional_groups"][0]
    assert diff["removed_devices"] == sorted(group["devices"])
    assert diff["removed_nets"] == group["omit"]["remove_nets"]
    assert diff["removed_ports"] == [] and use["residual_instances"] == []
    rejected = []
    for variant in ("missing_rule", "undeclared_group", "core_wrong_connection", "forged_receipt"):
        altered, selected = copy.deepcopy(spec), copy.deepcopy(mapping)
        if variant == "undeclared_group":
            selected["omitted_groups"] = []
        elif variant == "core_wrong_connection":
            next(i for i in altered["instances"] if i["name"] == "U_NM0")["connections"]["B"] = (
                "N_VSSA"
            )
        try:
            if variant == "forged_receipt":
                forged = copy.deepcopy(use)
                forged["adaptations"][-1]["diff"]["removed_devices"] = []
                verify_use(record, spec, forged, bindings)
            else:
                adapt(
                    record,
                    altered,
                    bindings,
                    selected,
                    rules[:1] if variant == "missing_rule" else rules,
                )
        except CircuitSpecError as exc:
            rejected.append(dict(variant=variant, reason=str(exc)))
        else:
            raise AssertionError("unsafe omission accepted: " + variant)
    layout, placement = plan_layout(
        spec,
        bindings,
        geometry,
        relations(record),
        mapping["device_map"],
        dict(spacing="loose", clearance=0.125, stub_length=0.75, routing=dict(mode="stub")),
        port_map=mapping["port_map"],
    )
    return dict(
        spec=spec,
        bindings=bindings,
        geometry=geometry,
        layout=layout,
        template_ref=record["template_ref"],
        mapping=mapping,
        allowed_rule_refs=rules,
        options=dict(match_mode="core", allowed_tiers=["project"], return_alternatives=0),
    ), dict(placement=placement, rejected=rejected, use=use)
