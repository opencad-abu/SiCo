"""Fixed real-training sources: independent labels, matching, adaptation and OA output."""

import json
import os
from pathlib import Path

import pytest
from template_pdk_support import bind_device, create, pdk_session, readback
from template_real_labels import (
    BOUNDARY,
    CASES,
    LABEL_SHA256,
    MASTERS,
    classification,
    label_digest,
)
from template_real_replay import replay, target_request
from template_real_review_support import (
    read_inventory,
    review_source,
    sha256,
    source_info,
    write_json,
)
from template_visual_support import export_schematic
from test_circuit_rc_gui import mcp_call

from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_reuse_build import publish_reusable
from cadai.template_schema import TemplateError

pytestmark = pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1",
                                reason="licensed fixed real-source review")


def test_real_training_labels_to_template_creation(tmp_path, monkeypatch):
    assert label_digest() == LABEL_SHA256
    sources = source_info()
    pdk = Path(sources["gpdk045"])
    model_path = pdk.parent / "models/spectre/gpdk045_mos.scs"
    if not model_path.is_file():
        pytest.skip("authorized training source/model unavailable")
    source_paths = [Path(sources[c["library"]]) / c["cell"] / "schematic/sch.oa"
                    for c in [*CASES, BOUNDARY]]
    if not all(p.is_file() for p in source_paths):
        pytest.skip("authorized real source OA unavailable")
    before = {str(p): sha256(p) for p in source_paths}
    with pdk_session(tmp_path, pdk) as (bridge, server):
        with (tmp_path / "cds.lib").open("a") as stream:
            for lib in sorted({c["library"] for c in [*CASES, BOUNDARY]}):
                stream.write(f"DEFINE {lib} {sources[lib]}\n")
        assert bridge.skill("ddUpdateLibList()") == "t"
        records = [review_source(bridge, tmp_path / case["id"], case,
                                Path(sources[case["library"]]), model_path) for case in CASES]
        # This real PDK resistor has a schematic. Do not relabel it to force eligibility.
        boundary = tmp_path / "boundary.jsonl"
        assert sha256(source_paths[-1]) == BOUNDARY["oa_sha256"]
        bridge.skill('aiTplCapture("amsPLL" "loop_filter" "schematic" "symbol" "layout" '
                     + json.dumps(str(boundary)) + ' list("schematic") "cad.template.capture.v2")')
        captured = read_capture(boundary)
        negative = make_template(captured)
        resistor = next(d for d in negative["topology"]["devices"] if d["source_name"] == "R1")
        assert resistor["role"] == "hierarchy"
        with pytest.raises(TemplateError, match="hierarchy"):
            make_template(captured, classifications={"R1": dict(kind="resistor", attributes={},
                source_ref="review:real-resistor", revision="v1")})
        write_json(tmp_path / "boundary-record.json", negative)
        results = {case["id"]: dict(replay=replay(case, record, records, tmp_path / case["id"]))
                   for case, record in zip(CASES, records)}
        write_json(tmp_path / "replay.json", results)
        package = tmp_path / "review-package"
        catalog = package / "catalogs/review.sqlite3"
        publish_reusable(records, catalog)
        package_hash = sha256(catalog)
        monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(package))
        bound = {cell: bind_device(server, cell, ["l", "fingers", "w", "m"],
                                  dict(G="left", B="right",
                                       D="up" if kind == "nmos" else "down",
                                       S="down" if kind == "nmos" else "up"),
                                  classification=classification(cell))
                 for cell, (_, kind) in MASTERS.items()}
        write_json(tmp_path / "bindings.json", bound)
        queried = mcp_call(server, "query_circuit_templates", dict(tier="project"))
        assert {r["template_ref"] for r in records} == {
            r["template_ref"] for r in queried["templates"]}
        for case, record in zip(CASES, records):
            directory = tmp_path / case["id"]
            request, placement = target_request(case, record, bound)
            write_json(directory / "request.json", request)
            prepared = mcp_call(server, "prepare_circuit_template_plan", request)
            write_json(directory / "prepare.json", prepared)
            assert prepared["status"] == "ready_for_creation_prepare", prepared
            plan = prepared["plan"]
            preview = plan["geometry"]
            assert len(preview["plan"]["ports"]) == len(case["ports"])
            assert all(p["placement_source"] == "template" for p in preview["plan"]["ports"])
            port_wires = [w for w in preview["plan"]["wires"] if "port" in w["endpoint"]]
            assert len(port_wires) == len(case["ports"])
            assert all(len(w["points"]) == 2 and w.get("label") for w in port_wires)
            args = dict(spec=plan["spec"], bindings=plan["preview"]["plan"]["bindings"],
                        template_use=plan["template_use"],
                        preview_digest=plan["preview"]["preview_digest"],
                        geometry=plan["geometry_input"], layout=preview["plan"]["layout"],
                        geometry_plan_digest=preview["geometry_plan_digest"])
            result = create(server, args, case["id"])
            result["physical_readback"] = readback(bridge, plan["spec"],
                                                   preview["plan"]["wires"])
            result["visual"] = export_schematic(bridge, directory, plan["spec"]["target"], "target")
            inventory = read_inventory(bridge, dict(library="genericWork",
                                                    cell=plan["spec"]["target"]["cell"]))
            actual = {row[0]: dict(row[4]) for row in inventory[0] if row[1] != "basic"}
            assert actual == {i["name"]: i["connections"] for i in plan["spec"]["instances"]}
            write_json(directory / "target-inventory.json", inventory)
            results[case["id"]].update(create=result, placement=placement)
        assert before == {str(p): sha256(p) for p in source_paths}
        assert sha256(catalog) == package_hash
        write_json(tmp_path / "acceptance.json", dict(
            labels_digest=label_digest(), review="codex_review; human_expert_pending",
            source_oa=before, source_unchanged=True, package_sha256=package_hash,
            boundary="hierarchy_rejected", cases=results))
