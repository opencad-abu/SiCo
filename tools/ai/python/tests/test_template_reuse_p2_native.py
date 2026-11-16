"""Native P2 acceptance: v3 core plus one boundary peripheral survives OA lifecycle."""

import copy
import hashlib
import json
import os

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import sample_master, session
from test_circuit_rc_gui import mcp_call

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest
from cadai.template_catalog import TemplateCatalog

pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed P2 native acceptance"
)


def _source_three_devices(data):
    request = fixture(data, "p2_source", worklib="SicoTest", routing={"mode": "stub"})
    extra = copy.deepcopy(next(i for i in request["spec"]["instances"] if i["id"] == "load"))
    extra.update(id="load2", name="CL2", parameters={"c": "2p", "polyCoef": 0})
    request["spec"]["instances"].append(extra)
    geo = copy.deepcopy(
        next(i for i in request["geometry"]["instances"] if i["instance"] == "load")
    )
    geo.update(instance="load2", parameters_digest=digest(extra["parameters"]))
    request["geometry"]["instances"].append(geo)
    request["layout"]["instances"]["load2"] = {"xy": [3, 2], "orientation": "R0"}
    request.pop("geometry_plan_digest", None)
    preview = preview_circuit(request["spec"], request["bindings"])
    request["preview_digest"] = preview["preview_digest"]
    checked = preview_geometry(**request)
    assert checked["plan"]["geometry_checks_passed"], checked["plan"]["issues"]
    request["geometry_plan_digest"] = checked["geometry_plan_digest"]
    return request


def _target_request(reference, source, adaptation):
    spec = copy.deepcopy(source["spec"])
    spec["target"]["cell"] = "p2_target"
    spec["nets"].append({"name": "BIAS", "scope": "local"})
    spec["ports"].append({"name": "BIAS", "direction": "input", "net": "BIAS"})
    spec["instances"].append(
        {
            "id": "extra",
            "name": "EXTRA",
            "master": "load",
            "role": "device",
            "parameters": {"c": "1p", "polyCoef": 0},
            "connections": {"PLUS": "OUT", "MINUS": "BIAS"},
            "unconnected": {},
        }
    )
    bindings = copy.deepcopy(source["bindings"])
    for master in bindings["masters"]:
        cell = master["target"]["cell"]
        master["classification"] = {
            "kind": "capacitor" if cell == "cap" else "resistor",
            "attributes": {},
            "source_ref": "native:analogLib",
            "revision": "native:analogLib.v1",
        }
    normalized = preview_circuit(spec, bindings)["plan"]["spec"]
    by_instance_name = {i["name"]: i["id"] for i in normalized["instances"]}
    device_map = {
        d["id"]: by_instance_name[d["source_name"]] for d in reference["topology"]["devices"]
    }
    mapping = {
        "device_map": device_map,
        "terminal_map": {
            d["id"]: {p["name"]: p["name"] for p in d["pins"]}
            for d in reference["topology"]["devices"]
        },
        "net_map": {n["id"]: n["source_name"] for n in reference["topology"]["nets"]},
        "port_map": {p["name"]: p["name"] for p in reference["topology"]["ports"]},
        "omitted_groups": [],
    }
    geometry_rows = copy.deepcopy(source["geometry"]["instances"])
    extra_geo = copy.deepcopy(next(i for i in geometry_rows if i["instance"] == "load"))
    extra_geo.update(
        instance="extra", parameters_digest=digest(spec["instances"][-1]["parameters"])
    )
    geometry_rows.append(extra_geo)
    geometry = copy.deepcopy(source["geometry"])
    geometry["binding_digest"] = digest(bindings)
    geometry["binding_snapshot"] = bindings["snapshot_ref"]
    geometry["instances"] = geometry_rows
    layout = copy.deepcopy(source["layout"])
    layout["instances"].update(
        load2={"xy": [3, 2], "orientation": "R0"},
        extra={"xy": [6, 1], "orientation": "R0"},
    )
    layout["ports"].update(BIAS={"xy": [-2, -2], "escape": "right"})
    layout["ports"]["OUT"] = {"xy": [8, 0], "escape": "left"}
    layout["routing"] = {"mode": "end_to_end", "engine": "template", "fallback": "stub"}
    if adaptation == "omit":
        spec["instances"] = [i for i in spec["instances"] if i["id"] not in {"load2", "extra"}]
        spec["nets"] = [n for n in spec["nets"] if n["name"] != "BIAS"]
        spec["ports"] = [p for p in spec["ports"] if p["name"] != "BIAS"]
        geometry["instances"] = [g for g in geometry_rows
                                 if g["instance"] not in {"load2", "extra"}]
        for key in ("load2", "extra"):
            layout["instances"].pop(key)
        layout["ports"].pop("BIAS")
        removed = next(d for d, name in mapping["device_map"].items() if name == "load2")
        mapping["device_map"].pop(removed)
        mapping["terminal_map"].pop(removed)
        mapping["omitted_groups"] = ["load"]
        normalized = preview_circuit(spec, bindings)["plan"]["spec"]
    return {
        "request_schema": "cad.template.adapt-request.v2",
        "template_ref": reference["template_ref"],
        "spec": normalized,
        "bindings": bindings,
        "mapping": mapping,
        "allowed_rules": ["add_boundary_group" if adaptation == "add" else "omit_optional_group"],
        "geometry": geometry,
        "layout": layout,
    }


