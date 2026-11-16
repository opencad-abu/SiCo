"""Independent live gpdk045 differential pair and current mirror acceptance."""

import copy
import os
from pathlib import Path

import pytest
from template_pdk_support import (
    bind_device,
    capture,
    create,
    oa_hash,
    pdk_session,
    readback,
    save_json,
)

from cadai.circuit_create import skill_literal
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest
from cadai.template_circuit import prepare_template
from cadai.template_wire_attachment import compile_reference

PDK = "/zone.disk/hdd3/cold-archive/PDK.Collection/CADENCE/45n/gpdk045_v_6_0/gpdk045"
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed P1 gpdk045 acceptance"
)


def fixture(binding, design):
    rows = [
        ("M0", dict(G="INP", D="OUTP", S="TAIL", B="VSS"), [0, 0]),
        ("M1", dict(G="INM", D="OUTM", S="TAIL", B="VSS"), [4, 0]),
        ("M2", dict(G="BIAS", D="TAIL", S="VSS", B="VSS"), [2, -3]),
    ]
    ports = [
        ("VSS", "inputOutput", [-3, 5]),
        ("INP", "input", [-3, 3]),
        ("INM", "input", [-3, 2]),
        ("BIAS", "input", [-3, 1]),
        ("OUTP", "output", [8, 3]),
        ("OUTM", "output", [8, 2]),
    ]
    if design == "mirror":
        rows = [
            ("M0", dict(G="REF", D="REF", S="VSS", B="VSS"), [0, 0]),
            ("M1", dict(G="REF", D="OUT", S="VSS", B="VSS"), [4, 0]),
        ]
        ports = [
            ("VSS", "inputOutput", [-3, 3]),
            ("REF", "input", [-3, 1]),
            ("OUT", "output", [8, 2]),
        ]
    params = dict(l="150n", w="640n", m="1")
    spec = dict(
        schema="cad.circuit.spec.v1",
        kind="circuit",
        target=dict(library="genericWork", cell=design + "_source", view="schematic"),
        instances=[
            dict(
                id=n,
                name=n,
                master="nmos2v",
                role="device",
                parameters=params,
                connections=c,
                unconnected={},
            )
            for n, c, _ in rows
        ],
        nets=[
            dict(name=n, scope="local")
            for n in sorted({net for _, conns, _ in rows for net in conns.values()})
        ],
        ports=[dict(name=n, direction=d, net=n) for n, d, _ in ports],
    )
    layout = dict(
        instances={n: dict(xy=xy, orientation="R0") for n, _, xy in rows},
        ports={n: dict(xy=xy, escape="left" if d == "output" else "right") for n, d, xy in ports},
        anchor_choices={},
        grid=0.0625,
        stub_length=0.5,
        clearance=0,
        routing=dict(mode="stub", decision=dict(source="user", chosen="stub")),
    )
    return dict(binding_refs=[binding["binding_ref"]], spec=spec, layout=layout)


def geometry_args(service, request):
    args = service.resolve_request(request)
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]
    preview = preview_geometry(**args)
    assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
    args["geometry_plan_digest"] = preview["geometry_plan_digest"]
    return args


def clean_shared_net(bridge, design):
    net = "TAIL" if design == "diff" else "REF"
    paths = (
        [
            [[0.25, -0.1875], [0.25, -1], [4.25, -1], [4.25, -0.1875]],
            [[2.25, -2.8125], [2.25, -1]],
        ]
        if design == "diff"
        else [
            [[0, 0], [-0.5, 0], [-0.5, 1], [3.5, 1], [3.5, 0], [4, 0]],
            [[0.25, 0.1875], [0.25, 1]],
            [[-3, 1], [-0.5, 1]],
        ]
    )
    code = (
        'let((psCv psWire) psCv=dbOpenCellViewByType("genericWork" '
        + skill_literal(design + "_source")
        + ' "schematic" "" "a") '
        'foreach(psShape psCv~>shapes when(and(car(psShape~>lpp)=="wire" '
        "psShape~>net~>name==" + skill_literal(net) + ") dbDeleteObject(psShape))) "
        "foreach(psPoints "
        + skill_literal(paths)
        + ' psWire=schCreateWire(psCv "draw" "full" psPoints 0.0625 0.0625 0.0)) '
        "schCreateWireLabel(psCv car(psWire) car(car(last("
        + skill_literal(paths)
        + "))) "
        + skill_literal(net)
        + ' "lowerLeft" "R0" "stick" 0.0625 nil) '
        "schCheck(psCv) dbSave(psCv) dbClose(psCv) t)"
    )
    assert bridge.skill(code) == "t"
    return net


