"""Independent passive network: capture, generic placement expansion and OA edge readback."""

import copy
import json
import os

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import AI_ROOT, sample_master, session
from template_pdk_support import capture, create, oa_hash, readback, save_json
from template_visual_support import export_schematic

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest
from cadai.template_circuit import prepare_template
from cadai.template_placement import preview_placement


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed acceptance")
def test_passive_branch_network_uses_generic_track_deformation(tmp_path, monkeypatch):
    with session(tmp_path) as (bridge, server, _):
        server.client.runtime = server.client.dispatcher.runtime
        original = bridge.request

        def request(message, timeout):
            if message["method"] == "capture_circuit_template":
                bridge.evaluate_path(message["source_path"], message["output_path"],
                                     evaluator="aiEvalExpression")
                return True, {"result_path": message["output_path"]}
            return original(message, timeout)

        monkeypatch.setattr(bridge, "request", request)
        for name in ("AI_templates.il", "AI_template_capture_geometry.il"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        base = fixture({c: sample_master(bridge, c) for c in ("res", "cap")}, "bus_source")
        master = copy.deepcopy(base["bindings"]["masters"][0])
        geometry = copy.deepcopy(base["geometry"]["instances"][0])
        observed = json.loads(bridge.skill("aiCrBindingGeometry(" + " ".join(json.dumps(v) for v in
                              ["analogLib", "res", "symbol", master["library_path"]]) + ")"))
        geometry["occupied_bbox"] = observed["occupied_bbox"]
        geometry["annotation_bbox"] = observed["annotation_bbox"]
        spec = base["spec"]
        spec.update(ports=[], notes=[],
                    nets=[dict(name=n, scope="local") for n in ("TOP", "RETURN")],
                    instances=[dict(id=f"R{i}", name=f"R{i}", master=master["id"], role="device",
                                    parameters=dict(r="2K"), unconnected={},
                                    connections=dict(PLUS="TOP", MINUS="RETURN"))
                               for i in range(4)])
        base["bindings"]["masters"] = [master]
        base["geometry"]["binding_digest"] = digest(base["bindings"])
        base["geometry"]["instances"] = [dict(copy.deepcopy(geometry), instance=i["id"])
                                             for i in spec["instances"]]
        base["layout"].update(ports={}, routing=dict(mode="stub"), instances={
            f"R{i}": dict(xy=[2*i, 0], orientation="R0") for i in range(4)})
        base["preview_digest"] = preview_circuit(spec, base["bindings"])["preview_digest"]
        base.pop("geometry_plan_digest")
        preview = preview_geometry(**base)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        base["geometry_plan_digest"] = preview["geometry_plan_digest"]
        create(server, base, "passive-source")
        # Own the synthetic source, with degree-two contacts along two rails.
        assert bridge.skill(
            'let((busCv busWire) busCv=dbOpenCellViewByType("genericWork" "bus_source" '
            '"schematic" "" "a") foreach(busShape busCv~>shapes '
            'when(car(busShape~>lpp)=="wire" dbDeleteObject(busShape))) '
            'foreach(busRow list(list("TOP" list(0:0 6:0)) '
            'list("RETURN" list(0:-0.375 6:-0.375))) '
            'busWire=car(schCreateWire(busCv "draw" "full" cadr(busRow) 0.0625 0.0625 0.0)) '
            'schCreateWireLabel(busCv busWire car(cadr(busRow)) car(busRow) '
            '"lowerLeft" "R0" "stick" 0.0625 nil)) schCheck(busCv) dbSave(busCv) '
            'dbClose(busCv) t)') == "t"
        source_oa = tmp_path / "library/bus_source/schematic/sch.oa"
        before = oa_hash(source_oa)
        record = capture(server, tmp_path, "bus_source")
        prepared = prepare_template(dict(
            template_ref=record["template_ref"], bindings=base["bindings"],
            target=dict(library="genericWork", cell="bus_target", view="schematic"),
            devices=[dict(source_device=d["id"], instance=d["source_name"],
                          name=d["source_name"], master=master["id"], parameters=dict(r="2K"))
                     for d in record["topology"]["devices"]]), workspace=tmp_path)
        args = dict(spec=prepared["spec"], bindings=base["bindings"], geometry=base["geometry"],
                    template_use=prepared["template_use"],
                    preview_digest=prepared["preview_digest"])
        placement = preview_placement(dict(
            template_ref=record["template_ref"],
            **{k: args[k] for k in ("spec", "bindings", "geometry", "template_use")},
            options=dict(column_gap=3, clearance=0,
                         routing=dict(mode="end_to_end", engine="template", fallback="reject"))),
            workspace=tmp_path)
        args["layout"] = placement["layout"]
        preview = preview_geometry(**args, workspace=tmp_path)
        save_json(tmp_path / "target-preview.json", preview)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        assert all(r["method"] == "orthogonal_deformation"
                   for r in preview["plan"]["routing"]["nets"])
        args["geometry_plan_digest"] = preview["geometry_plan_digest"]
        done = create(server, args, "passive-target")
        physical = readback(bridge, args["spec"], preview["plan"]["wires"])
        screenshot = export_schematic(bridge, tmp_path, args["spec"]["target"], "passive-target")
        assert oa_hash(source_oa) == before
        assert done["created"]["svg_preview"]["status"] == "ready"
        save_json(tmp_path / "acceptance.json", dict(placement=placement, preview=preview, **done,
            physical_readback=physical, screenshot=screenshot, source_sha256_before=before,
            source_sha256_after=oa_hash(source_oa)))