@pytest.mark.parametrize("adaptation", ["add", "omit"])
def test_native_v3_boundary_peripheral_save_reopen_replay(tmp_path, monkeypatch, adaptation):
    with session(tmp_path, worklib="SicoTest") as (bridge, server, _):
        original_request = bridge.request

        def request(message, timeout):
            if message["method"] == "capture_circuit_template":
                bridge.evaluate_path(
                    message["source_path"], message["output_path"], evaluator="aiEvalExpression"
                )
                return True, {"result_path": message["output_path"]}
            return original_request(message, timeout)

        monkeypatch.setattr(bridge, "request", request)
        server.client.runtime = server.client.dispatcher.runtime
        from circuit_create_support import AI_ROOT

        for name in ("AI_templates.il", "AI_template_capture_geometry.il"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        data = {cell: sample_master(bridge, cell) for cell in ("res", "cap")}
        source = _source_three_devices(data)
        source_oa = tmp_path / "library" / "p2_source" / "schematic" / "sch.oa"
        # Use the existing creator helper contract directly through MCP.
        task = mcp_call(
            server,
            "begin_circuit_task",
            {"task_id": "p2-create-source", "presentation": "background", "choice_confirmed": True},
        )
        prepared = mcp_call(
            server,
            "prepare_circuit_creation",
            {**source, "task_ref": task["task_ref"], "request_id": "p2-source"},
        )
        assert prepared["ok"], prepared
        created = mcp_call(
            server,
            "create_circuit_from_plan",
            {
                "prepare_ref": prepared["prepare_ref"],
                "request_id": "p2-source",
                "geometry_plan_digest": source["geometry_plan_digest"],
            },
        )
        assert created["ok"] and created["saved"] and created["readback_verified"]
        before = hashlib.sha256(source_oa.read_bytes()).hexdigest()
        captured = mcp_call(
            server,
            "extract_circuit_templates",
            {
                "library": "SicoTest",
                "cell": "p2_source",
                "assets": ["schematic"],
                "classifications": {name: dict(kind=kind, attributes={},
                    source_ref="native:analogLib", revision="native:analogLib.v1")
                    for name, kind in [("RF", "resistor"), ("CL", "capacitor"),
                                       ("CL2", "capacitor")]},
                "reuse": dict(mode="core", allow_rename=False,
                    allow_omit_optional_groups=True, allow_add_boundary_group=True,
                    core_instances=["RF", "CL"],
                    optional_groups=[dict(id="load", instances=["CL2"])],
                    boundary_terminals=[dict(endpoint=dict(instance=name, terminal="MINUS"),
                        allowed_device_kinds=["capacitor"], min_additional_connections=0,
                        max_additional_connections=2) for name in ["RF", "CL"]]),
            },
        )
        assert captured["ok"], captured
        assert captured["reuse"]["status"] == "qualified", captured
        reusable = TemplateCatalog(workspace=tmp_path).get(captured["template_ref"])
        from pathlib import Path

        catalog = Path(captured["catalog"])
        package_hash = hashlib.sha256(catalog.read_bytes()).hexdigest()
        queried = mcp_call(server, "query_circuit_templates", {"tier": "private"})
        assert reusable["template_ref"] in {r["template_ref"] for r in queried["templates"]}
        args = _target_request(reusable, source, adaptation)
        request = {k: v for k, v in args.items() if k != "request_schema"}
        request["options"] = {"match_mode": "core", "allowed_tiers": ["private"],
                              "return_alternatives": 0}
        prepared_target = mcp_call(server, "prepare_circuit_template_plan", request)
        assert prepared_target["status"] == "ready_for_creation_prepare", prepared_target
        warm = mcp_call(server, "prepare_circuit_template_plan", request)
        assert warm["status"] == "ready_for_creation_prepare", warm
        # Only the verified ready result enters the plan layer.
        assert warm["diagnostics"]["plan_cache"] == "hit"
        assert warm["plan"] == prepared_target["plan"]
        plan = warm["plan"]
        assert plan["template_use"]["residual_instances"] == (
            ["extra"] if adaptation == "add" else [])
        assert plan["template_use"]["adaptations"][0]["rule_ref"]["id"] == (
            "add_boundary_group" if adaptation == "add" else "omit_optional_group")
        preview = plan["geometry"]
        creation_args = dict(
            spec=plan["spec"], bindings=plan["preview"]["plan"]["bindings"],
            template_use=plan["template_use"], preview_digest=plan["preview"]["preview_digest"],
            geometry=plan["geometry_input"], layout=preview["plan"]["layout"],
            geometry_plan_digest=preview["geometry_plan_digest"],
        )
        task = mcp_call(
            server,
            "begin_circuit_task",
            {"task_id": "p2-target", "presentation": "background", "choice_confirmed": True},
        )
        pre = mcp_call(
            server,
            "prepare_circuit_creation",
            {**creation_args, "task_ref": task["task_ref"], "request_id": "p2-target"},
        )
        assert pre["ok"], pre
        done = mcp_call(
            server,
            "create_circuit_from_plan",
            {
                "prepare_ref": pre["prepare_ref"],
                "request_id": "p2-target",
                "geometry_plan_digest": preview["geometry_plan_digest"],
            },
        )
        assert (
            done["ok"] and done["saved"] and done["readback_verified"] and done["check"] == [0, 0]
        )
        inspected = mcp_call(
            server, "inspect_created_circuit", {"circuit_ref": done["circuit_ref"]}
        )
        assert inspected["ok"] and inspected["readback_verified"]
        replay = mcp_call(
            server,
            "create_circuit_from_plan",
            {
                "prepare_ref": pre["prepare_ref"],
                "request_id": "p2-target",
                "geometry_plan_digest": preview["geometry_plan_digest"],
            },
        )
        assert replay == done
        text_layout = done["text_layout"]
        assert done["svg_preview"]["status"] == "ready"
        assert done["svg_preview"]["artifact"]["path"].endswith(".svg")
        assert not text_layout["unresolved"]
        assert text_layout["dynamic_text_verified"] is False
        assert all("\n" in note[0] and note[3] and note[4] for note in text_layout["notes"])
        assert hashlib.sha256(source_oa.read_bytes()).hexdigest() == before
        assert hashlib.sha256(catalog.read_bytes()).hexdigest() == package_hash
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                {
                    "template_ref": reusable["template_ref"],
                    "prepared": prepared_target,
                    "preview": preview,
                    "created": done,
                    "inspected": inspected,
                    "package_sha256": package_hash,
                    "query": queried,
                    "search_cache": warm["diagnostics"]["cache"],
                    "extraction": captured,
                    "request": request,
                    "adaptation": adaptation,
                },
                indent=2,
            )
        )
