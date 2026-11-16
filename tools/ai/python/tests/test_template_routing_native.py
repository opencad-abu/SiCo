"""Actual saved capture v2 -> explicit target -> template wires -> OA readback."""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import AI_ROOT, sample_master, session
from test_circuit_rc_gui import mcp_call

from cadai.circuit_geometry import preview_geometry
from cadai.template_catalog import TemplateCatalog
from cadai.template_circuit import prepare_template
from cadai.template_wire_attachment import compile_reference


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed P1 acceptance")
def test_capture_and_reuse_real_branch_paths(tmp_path, monkeypatch):
    with session(tmp_path) as (bridge, server, _):
        server.client.runtime = server.client.dispatcher.runtime
        original_request = bridge.request

        def request(message, timeout):
            if message["method"] == "capture_circuit_template":
                bridge.evaluate_path(message["source_path"], message["output_path"],
                                     evaluator="aiEvalExpression")
                payload = json.loads(Path(message["output_path"]).read_text())
                assert payload["ok"], payload
                return True, {"result_path": message["output_path"]}
            return original_request(message, timeout)

        monkeypatch.setattr(bridge, "request", request)
        for name in ("AI_templates.il", "AI_template_capture_geometry.il"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        data = {cell: sample_master(bridge, cell) for cell in ("res", "cap")}
        task = mcp_call(
            server,
            "begin_circuit_task",
            {"task_id": "p1", "presentation": "background", "choice_confirmed": True},
        )
        source = fixture(
            data, "source", routing={"mode": "end_to_end", "engine": "cadence_route",
                                    "pg": "route", "ports": "route"}
        )
        for geo in source["geometry"]["instances"]:
            master = next(m for m in source["bindings"]["masters"] if m["id"] == geo["instance"])
            observed = json.loads(bridge.skill("aiCrBindingGeometry(" + " ".join(
                json.dumps(v) for v in ["analogLib", master["target"]["cell"], "symbol",
                                       master["library_path"]]) + ")"))
            geo["occupied_bbox"] = observed["occupied_bbox"]
        source["geometry_plan_digest"] = preview_geometry(**{
            k: v for k, v in source.items() if k != "geometry_plan_digest"
        })["geometry_plan_digest"]

        def create(args, request):
            pre = mcp_call(
                server,
                "prepare_circuit_creation",
                {**args, "task_ref": task["task_ref"], "request_id": request},
            )
            assert pre["ok"], pre
            created = mcp_call(
                server,
                "create_circuit_from_plan",
                {
                    "prepare_ref": pre["prepare_ref"],
                    "request_id": request,
                    "geometry_plan_digest": args["geometry_plan_digest"],
                },
            )
            assert created["ok"] and created["saved"] and created["readback_verified"], created
            assert created["check"] == [0, 0], created
            return created

        create(source, "source")
        # Own a deliberate physical reference with T contacts and no ambiguous X
        # crossings. The native router is allowed to cross disconnected nets and
        # end inside arrow polygons; those references intentionally fail closed.
        bridge.skill('let((cv w) cv=dbOpenCellViewByType("genericWork" "source" '
                     '"schematic" "" "a") '
                     'foreach(sh cv~>shapes when(car(sh~>lpp)=="wire" dbDeleteObject(sh))) '
                     'foreach(row list(list("IN" list(-2:-1 -0.5:-1 -0.5:0 0:0)) '
                     'list("OUT" list(0.375:0 3:0)) list(nil list(3:0 5:0)) '
                     'list("VSS" list(-2:1 6:1 6:-2 3:-2 3:-0.375))) '
                     'w=car(schCreateWire(cv "draw" "full" cadr(row) 0.0625 0.0625 0.0)) '
                     'when(car(row) schCreateWireLabel(cv w car(cadr(row)) car(row) '
                     '"lowerLeft" "R0" "stick" 0.0625 nil))) '
                         'schCheck(cv) dbSave(cv) dbClose(cv))')
        source_oa = tmp_path / "library" / "source" / "schematic" / "sch.oa"
        source_before_capture = hashlib.sha256(source_oa.read_bytes()).hexdigest()
        captured = mcp_call(
            server,
            "extract_circuit_templates",
            {
                "library": "genericWork",
                "cell": "source",
                "assets": ["schematic"],
                "capture_schema": "cad.template.capture.v2",
            },
        )
        assert captured["ok"], captured
        assert hashlib.sha256(source_oa.read_bytes()).hexdigest() == source_before_capture
        record = TemplateCatalog(workspace=tmp_path).get(captured["template_ref"])
        attached = compile_reference(record)
        (tmp_path / "attachments.json").write_text(json.dumps(attached, indent=2))
        inputs = {i["name"]: i for i in source["spec"]["instances"]}
        selections = []
        for device in record["topology"]["devices"]:
            target = inputs[device["source_name"]]
            selections.append(
                {
                    "source_device": device["id"],
                    "instance": target["id"],
                    "name": target["name"],
                    "master": target["master"],
                    "parameters": target["parameters"],
                }
            )
        prepared = prepare_template(
            {
                "template_ref": record["template_ref"],
                "target": {"library": "genericWork", "cell": "target", "view": "schematic"},
                "bindings": source["bindings"],
                "devices": selections,
            },
            workspace=tmp_path,
        )
        target = {k: copy.deepcopy(v) for k, v in source.items() if k != "geometry_plan_digest"}
        target.update(
            spec=prepared["spec"],
            template_use=prepared["template_use"],
            preview_digest=prepared["preview_digest"],
        )
        target["layout"]["routing"] = {
            "mode": "end_to_end",
            "engine": "template",
            "ports": "route",
            "fallback": "stub",
        }
        for place in target["layout"]["instances"].values():
            place["xy"] = [place["xy"][0] + 10, place["xy"][1] + 5]
        for place in target["layout"]["ports"].values():
            place["xy"] = [place["xy"][0] + 10, place["xy"][1] + 5]
        preview = preview_geometry(**target, workspace=tmp_path)
        (tmp_path / "target-preview.json").write_text(json.dumps(preview, indent=2))
        assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
        reused = [n for n in preview["plan"]["routing"]["nets"] if n["status"] == "reused"]
        assert any(n["terminals"] >= 3 for n in reused), preview["plan"]["routing"]
        target["geometry_plan_digest"] = preview["geometry_plan_digest"]
        created = create(target, "target")
        assert {
            row["net"]: row["status"] for row in created["template_routing"]["nets"]
        } == {
            row["net"]: row["status"] for row in preview["plan"]["routing"]["nets"]
        }
        inspected = mcp_call(
            server, "inspect_created_circuit", {"circuit_ref": created["circuit_ref"]}
        )
        assert inspected["ok"] and inspected["readback_verified"], inspected
        replay = create(target, "target")
        assert replay == created

        # Move only the capacitor's degree-one VSS endpoint.  The filter and
        # ports retain the rigid frame, so template routing must keep the
        # captured trunk and repair that one local branch before native create.
        reroute_prepared = prepare_template(
            {
                "template_ref": record["template_ref"],
                "target": {
                    "library": "genericWork",
                    "cell": "target_reroute",
                    "view": "schematic",
                },
                "bindings": source["bindings"],
                "devices": selections,
            },
            workspace=tmp_path,
        )
        reroute = {k: copy.deepcopy(v) for k, v in source.items()
                   if k != "geometry_plan_digest"}
        reroute.update(
            spec=reroute_prepared["spec"],
            template_use=reroute_prepared["template_use"],
            preview_digest=reroute_prepared["preview_digest"],
        )
        reroute["layout"]["routing"] = {
            "mode": "end_to_end",
            "engine": "template",
            "ports": "route",
            "fallback": "reject",
        }
        for place in reroute["layout"]["instances"].values():
            place["xy"] = [place["xy"][0] + 10, place["xy"][1] + 5]
        for place in reroute["layout"]["ports"].values():
            place["xy"] = [place["xy"][0] + 10, place["xy"][1] + 5]
        # One grid step is enough to invalidate the rigid frame while keeping
        # the short terminal branch repairable around the capacitor body.
        reroute["layout"]["instances"]["load"]["xy"][1] += 0.5
        reroute_preview = preview_geometry(**reroute, workspace=tmp_path)
        (tmp_path / "reroute-preview.json").write_text(json.dumps(reroute_preview, indent=2))
        assert reroute_preview["plan"]["geometry_checks_passed"], reroute_preview["plan"]["issues"]
        assert any(
            row["status"] == "rerouted" for row in reroute_preview["plan"]["routing"]["nets"]
        ), reroute_preview["plan"]["routing"]
        assert all(
            row["status"] in {"reused", "rerouted"}
            for row in reroute_preview["plan"]["routing"]["nets"]
        )
        reroute["geometry_plan_digest"] = reroute_preview["geometry_plan_digest"]
        rerouted_created = create(reroute, "target-reroute")
        assert any(
            row["status"] == "rerouted" for row in rerouted_created["template_routing"]["nets"]
        )
        rerouted_inspected = mcp_call(
            server, "inspect_created_circuit", {"circuit_ref": rerouted_created["circuit_ref"]}
        )
        assert (
            rerouted_inspected["ok"] and rerouted_inspected["readback_verified"]
        ), rerouted_inspected
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                {
                    "capture": captured,
                    "routing": preview["plan"]["routing"],
                    "created": created,
                    "inspected": inspected,
                    "reroute_routing": reroute_preview["plan"]["routing"],
                    "rerouted_created": rerouted_created,
                    "rerouted_inspected": rerouted_inspected,
                },
                indent=2,
            )
        )
