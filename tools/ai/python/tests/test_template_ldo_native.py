"""Live LDO template creation with physical sizing and installed CDF callbacks."""

import copy
import json
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
from template_visual_support import export_schematic
from test_template_pdk_native import PDK

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_schema import digest
from cadai.template_circuit import prepare_template
from cadai.template_placement import preview_placement

pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed P1 LDO acceptance"
)
SOURCE = "/zone.disk/ssd1/workarea/xh/amsVerify/DESIGNS/LDO_IP/amsLDO"
POLICIES = {
    "pmos2v": (["l", "fingers", "w", "m"], dict(B="right", D="down", G="left", S="up")),
    "nmos2v": (["l", "fingers", "w", "m"], dict(D="up", G="left", B="right", S="down")),
    "nmoscap2v": (
        ["calculatedParam", "l", "fingers", "w", "m"],
        dict(S="down", G="left", D="down", B="right"),
    ),
    "resnsnpoly": (
        ["calculatedParam", "segW", "segL", "segments"],
        dict(PLUS="up", MINUS="down", B="right"),
    ),
}


def sizing():
    """Explicit acceptance choices; never copy source electrical/derived values."""
    rows = {name: dict(l="500n", fingers="1", w="5u", m="1")
            for name in ("M2", "M3", "M5", "M6")}
    rows.update({name: dict(l="500n", fingers="2", w="20u", m="1")
                 for name in ("M0", "M1")})
    rows.update(
        M7=dict(l="500n", fingers="2", w="10u", m="100"),
        M8=dict(l="500n", fingers="1", w="5u", m="10"),
        M4=dict(calculatedParam="Capacitance", l="10u", fingers="100", w="32u", m="100"),
    )
    rows.update({name: dict(calculatedParam="Resistance", segW="2u", segL="10u", segments=2)
                 for name in ("R0", "R1", "R2")})
    return rows


def derived_readback(bridge):
    rows = json.loads(bridge.skill(
        'let((ldrCv ldrRows) ldrCv=dbOpenCellViewByType("genericWork" "LDO_target" '
        '"schematic" "" "r") ldrRows=aiCircuitJson(foreach(mapcar ldrInst '
        'setof(ldrI ldrCv~>instances ldrI~>libName!="basic") '
        'list(ldrInst~>name foreach(mapcar ldrP cdfGetInstCDF(ldrInst)~>parameters '
        'list(ldrP~>name ldrP~>value))))) dbClose(ldrCv) ldrRows)'
    ))
    return {name: dict(values) for name, values in rows}


