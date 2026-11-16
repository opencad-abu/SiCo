"""Saved gpdk090 logic gates through the public template and native creation flow."""

import json
import os
from copy import deepcopy
from functools import partial
from pathlib import Path

import pytest
from template_gate_labels import (
    CASES,
    LABEL_SHA256,
    LIBRARY,
    MASTERS,
    PARAMETERS,
    classification,
    label_digest,
)
from template_gate_source import review_source, sources, verify_models
from template_pdk_support import bind_device, create, pdk_session, readback
from template_real_replay import labeled_target, replay, target_request
from template_real_review_support import read_inventory, sha256, write_json
from template_visual_support import export_schematic
from test_circuit_rc_gui import mcp_call

pytestmark = pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1",
                                reason="licensed saved Custom IC gate review")


def test_saved_local_supply_gates(tmp_path):
    assert label_digest() == LABEL_SHA256
    target_topology = partial(labeled_target, masters=MASTERS)
    paths = sources()
    pdk = Path(paths[LIBRARY])
    model = pdk.parents[1] / "models/spectre/gpdk090_mos.scs"
    source_paths = [Path(paths[c["library"]]) / c["cell"] / "schematic/sch.oa" for c in CASES]
    if not model.is_file() or not all(p.is_file() for p in source_paths):
        pytest.skip("authorized saved source/model unavailable")
    before = {str(p): sha256(p) for p in source_paths}
    with pdk_session(tmp_path, pdk, library=LIBRARY) as (bridge, server):
        with (tmp_path / "cds.lib").open("a") as stream:
            stream.write("DEFINE ether " + paths["ether"] + "\n")
        assert bridge.skill("ddUpdateLibList()") == "t"
        write_json(tmp_path / "cdf-evidence.json", verify_models(bridge, model))
        records = [review_source(bridge, server, tmp_path / c["id"], c, Path(paths[c["library"]]))
                   for c in CASES]
        replayed = {c["id"]: replay(c, r, records, tmp_path / c["id"],
                                   target_factory=target_topology) for c, r in zip(CASES, records)}
        write_json(tmp_path / "replay.json", replayed)
        catalogs = [Path(json.loads((tmp_path / c["id"] / "extract.json").read_text())["catalog"])
                    for c in CASES]
        package_hashes = {str(p): sha256(p) for p in catalogs}
        bound = {cell: bind_device(server, cell, ["l", "fingers", "w", "m"],
                     dict(G="left", B="right", D="up" if kind == "nmos" else "down",
                          S="down" if kind == "nmos" else "up"),
                     classification=classification(cell), library=LIBRARY)
                 for cell, (_, kind) in MASTERS.items()}
        write_json(tmp_path / "bindings.json", bound)
        query = mcp_call(server, "query_circuit_templates", dict(tier="private"))
        assert {r["template_ref"] for r in query["templates"]} == {
            r["template_ref"] for r in records}
        for case, record in zip(CASES, records):
            view = mcp_call(server, "get_circuit_template", dict(
                template_ref=record["template_ref"], section="relations", entity="groups"))
            assert view["metadata"]["schema"] == "cad.template.relations.v2"
            assert all(r["data"]["evidence"] == "source_geometry" for r in view["records"])
            write_json(tmp_path / case["id"] / "relation-groups.json", view)
        results = {}
        for case, record in zip(CASES, records):
            directory = tmp_path / case["id"]
            request, placement = target_request(case, record, bound,
                target_factory=target_topology, parameters=PARAMETERS)
            request["options"]["allowed_tiers"] = ["private"]
            write_json(directory / "request.json", request)
            # Preserve the historical replay oracle while exercising the compact public flow.
            compact = {k: deepcopy(v) for k, v in request.items()
                       if k not in {"bindings", "geometry", "layout"}}
            compact["spec"].pop("project_ref")
            compact["spec"].pop("binding_snapshot")
            compact["binding_refs"] = [bound[m["id"]]["binding_ref"]
                                       for m in request["bindings"]["masters"]]
            compact["placement"] = dict(spacing="loose", clearance=0.125, stub_length=0.75,
                                        routing=dict(mode="stub"))
            write_json(directory / "integrated-request.json", compact)
            prepared = mcp_call(server, "prepare_circuit_template_plan", compact)
            write_json(directory / "prepare.json", prepared)
            assert prepared["status"] == "ready_for_creation_prepare", prepared
            assert "placement" in prepared["diagnostics"]
            assert len(prepared["diagnostics"]["placement"]["gaps"]) <= 8
            plan = prepared["plan"]
            assert plan["geometry"]["plan"]["layout"] == request["layout"]
            explicit = server.pdk_bindings().resolve_request(compact, include_geometry=True)
            explicit.pop("placement")
            explicit["layout"] = request["layout"]
            comparison = mcp_call(server, "prepare_circuit_template_plan", explicit)
            assert comparison["plan"] == plan
            write_json(directory / "workflow-comparison.json", {
                "layout_equal": True, "prepared_plan_equal": True,
                "explicit_request_bytes": len(json.dumps(request, sort_keys=True).encode()),
                "integrated_request_bytes": len(json.dumps(compact, sort_keys=True).encode()),
                "retained_geometry_instances": len(plan["geometry_input"]["instances"]),
            })
            preview = plan["geometry"]
            ports = preview["plan"]["ports"]
            wires = [w for w in preview["plan"]["wires"] if "port" in w["endpoint"]]
            assert len(ports) == len(wires) == len(case["ports"])
            assert all(p["placement_source"] == "template" for p in ports)
            assert all(len(w["points"]) == 2 and w.get("label") for w in wires)
            args = dict(spec=plan["spec"], bindings=plan["preview"]["plan"]["bindings"],
                        template_use=plan["template_use"],
                        preview_digest=plan["preview"]["preview_digest"],
                        geometry=plan["geometry_input"], layout=preview["plan"]["layout"],
                        geometry_plan_digest=preview["geometry_plan_digest"])
            result = create(server, args, case["id"])
            result["physical_readback"] = readback(bridge, plan["spec"], preview["plan"]["wires"])
            inventory = read_inventory(bridge, plan["spec"]["target"])
            assert {r[0]: dict(r[4]) for r in inventory[0] if r[1] != "basic"} == {
                i["name"]: i["connections"] for i in plan["spec"]["instances"]}
            assert {name: (direction, net) for name, direction, net in inventory[1]} == {
                p["name"]: (p["direction"], p["net"]) for p in plan["spec"]["ports"]}
            assert {n[0] for n in inventory[2]} == {n["name"] for n in plan["spec"]["nets"]}
            assert all(not n[2] for n in inventory[2])
            write_json(directory / "target-inventory.json", inventory)
            result["visual"] = export_schematic(bridge, directory, plan["spec"]["target"], "target")
            results[case["id"]] = dict(create=result, placement=placement)
        assert before == {str(p): sha256(p) for p in source_paths}
        assert {str(p): sha256(p) for p in catalogs} == package_hashes
        write_json(tmp_path / "acceptance.json", dict(labels_digest=label_digest(),
            review="codex_review; human_expert_pending; no_simulation_qualification",
            source_oa=before, source_unchanged=True, package_sha256=package_hashes,
            replay=replayed, cases=results))
