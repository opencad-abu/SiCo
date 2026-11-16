"""Independent passive crossings: guarded capture and conservative target creation."""

import copy
import json
import os

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import AI_ROOT, sample_master, session
from template_pdk_support import capture, create, oa_hash, readback, save_json
from template_visual_support import export_schematic

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_geometry_schema import bounds, overlap
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest
from cadai.template_circuit import prepare_template
from cadai.template_placement import preview_placement
from cadai.template_wire_attachment import compile_reference

pytestmark = pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1",
                                reason="licensed contact acceptance")


def test_crossed_passive_source_creates_without_target_crossings(tmp_path, monkeypatch):
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
        base = fixture({c: sample_master(bridge, c) for c in ("res", "cap")}, "cross_source")
        master = copy.deepcopy(base["bindings"]["masters"][0])
        geometry = copy.deepcopy(base["geometry"]["instances"][0])
        observed = json.loads(bridge.skill("aiCrBindingGeometry(" + " ".join(json.dumps(v) for v in
                              ["analogLib", "res", "symbol", master["library_path"]]) + ")"))
        for key in ("occupied_bbox", "annotation_bbox"):
            geometry[key] = observed[key]
        spec = base["spec"]
        spec.update(notes=[], nets=[dict(name=n, scope="local") for n in
                                    ("H", "V", "E0", "E1", "E2", "E3")],
                    ports=[dict(name=f"E{i}", net=f"E{i}", direction="inputOutput")
                           for i in range(4)],
                    instances=[dict(id=f"R{i}", name=f"R{i}", master=master["id"], role="device",
                                    parameters=dict(r="2K"), unconnected={},
                                    connections=dict(PLUS="H" if i < 2 else "V", MINUS=f"E{i}"))
                               for i in range(4)])
        base["bindings"]["masters"] = [master]
        base["geometry"]["binding_digest"] = digest(base["bindings"])
        base["geometry"]["instances"] = [dict(copy.deepcopy(geometry), instance=f"R{i}")
                                             for i in range(4)]
        places = [([-3, 0], "R270"), ([3, 0], "R90"), ([0, -3], "R0"), ([0, 3], "R180")]
        base["layout"].update(
            ports={f"E{i}": dict(xy=[-6, 5-i], escape="right") for i in range(4)},
            routing=dict(mode="stub"), instances={f"R{i}": dict(xy=xy, orientation=o)
                                                  for i, (xy, o) in enumerate(places)})
        base["preview_digest"] = preview_circuit(spec, base["bindings"])["preview_digest"]
        base.pop("geometry_plan_digest")
        preview = preview_geometry(**base)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        base["geometry_plan_digest"] = preview["geometry_plan_digest"]
        create(server, base, "contact-source")
        checked = json.loads(bridge.skill(
            'let((ctCv ctWire ctCheck) ctCv=dbOpenCellViewByType("genericWork" "cross_source" '
            '"schematic" "" "a") foreach(ctShape ctCv~>shapes '
            'when(and(car(ctShape~>lpp)=="wire" member(ctShape~>net~>name list("H" "V"))) '
            'dbDeleteObject(ctShape))) '
            'foreach(ctRow list(list("H" list(-3:0 3:0)) list("V" list(0:-3 0:3))) '
            'ctWire=car(schCreateWire(ctCv "draw" "full" cadr(ctRow) 0.0625 0.0625 0.0)) '
            'schCreateWireLabel(ctCv ctWire car(cadr(ctRow)) car(ctRow) '
            '"lowerLeft" "R0" "stick" 0.0625 nil)) ctCheck=schCheck(ctCv) dbSave(ctCv) '
            'dbClose(ctCv) aiCircuitJson(ctCheck))'))
        assert checked == [0, 0]
        source_oa = tmp_path / "library/cross_source/schematic/sch.oa"
        before = oa_hash(source_oa)
        record = capture(server, tmp_path, "cross_source")
        reference = compile_reference(record)
        crossing_ids = {n["id"] for n in record["topology"]["nets"]
                        if n["source_name"] in {"H", "V"}}
        assert all(reference["nets"][n]["eligible"] and
                   len(reference["nets"][n]["isolated_crossings"]) == 1 for n in crossing_ids)
        prepared = prepare_template(dict(
            template_ref=record["template_ref"], bindings=base["bindings"],
            target=dict(library="genericWork", cell="cross_target", view="schematic"),
            devices=[dict(source_device=d["id"], instance=d["source_name"],
                          name=d["source_name"], master=master["id"], parameters=dict(r="2K"))
                     for d in record["topology"]["devices"]]), workspace=tmp_path)
        args = dict(spec=prepared["spec"], bindings=base["bindings"], geometry=base["geometry"],
                    template_use=prepared["template_use"],
                    preview_digest=prepared["preview_digest"])
        placement = preview_placement(dict(template_ref=record["template_ref"],
            **{k: args[k] for k in ("spec", "bindings", "geometry", "template_use")},
            options=dict(clearance=0, routing=dict(mode="end_to_end", engine="template",
                                                   fallback="stub"))), workspace=tmp_path)
        args["layout"] = placement["layout"]
        preview = preview_geometry(**args, workspace=tmp_path)
        save_json(tmp_path / "target-preview.json", preview)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        crossed = [r for r in preview["plan"]["routing"]["nets"] if r["net"] in {"H", "V"}]
        # Final reconciliation repairs one proven graph against the other, then
        # retains both only when their complete target paths no longer touch.
        assert {r["net"]: r["method"] for r in crossed} == {
            "H": "final_graph_detour", "V": "rigid"}
        wires = preview["plan"]["wires"]
        assert not any(overlap(bounds(h["points"]), bounds(v["points"]))
                       for h in wires if h["net"] == "H"
                       for v in wires if v["net"] == "V")
        args["geometry_plan_digest"] = preview["geometry_plan_digest"]
        done = create(server, args, "contact-target")
        physical = readback(bridge, args["spec"], preview["plan"]["wires"])
        screenshot = export_schematic(bridge, tmp_path, args["spec"]["target"], "contact-target")
        assert oa_hash(source_oa) == before
        save_json(tmp_path / "acceptance.json", dict(placement=placement, preview=preview,
            source_reference=reference, source_check=checked, **done, physical_readback=physical,
            screenshot=screenshot, source_sha256_before=before,
            source_sha256_after=oa_hash(source_oa)))