def test_ldo_physical_inputs_create_reopen_replay_and_source_unchanged(tmp_path):
    source = Path(os.environ.get("SICO_AI_LDO_TEMPLATE_LIBRARY", SOURCE))
    source_oa = source / "LDO_MASTER/schematic/sch.oa"
    if not source_oa.is_file() or not Path(PDK).is_dir():
        pytest.skip("installed LDO reference and test PDK required")
    before = oa_hash(source_oa)
    with pdk_session(tmp_path, Path(PDK), source_lib=source) as (bridge, server):
        bound = {cell: bind_device(server, cell, names, escapes)
                 for cell, (names, escapes) in POLICIES.items()}
        save_json(tmp_path / "bindings.json", bound)
        init = json.loads(bridge.skill('aiPdkInitializeLibrary("gpdk045")'))
        assert init["ok"] and init["initialized"] and init["cached"]
        first = bound["pmos2v"]
        bindings = dict(schema="cad.circuit.bindings.v1", project_ref=first["project_ref"],
                        snapshot_ref=first["context"]["snapshot_ref"],
                        source_ref=first["context"]["session_ref"], evidence_kind="session_capture",
                        masters=[copy.deepcopy(row["master"]) for row in bound.values()])
        record = capture(server, tmp_path, "LDO_MASTER", library="amsLDO")
        parameters = sizing()
        devices = [dict(source_device=d["id"], instance=d["source_name"], name=d["source_name"],
                        master=d["master"]["cell"], parameters=parameters[d["source_name"]])
                   for d in record["topology"]["devices"]]
        prepared = prepare_template(dict(
            template_ref=record["template_ref"], bindings=bindings, devices=devices,
            target=dict(library="genericWork", cell="LDO_target", view="schematic"),
        ), workspace=tmp_path)
        geometry = [dict(copy.deepcopy(bound[d["master"]]["geometry"]), instance=d["instance"],
                         parameters_digest=digest(d["parameters"])) for d in devices]
        args = dict(spec=prepared["spec"], bindings=bindings,
                    template_use=prepared["template_use"],
                    preview_digest=prepared["preview_digest"],
                    geometry=dict(schema="cad.circuit.geometry.v1",
                                  project_ref=bindings["project_ref"],
                                  binding_snapshot=bindings["snapshot_ref"],
                                  binding_digest=digest(bindings),
                                  source_ref=bindings["source_ref"],
                                  evidence_kind="session_capture",
                                  coordinate_system="master_local_schematic_uu",
                                  instances=geometry))
        placement = preview_placement(dict(
            template_ref=record["template_ref"],
            **{k: args[k] for k in ("spec", "bindings", "geometry", "template_use")},
            options=dict(clearance=0, stub_length=0.5,
                         routing=dict(mode="end_to_end", engine="template", fallback="stub")),
        ), workspace=tmp_path)
        args["layout"] = placement["layout"]
        save_json(tmp_path / "placement.json", placement)
        save_json(tmp_path / "creation-args.json", args)
        left = args["layout"]["instances"]
        assert left["R0"]["xy"][0] == left["M2"]["xy"][0] - 0.25
        assert left["R2"]["xy"][0] == left["R1"]["xy"][0]
        assert left["R0"]["xy"][1] == left["R2"]["xy"][1]
        assert left["R0"]["xy"][0] < left["R2"]["xy"][0] < left["M6"]["xy"][0]
        assert left["M0"]["xy"][0] - 0.25 == left["M6"]["xy"][0] + 0.25
        assert left["M1"]["xy"][0] + 0.25 == left["M8"]["xy"][0] - 0.25
        assert left["M5"]["xy"][0] == left["M7"]["xy"][0]
        assert left["R0"]["xy"][1] > left["R1"]["xy"][1] > left["M2"]["xy"][1]
        assert not any(g["code"] == "terminal_axis_unavailable"
                       for g in placement["report"]["gaps"])
        assert all(p["placement_source"] == "template" for p in args["layout"]["ports"].values())
        preview = preview_geometry(**args, workspace=tmp_path)
        save_json(tmp_path / "target-preview.json", preview)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        assert (preview["plan"]["routing"]["reused_nets"]
                + preview["plan"]["routing"]["rerouted_nets"]) >= 1
        args["geometry_plan_digest"] = preview["geometry_plan_digest"]
        done = create(server, args, "ldo-target")
        svg = done["created"]["svg_preview"]
        assert svg["status"] == "ready" and svg["artifact"], svg
        assert Path(svg["artifact"]["path"]).read_text().startswith("<svg")
        assert svg["acceptance_blocking"] is False
        text_layout = done["created"]["text_layout"]
        assert text_layout["dynamic_text_verified"] is False
        assert text_layout["visual_review_required"] is True
        assert text_layout["dynamic_text_policy"] == "cadence_expressions_not_blocking"
        assert not text_layout["unresolved"]
        assert not any(row[0] == "wire_label_conflict" for row in text_layout["unresolved"])
        physical = readback(bridge, prepared["spec"], preview["plan"]["wires"])
        derived = derived_readback(bridge)
        assert derived["M4"]["c"] == "1.9392p"
        for name in ("R0", "R1", "R2"):
            assert {key: derived[name][key] for key in ("r", "segR", "effW", "effL")} == dict(
                r="2K", segR="1K", effW="2u", effL="20u"
            )
        screenshots = [
            export_schematic(bridge, tmp_path,
                             dict(library="amsLDO", cell="LDO_MASTER", view="schematic"),
                             "ldo-source"),
            export_schematic(bridge, tmp_path, prepared["spec"]["target"], "ldo-target"),
        ]
        after = oa_hash(source_oa)
        assert after == before
        save_json(tmp_path / "acceptance.json", dict(
            source_sha256_before=before, source_sha256_after=after, initialization=init,
            bindings=bound, prepared=prepared, placement=placement, preview=preview, **done,
            physical_readback=physical, derived_parameters=derived, screenshots=screenshots,
        ))