def target_args(source, record, workspace, cell):
    # Target parameters are an explicit new design choice; no source CDF values are copied.
    params = dict(l="200n", w="960n", m="1")
    prepared = prepare_template(
        dict(
            template_ref=record["template_ref"],
            bindings=source["bindings"],
            target=dict(library="genericWork", cell=cell, view="schematic"),
            devices=[
                dict(
                    source_device=d["id"],
                    instance=d["source_name"],
                    name=d["source_name"],
                    master="nmos2v",
                    parameters=params,
                )
                for d in record["topology"]["devices"]
            ],
        ),
        workspace=workspace,
    )
    args = {k: copy.deepcopy(v) for k, v in source.items() if k != "geometry_plan_digest"}
    args.update(
        spec=prepared["spec"],
        template_use=prepared["template_use"],
        preview_digest=prepared["preview_digest"],
    )
    for geo in args["geometry"]["instances"]:
        geo["parameters_digest"] = digest(params)
    for item in [*args["layout"]["instances"].values(), *args["layout"]["ports"].values()]:
        item["xy"] = [item["xy"][0] + 10, item["xy"][1] + 5]
    args["layout"]["routing"] = dict(mode="end_to_end", engine="template", fallback="stub")
    return args


def verify_target(bridge, server, workspace, args, key, shared):
    preview = preview_geometry(**args, workspace=workspace)
    save_json(workspace / (key + "-preview.json"), preview)
    assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
    row = next(r for r in preview["plan"]["routing"]["nets"] if r["net"] == shared)
    assert row["status"] == ("rerouted" if key.endswith("reroute") else "reused"), row
    assert row["terminals"] >= 3
    args["geometry_plan_digest"] = preview["geometry_plan_digest"]
    result = create(server, args, key)
    result["physical_readback"] = readback(bridge, args["spec"], preview["plan"]["wires"])
    assert {r["net"]: r["status"] for r in result["created"]["template_routing"]["nets"]} == {
        r["net"]: r["status"] for r in preview["plan"]["routing"]["nets"]
    }
    return result


@pytest.mark.parametrize("design", ["diff", "mirror"])
def test_gpdk045_reference_template_native(tmp_path, design):
    pdk = Path(os.environ.get("SICO_AI_TEMPLATE_PDK_ROOT", PDK))
    if not pdk.is_dir():
        pytest.skip("gpdk045 unavailable")
    with pdk_session(tmp_path, pdk) as (bridge, server):
        binding = bind_device(
            server, "nmos2v", ["l", "w", "m"], dict(D="up", S="down", G="left", B="right")
        )
        save_json(tmp_path / "binding.json", binding)
        source = geometry_args(server._pdk_bindings, fixture(binding, design))
        create(server, source, design + "_source")
        shared = clean_shared_net(bridge, design)
        source_oa = tmp_path / "library" / (design + "_source") / "schematic" / "sch.oa"
        before = oa_hash(source_oa)
        record = capture(server, tmp_path, design + "_source")
        attachment = compile_reference(record)
        save_json(tmp_path / "attachment.json", attachment)
        args = target_args(source, record, tmp_path, design + "_target")
        result = verify_target(bridge, server, tmp_path, args, design + "_target", shared)
        reroute = target_args(source, record, tmp_path, design + "_reroute")
        if design == "diff":
            reroute["layout"]["instances"]["M2"]["xy"][1] -= 0.5
        else:
            reroute["layout"]["instances"]["M1"]["xy"][0] += 0.5
        moved = verify_target(bridge, server, tmp_path, reroute, design + "_reroute", shared)
        assert oa_hash(source_oa) == before
        save_json(
            tmp_path / "acceptance.json",
            dict(
                source_sha256_before=before,
                source_sha256_after=oa_hash(source_oa),
                device_count=len(source["spec"]["instances"]),
                template_ref=record["template_ref"],
                target=result,
                reroute=moved,
            ),
        )