def test_native_crossing_and_contact_semantics_after_reopen(tmp_path):
    cases = {"x": ("list(-2:0 2:0)", "list(0:-2 0:2)"),
             "t": ("list(-2:0 2:0)", "list(0:0 0:2)"),
             "end": ("list(-2:0 0:0)", "list(0:0 0:2)"),
             "overlap": ("list(-2:0 2:0)", "list(0:0 3:0)")}
    results = {}
    with session(tmp_path) as (bridge, _, _):
        for name, (first, second) in cases.items():
            code = f'''let((ctCv ctWire ctCheck ctBefore ctAfter)
              ctCv=dbOpenCellViewByType("genericWork" "contact_{name}"
                "schematic" "schematic" "w")
              ctWire=car(schCreateWire(ctCv "draw" "full" {first} 0.0625 0.0625 0.0))
              schCreateWireLabel(ctCv ctWire car({first}) "H"
                "lowerLeft" "R0" "stick" 0.0625 nil)
              ctWire=car(schCreateWire(ctCv "draw" "full" {second} 0.0625 0.0625 0.0))
              schCreateWireLabel(ctCv ctWire car({second}) "V"
                "lowerLeft" "R0" "stick" 0.0625 nil)
              ctCheck=schCheck(ctCv)
              ctBefore=foreach(mapcar ctShape setof(s ctCv~>shapes s~>objType=="line")
                list(ctShape~>points ctShape~>net~>name))
              dbSave(ctCv) dbClose(ctCv)
              ctCv=dbOpenCellViewByType("genericWork" "contact_{name}" "schematic" "" "r")
              ctAfter=foreach(mapcar ctShape setof(s ctCv~>shapes s~>objType=="line")
                list(ctShape~>points ctShape~>net~>name))
              dbClose(ctCv) aiCircuitJson(list(ctCheck ctBefore ctAfter)))'''
            check, before, after = json.loads(bridge.skill(code))
            assert before == after
            if name == "x":
                # Four dangling ends warn; zero electrical errors and two intact nets.
                assert check == [0, 4]
                assert {net for _, net in after} == {"H", "V"} and len(after) == 2
            else:
                assert check[0] > 0
            results[name] = dict(check=check, before=before, after=after)
    save_json(tmp_path / "contact-semantics.json", results)
