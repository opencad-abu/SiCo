"""PDK-neutral shared terminal geometry through native capture, reuse and readback."""

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
from cadai.template_wire_attachment import compile_reference


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed acceptance")
def test_shared_device_terminals_survive_bus_deformation_and_native_reopen(tmp_path, monkeypatch):
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
        bridge.skill("ddCreateLib(\"testDevices\" " + json.dumps(str(tmp_path / "devices")) + ")")
        # Copy only a test symbol. Three distinct named terminals share its lower pin.
        observed = json.loads(bridge.skill('''let((ctSrc ctCv ctNet ctTerm ctFig ctBox)
          ctSrc=dbOpenCellViewByType("analogLib" "cap" "symbol" "" "r")
          ctCv=dbCopyCellView(ctSrc "testDevices" "contact_device" "symbol")
          dbClose(ctSrc)
          ctBox=car(car(setof(ct ctCv~>terminals ct~>name=="MINUS"))~>pins)~>fig~>bBox
          foreach(ctName list("TAP1" "TAP2")
            ctNet=dbCreateNet(ctCv ctName) ctTerm=dbCreateTerm(ctNet ctName "inputOutput")
            ctFig=dbCreateRect(ctCv list("pin" "drawing") ctBox)
            dbCreatePin(ctNet ctFig nil ctTerm))
          dbSave(ctCv) dbClose(ctCv)
          aiCrBindingGeometry("testDevices" "contact_device" "symbol"
            ddGetObjReadPath(ddGetObj("testDevices"))))'''))
        assert observed["ok"]
        base = fixture({c: sample_master(bridge, c) for c in ("res", "cap")}, "shared_source")
        master = copy.deepcopy(base["bindings"]["masters"][0])
        master.update(target=dict(library="testDevices", cell="contact_device", view="symbol"),
                      library_path=str(tmp_path / "devices"), revision=digest(observed),
                      parameters=[], terminals=[dict(name=n, direction="inputOutput")
                                                 for n, _ in observed["pin_centers"]])
        geometry = dict(master_revision=master["revision"], parameters_digest=digest({}),
                        occupied_bbox=observed["occupied_bbox"],
                        annotation_bbox=observed["annotation_bbox"],
                        terminals=[dict(name=n, anchors=[dict(id="a0", xy=points[0],
                                        escape="up" if n == "PLUS" else "down")])
                                   for n, points in observed["pin_centers"]])
        spec = base["spec"]
        spec.update(ports=[], notes=[], nets=[dict(name=n, scope="local") for n in ("TOP", "LOW")],
                    instances=[dict(id=f"X{i}", name=f"X{i}", master=master["id"], role="device",
                                    parameters={}, unconnected={}, connections={
                                        n: "TOP" if n == "PLUS" else "LOW"
                                        for n, _ in observed["pin_centers"]}) for i in range(4)])
        base["bindings"]["masters"] = [master]
        base["geometry"].update(binding_digest=digest(base["bindings"]), instances=[
            dict(copy.deepcopy(geometry), instance=f"X{i}") for i in range(4)])
        base["layout"].update(ports={}, routing=dict(mode="stub"), instances={
            f"X{i}": dict(xy=[2*i, 0], orientation="R0") for i in range(4)})
        base["preview_digest"] = preview_circuit(spec, base["bindings"])["preview_digest"]
        base.pop("geometry_plan_digest")
        preview = preview_geometry(**base)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        base["geometry_plan_digest"] = preview["geometry_plan_digest"]
        create(server, base, "shared-source")
        check = json.loads(bridge.skill(
            'let((ctCv ctWire ctCheck) ctCv=dbOpenCellViewByType("genericWork" "shared_source" '
            '"schematic" "" "a") foreach(ctShape ctCv~>shapes '
            'when(car(ctShape~>lpp)=="wire" dbDeleteObject(ctShape))) '
            'foreach(ctRow list(list("TOP" list(0:0 6:0)) '
            'list("LOW" list(0:-0.375 6:-0.375))) '
            'ctWire=car(schCreateWire(ctCv "draw" "full" cadr(ctRow) 0.0625 0.0625 0.0)) '
            'schCreateWireLabel(ctCv ctWire car(cadr(ctRow)) car(ctRow) '
            '"lowerLeft" "R0" "stick" 0.0625 nil)) ctCheck=schCheck(ctCv) dbSave(ctCv) '
            'dbClose(ctCv) aiCircuitJson(ctCheck))'))
        assert check == [0, 0]
        source_oa = tmp_path / "library/shared_source/schematic/sch.oa"
        before = oa_hash(source_oa)
        record = capture(server, tmp_path, "shared_source")
        compiled = compile_reference(record)
        lower = next(n["id"] for n in record["topology"]["nets"] if n["source_name"] == "LOW")
        assert compiled["nets"][lower]["gaps"] == []
        assert len(compiled["nets"][lower]["terminal_contacts"]) == 4
        assert len(compiled["nets"][lower]["anchors"]) == 12
        prepared = prepare_template(dict(
            template_ref=record["template_ref"], bindings=base["bindings"],
            target=dict(library="genericWork", cell="shared_target", view="schematic"),
            devices=[dict(source_device=d["id"], instance=d["source_name"],
                          name=d["source_name"], master=master["id"], parameters={})
                     for d in record["topology"]["devices"]]), workspace=tmp_path)
        args = dict(spec=prepared["spec"], bindings=base["bindings"], geometry=base["geometry"],
                    template_use=prepared["template_use"],
                    preview_digest=prepared["preview_digest"])
        placement = preview_placement(dict(template_ref=record["template_ref"],
            **{k: args[k] for k in ("spec", "bindings", "geometry", "template_use")},
            options=dict(column_gap=3, clearance=0, routing=dict(
                mode="end_to_end", engine="template", fallback="reject"))), workspace=tmp_path)
        args["layout"] = placement["layout"]
        preview = preview_geometry(**args, workspace=tmp_path)
        save_json(tmp_path / "target-preview.json", preview)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        assert all(r["method"] == "orthogonal_deformation"
                   for r in preview["plan"]["routing"]["nets"])
        assert preview["plan"]["routing"]["metrics"]["routed_terminals"] == 16
        args["geometry_plan_digest"] = preview["geometry_plan_digest"]
        done = create(server, args, "shared-target")
        physical = readback(bridge, args["spec"], preview["plan"]["wires"])
        terms = json.loads(bridge.skill(
            'let((ctCv ctRows) ctCv=dbOpenCellViewByType("genericWork" "shared_target" '
            '"schematic" "" "r") ctRows=aiCircuitJson(foreach(mapcar ctInst ctCv~>instances '
            'list(ctInst~>name foreach(mapcar ctIt ctInst~>instTerms '
            'list(ctIt~>name ctIt~>net~>name))))) dbClose(ctCv) ctRows)'))
        assert len(terms) == 4
        assert all(dict(pins) == dict(PLUS="TOP", MINUS="LOW", TAP1="LOW", TAP2="LOW")
                   for _, pins in terms)
        screenshot = export_schematic(bridge, tmp_path, args["spec"]["target"], "shared-target")
        assert oa_hash(source_oa) == before
        save_json(tmp_path / "acceptance.json", dict(placement=placement, preview=preview, **done,
            physical_readback=physical, terminal_readback=terms, screenshot=screenshot,
            source_sha256_before=before, source_sha256_after=oa_hash(source_oa)))
