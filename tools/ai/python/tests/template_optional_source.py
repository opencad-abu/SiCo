"""Observe real source evidence and construct its reviewed structural omission contract."""

import json

from template_optional_labels import expected_devices, expected_nets, expected_ports
from template_real_review_support import (
    read_inventory,
    sha256,
    verified_classifications,
    write_json,
)
from template_visual_support import export_schematic

from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_reuse_build import make_reusable
from cadai.template_rules import rule_ref


def check_inventory(case, inventory):
    devices, ports, nets = inventory
    expected = expected_devices(case)
    actual = {r[0]: (r[1], r[2], r[3], dict(r[4])) for r in devices if r[1] != "basic"}
    assert actual == {
        name: (r["library"], r["cell"], "symbol", r["pins"]) for name, r in expected.items()
    }
    assert {name: (direction, net) for name, direction, net in ports} == expected_ports(case)
    assert {name: (typ, bool(global_)) for name, typ, global_ in nets} == expected_nets(case)


def check_record(case, record):
    top = record["topology"]
    names = {n["id"]: n["source_name"] for n in top["nets"]}
    assert {
        d["source_name"]: dict(
            library=d["master"]["library"],
            cell=d["master"]["cell"],
            kind=d["kind"],
            pins={p["name"]: names[p["net"]] for p in d["pins"]},
        )
        for d in top["devices"]
    } == expected_devices(case)
    assert {p["name"]: (p["direction"], names[p["net"]]) for p in top["ports"]} == expected_ports(
        case
    )
    assert {
        n["source_name"]: (n["sig_type"], n["is_global"]) for n in top["nets"]
    } == expected_nets(case)


def optional_record(case, base):
    top = base["topology"]
    devices = {d["source_name"]: d for d in top["devices"]}
    nets = {n["source_name"]: n["id"] for n in top["nets"]}
    group = case["optional"]
    core = [d for name, d in devices.items() if name not in group["devices"]]

    def endpoint(name, pin):
        return dict(device=devices[name]["id"], terminal=pin)

    contract = dict(
        core_devices=[d["id"] for d in core],
        core_connections=[endpoint(d["source_name"], p["name"]) for d in core for p in d["pins"]],
        boundary_terminals=[
            dict(
                endpoint=endpoint(name, pin),
                allowed_device_kinds=[kind],
                min_additional_connections=0,
                max_additional_connections=1,
            )
            for name, pin, kind in group["boundaries"]
        ],
        optional_groups=[
            dict(
                id=group["id"],
                devices=[devices[n]["id"] for n in group["devices"]],
                boundary_connections=[endpoint(n, p) for n, p in group["group_pins"]],
                omit=dict(remove_nets=[nets[n] for n in group["remove_nets"]], remove_ports=[]),
            )
        ],
        allowed_rule_refs=[rule_ref(n) for n in ("rename", "omit_optional_group")],
        placement_constraints=[],
        routing_reference=dict(
            asset="schematic", status="unavailable", anchor_ids=[], segment_ids=[]
        ),
    )
    classes = {
        d["id"]: d.get("classification_evidence")
        or dict(
            kind=d["kind"],
            attributes=d["attributes"],
            source_ref="review:installed-CDF:" + d["kind"],
            revision=case["oa_sha256"],
        )
        for d in top["devices"]
    }
    return make_reusable(base, contract, classes)


def review_source(bridge, directory, case, source_path, model_path):
    directory.mkdir()
    oa = source_path / case["cell"] / "schematic/sch.oa"
    assert sha256(oa) == case["oa_sha256"]
    raw = directory / "capture.jsonl"
    bridge.skill(
        "aiTplCapture("
        + " ".join(json.dumps(case[k]) for k in ("library", "cell"))
        + ' "schematic" "symbol" "layout" '
        + json.dumps(str(raw))
        + ' list("schematic") "cad.template.capture.v2")'
    )
    inventory = read_inventory(bridge, case)
    check_inventory(case, inventory)
    classes, cdf = verified_classifications(bridge, case, model_path)
    for row in case["passives"].values():
        cell = json.dumps(row["cell"])
        observed = json.loads(
            bridge.skill(
                'let((ocsCdf) ocsCdf=cdfGetCellCDF(ddGetObj("analogLib" ' + cell + ")) "
                "aiCircuitJson(list(aiTplGet(aiTplGet(aiTplGet(ocsCdf 'simInfo) 'spectre) "
                "'componentName) "
                "aiTplGet(aiTplGet(aiTplGet(ocsCdf 'simInfo) 'spectre) 'termOrder))))"
            )
        )
        assert observed == [row["kind"], ["PLUS", "MINUS"]]
        cdf[row["cell"]] = dict(component=observed[0], terminals=observed[1])
    base = make_template(read_capture(raw), classifications=classes)
    check_record(case, base)
    visual = export_schematic(
        bridge,
        directory,
        dict(library=case["library"], cell=case["cell"], view="schematic"),
        "source",
    )
    assert sha256(oa) == case["oa_sha256"]
    for name, value in (
        ("oa-inventory", inventory),
        ("base-record", base),
        ("cdf-evidence", cdf),
        ("visual", visual),
    ):
        write_json(directory / (name + ".json"), value)
    return base
