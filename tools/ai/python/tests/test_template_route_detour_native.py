"""Independent passive topologies exercise single and multiple edge detours."""

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

pytestmark = pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1",
                                reason="licensed detour acceptance")


@pytest.mark.parametrize("mode", ["single", "multiple", "free_track", "coordinated", "local_order",
                                  "component", "joint_motion", "network_search",
                                  "network_priority", "network_alternatives"])
def test_deformed_passive_crossing_uses_legal_local_detour(tmp_path, monkeypatch, mode):
    multiple = mode == "multiple"
    free_track = mode == "free_track"
    coordinated = mode == "coordinated"
    local_order = mode == "local_order"
    component = mode == "component"
    joint_motion = mode == "joint_motion"
    network_search = mode == "network_search"
    network_alternatives = mode == "network_alternatives"
    network_priority = mode in {"network_priority", "network_alternatives"}
    count = 8 if multiple else 6
    rails = ["H", "J", "V"] if multiple else ["H", "V"]
    rail_for = (["H", "H", "J", "J", "V", "V", "V", "V"] if multiple else
                ["H", "H", "V", "V", "V", "V"])
    if free_track:
        rail_for = ["V", "V", "V", "V", "H", "H"]
    if local_order:
        rails, rail_for = ["V"], ["V"] * count
    if component:
        count, rail_for = 5, ["H", "H", "H", "V", "V"]
    if joint_motion:
        count, rails = 8, ["H", "J", "V"]
        rail_for = ["H", "H", "H", "H", "J", "J", "V", "V"]
    if network_search:
        count, rail_for = 4, ["H", "H", "V", "V"]
    if network_priority:
        rails, rail_for = ["H", "J", "V"], ["H", "H", "J", "J", "V", "V"]
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
        base = fixture({c: sample_master(bridge, c) for c in ("res", "cap")}, "detour_source")
        master = copy.deepcopy(base["bindings"]["masters"][0])
        geometry = copy.deepcopy(base["geometry"]["instances"][0])
        observed = json.loads(bridge.skill("aiCrBindingGeometry(" + " ".join(json.dumps(v) for v in
                              ["analogLib", "res", "symbol", master["library_path"]]) + ")"))
        for key in ("occupied_bbox", "annotation_bbox"):
            geometry[key] = observed[key]
        spec = base["spec"]
        spec.update(notes=[], nets=[dict(name=n, scope="local") for n in
                                    rails + [f"E{i}" for i in range(count)]],
                    ports=[dict(name=f"E{i}", net=f"E{i}", direction="inputOutput")
                           for i in range(count)],
                    instances=[dict(id=f"R{i}", name=f"R{i}", master=master["id"], role="device",
                                    parameters=dict(r="2K"), unconnected={},
                                    connections=dict(PLUS=rail_for[i], MINUS=f"E{i}"))
                               for i in range(count)])
        base["bindings"]["masters"] = [master]
        base["geometry"]["binding_digest"] = digest(base["bindings"])
        base["geometry"]["instances"] = [dict(copy.deepcopy(geometry), instance=f"R{i}")
                                             for i in range(count)]
        places = [([-3, 0], "R270"), ([3, 0], "R90"), ([0, -3], "R0"),
                  ([0, 3], "R180"), ([0, -1.5], "R90"), ([0, 1.5], "R90")]
        if multiple:
            places = [([-3, -3], "R270"), ([3, -3], "R90"), ([-3, 3], "R270"),
                      ([3, 3], "R90"), ([0, -5], "R0"), ([0, 5], "R180"),
                      ([0, -1], "R90"), ([0, 1], "R90")]
        source_wires = ('list(list("H" list(-3:-3 3:-3)) list("J" list(-3:3 3:3)) '
                        'list("V" list(0:-5 0:5)))' if multiple else
                        'list(list("H" list(-3:0 3:0)) list("V" list(0:-3 0:3)))')
        if free_track:
            places = [([2*i, 0], "R0") for i in range(4)] + [
                ([3, .5], "R0"), ([3, 1.5], "R180")]
            source_wires = ('list(list("H" list(3:0.5 3:1.5)) list("V" list(0:1 6:1)) '
                            'list("V" list(0:0 0:1)) list("V" list(2:0 2:1)) '
                            'list("V" list(4:0 4:1)) list("V" list(6:0 6:1)))')
        if coordinated:
            places = [([-3, 0], "R0"), ([3, 0], "R0"), ([0, 1], "R0"),
                      ([0, 3], "R180"), ([0, 1.5], "R90"), ([0, 2.5], "R90")]
            source_wires = ('list(list("H" list(-3:0 -3:2 3:2 3:0)) '
                            'list("V" list(0:1 0:3)))')
        if local_order:
            places = [([0, 4], "R180"), ([4, 6], "R180"), ([8, 8], "R180"),
                      ([0, 1], "R90"), ([4, 2], "R90"), ([8, 3], "R90")]
            source_wires = ('list(list("V" list(0:0 8:0)) list("V" list(0:0 0:4)) '
                            'list("V" list(4:0 4:6)) list("V" list(8:0 8:8)))')
        if component:
            places = [([-3, 0], "R270"), ([3, 0], "R90"), ([0, -3], "R0"),
                      ([-1, 1], "R180"), ([1, 1], "R180")]
            source_wires = ('list(list("H" list(-3:0 3:0)) list("H" list(0:-3 0:0)) '
                            'list("V" list(-1:1 -1:0.5 1:0.5 1:1)))')
        if joint_motion:
            places = [([-3, 0], "R270"), ([0, -3], "R0"), ([7, 2], "R90"),
                      ([4, 5], "R180"), ([-1, 1], "R180"), ([1, 1], "R180"),
                      ([3, 3], "R180"), ([5, 3], "R180")]
            source_wires = ('list(list("H" list(-3:0 4:0 4:5)) '
                            'list("H" list(0:-3 0:0)) list("H" list(4:2 7:2)) '
                            'list("J" list(-1:1 -1:0.5 1:0.5 1:1)) '
                            'list("V" list(3:3 3:2.5 5:2.5 5:3)))')
        if network_search:
            places = [([-4, -4], "R270"), ([2, 2], "R90"),
                      ([0, 0], "R90"), ([-2, 4], "R180")]
            source_wires = (
                'list(list("H" list(-4:-4 -1:-4 -1:2 2:2)) '
                'list("V" list(0:0 0:-5 -2:-5 -2:4)))')
        if network_priority:
            places = [([2, 4], "R180"), ([-4, 0], "R180"), ([4, 2], "R90"),
                      ([0, -2], "R270"), ([0, 4], "R90"), ([-2, -2], "R90")]
            source_wires = ('list(list("H" list(2:4 2:-3 -4:-3 -4:0)) '
                            'list("J" list(4:2 1:2 1:-2 0:-2)) '
                            'list("V" list(0:4 -5:4 -5:-2 -2:-2)))')
        if network_alternatives:
            # Rotate the same independent source geometry by R90. Previously
            # the first dead conflict discarded all other choices in this view.
            places = [([-4, 2], "R270"), ([0, -4], "R270"), ([-2, 4], "R180"),
                      ([2, 0], "R0"), ([-4, 0], "R180"), ([2, -2], "R180")]
            source_wires = ('list(list("H" list(-4:2 3:2 3:-4 0:-4)) '
                            'list("J" list(-2:4 -2:1 2:1 2:0)) '
                            'list("V" list(-4:0 -4:-5 2:-5 2:-2)))')
        base["layout"].update(
            ports={f"E{i}": dict(xy=[-6, 5-i], escape="right") for i in range(count)},
            routing=dict(mode="stub"), instances={f"R{i}": dict(xy=xy, orientation=o)
                                                  for i, (xy, o) in enumerate(places)})
        base["preview_digest"] = preview_circuit(spec, base["bindings"])["preview_digest"]
        base.pop("geometry_plan_digest")
        preview = preview_geometry(**base)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        base["geometry_plan_digest"] = preview["geometry_plan_digest"]
        create(server, base, "detour-source")
        checked = json.loads(bridge.skill(
            'let((ctCv ctWire ctCheck) ctCv=dbOpenCellViewByType("genericWork" "detour_source" '
            '"schematic" "" "a") foreach(ctShape ctCv~>shapes '
            'when(and(car(ctShape~>lpp)=="wire" member(ctShape~>net~>name list("H" "J" "V"))) '
            'dbDeleteObject(ctShape))) '
            "foreach(ctRow " + source_wires + " "
            'ctWire=car(schCreateWire(ctCv "draw" "full" cadr(ctRow) 0.0625 0.0625 0.0)) '
            'schCreateWireLabel(ctCv ctWire car(cadr(ctRow)) car(ctRow) '
            '"lowerLeft" "R0" "stick" 0.0625 nil)) ctCheck=schCheck(ctCv) dbSave(ctCv) '
            'dbClose(ctCv) aiCircuitJson(ctCheck))'))
        assert checked == [0, 0]
        source_oa = tmp_path / "library/detour_source/schematic/sch.oa"
        before = oa_hash(source_oa)
        record = capture(server, tmp_path, "detour_source")
        reference = compile_reference(record)
        crossing_ids = {n["id"] for n in record["topology"]["nets"]
                        if n["source_name"] in rails}
        assert all(reference["nets"][n]["eligible"] and
                   (local_order or component or joint_motion
                    or reference["nets"][n]["isolated_crossings"])
                   for n in crossing_ids)
        prepared = prepare_template(dict(
            template_ref=record["template_ref"], bindings=base["bindings"],
            target=dict(library="genericWork", cell="detour_target", view="schematic"),
            devices=[dict(source_device=d["id"], instance=d["source_name"],
                          name=d["source_name"], master=master["id"], parameters=dict(r="2K"))
                     for d in record["topology"]["devices"]]), workspace=tmp_path)
        args = dict(spec=prepared["spec"], bindings=base["bindings"], geometry=base["geometry"],
                    template_use=prepared["template_use"],
                    preview_digest=prepared["preview_digest"])
        placement = preview_placement(dict(template_ref=record["template_ref"],
            **{k: args[k] for k in ("spec", "bindings", "geometry", "template_use")},
            options=dict(clearance=0, column_gap=3,
                row_gap=.375 if free_track or coordinated or local_order else 3, routing=dict(
                mode="end_to_end", engine="template", fallback="stub",
                **(dict(max_bends=1) if free_track else {})))), workspace=tmp_path)
        args["layout"] = copy.deepcopy(placement["layout"])
        if local_order:
            # Independent user-requested move: exchange two leaf rows while
            # preserving their columns and all middle terminal contacts.
            # This intentionally exercises nonmonotone routing, not placement.
            positions = args["layout"]["instances"]
            positions["R0"]["xy"][1], positions["R1"]["xy"][1] = (
                positions["R1"]["xy"][1], positions["R0"]["xy"][1])
        if component:
            # Explicit independent routing test: retain source device positions,
            # then move the foreign U-shaped rail onto the unpinned junction.
            # Record this separately; it is not an automatic placement claim.
            args["layout"]["instances"] = copy.deepcopy(base["layout"]["instances"])
            for key in ("R3", "R4"):
                args["layout"]["instances"][key]["xy"][1] = .5
        if joint_motion:
            args["layout"]["instances"] = copy.deepcopy(base["layout"]["instances"])
            for key in ("R4", "R5", "R6", "R7"):
                args["layout"]["instances"][key]["xy"][1] -= .5
        if network_search:
            # Preserve the two source paths with an explicitly requested
            # one-corner target routing budget. Neither net can be repaired
            # with the other fixed; both must be changed atomically.
            args["layout"]["instances"] = copy.deepcopy(base["layout"]["instances"])
            args["layout"]["routing"].update(max_bends=1, max_detour=1.25)
        if network_priority:
            args["layout"]["instances"] = copy.deepcopy(base["layout"]["instances"])
            args["layout"]["routing"].update(max_bends=1, max_detour=1.5)
        preview = preview_geometry(**args, workspace=tmp_path)
        save_json(tmp_path / "target-preview.json", preview)
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        crossed = [r for r in preview["plan"]["routing"]["nets"] if r["net"] in rails]
        assert len(crossed) == len(rails)
        assert all(r["status"] in {"reused", "rerouted"} for r in crossed), crossed
        method = ("coordinated_network_search" if network_search or network_priority else
                  "final_conflict_component" if component or joint_motion else
                  "local_order_deformation" if local_order else
                  "coordinated_deformation" if coordinated else
                  "orthogonal_deformation_free_track" if free_track else
                  "orthogonal_deformation_detour")
        assert any(r.get("method") == method for r in crossed), crossed
        if network_search:
            assert all(r.get("method") == method for r in crossed), crossed
            assert any(d.get("changed_nets") == ["H", "V"] for r in crossed
                       for d in r.get("diagnostics", [])), crossed
        if network_priority:
            applied = [d for r in crossed for d in r.get("diagnostics", [])
                       if d["code"] == "target_network_search_applied"]
            assert applied and len(applied[0]["conflict_pairs"]) >= 2, crossed
            if network_alternatives:
                assert applied[0]["stalled_pairs"] > 0, crossed
                assert applied[0]["fallback_replans"] > 0, crossed
        if multiple:
            assert any(len(d.get("source_edges", [])) == 2 for r in crossed
                       for d in r.get("diagnostics", [])), crossed
        if joint_motion:
            assert any(len(d.get("junction_moves", [])) == 2 for r in crossed
                       for d in r.get("diagnostics", [])), crossed
            assert any(d.get("motion_units") == 2 for r in crossed
                       for d in r.get("diagnostics", [])), crossed
        args["geometry_plan_digest"] = preview["geometry_plan_digest"]
        done = create(server, args, "detour-target")
        physical = readback(bridge, args["spec"], preview["plan"]["wires"])
        screenshot = export_schematic(bridge, tmp_path, args["spec"]["target"], "detour-target")
        assert oa_hash(source_oa) == before
        save_json(tmp_path / "acceptance.json", dict(placement=placement, preview=preview,
            source_reference=reference, source_check=checked, **done, physical_readback=physical,
            screenshot=screenshot, source_sha256_before=before,
            source_sha256_after=oa_hash(source_oa),
            **(dict(layout_override=dict(operation="foreign_rail_over_junction",
                                         instances=args["layout"]["instances"]))
               if component or joint_motion else {}),
            **(dict(layout_override=dict(operation="retain_source_positions",
                                         instances=args["layout"]["instances"],
                                         routing=args["layout"]["routing"]))
               if network_search or network_priority else {}),
            **(dict(layout_override=dict(operation="swap_leaf_rows", instances=["R0", "R1"]))
               if local_order else {})))
