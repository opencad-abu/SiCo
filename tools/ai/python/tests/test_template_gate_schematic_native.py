"""Native explicit INV/NAND fixtures plus inherited-source refusal and visual evidence."""

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
from test_circuit_rc_gui import mcp_call
from test_template_pdk_native import PDK

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest
from cadai.template_build import publish
from cadai.template_catalog import TemplateCatalog
from cadai.template_placement_layout import plan_layout
from cadai.template_relations import relations
from cadai.template_reuse_build import make_reusable, publish_reusable
from cadai.template_rules import rule_ref
from cadai.template_schema import TemplateError

pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed gate schematic acceptance"
)
CATALOG = Path(__file__).resolve().parents[2] / "reference/circuit_templates"
KINDS = {"nmos1v": "nmos", "pmos1v": "pmos"}  # Explicit test PDK choices only.


def classification(cell):
    return dict(kind=KINDS[cell], attributes={}, source_ref="test:gpdk045-mos",
                revision="test:gpdk045-mos.v1")


def reusable(record):
    devices = record["topology"]["devices"]
    contract = dict(core_devices=[d["id"] for d in devices],
                    core_connections=[dict(device=d["id"], terminal=p["name"])
                                      for d in devices for p in d["pins"]],
                    boundary_terminals=[], optional_groups=[],
                    allowed_rule_refs=[rule_ref("rename")], placement_constraints=[],
                    routing_reference=dict(asset="schematic", status="unavailable",
                                           anchor_ids=[], segment_ids=[]))
    return make_reusable(record, contract,
                         {d["id"]: classification(d["master"]["cell"]) for d in devices})


def request_for(record, bound):
    first = next(iter(bound.values()))
    bindings = dict(schema="cad.circuit.bindings.v1", project_ref=first["project_ref"],
                    snapshot_ref=first["context"]["snapshot_ref"],
                    source_ref=first["context"]["session_ref"], evidence_kind="session_capture",
                    masters=[copy.deepcopy(b["master"]) for b in bound.values()])
    top = record["topology"]
    nets = {n["id"]: n["source_name"] for n in top["nets"]}
    instances, geometry = [], []
    for d in top["devices"]:
        params = dict(l="100n", w="2u" if d["kind"] == "pmos" else "1u", fingers="1", m="1")
        name, master = d["source_name"], d["master"]["cell"]
        instances.append(dict(id=name, name=name, master=master, role="device",
                              parameters=params, connections={p["name"]: nets[p["net"]]
                                                              for p in d["pins"]}, unconnected={}))
        geometry.append(dict(copy.deepcopy(bound[master]["geometry"]), instance=name,
                             parameters_digest=digest(params)))
    spec = dict(schema="cad.circuit.spec.v1", kind="circuit", project_ref=bindings["project_ref"],
                binding_snapshot=bindings["snapshot_ref"],
                target=dict(library="genericWork", cell=record["source"]["cell"] + "_target",
                            view="schematic"), instances=instances,
                nets=[dict(name=n["source_name"], scope="global" if n["is_global"] else "local")
                      for n in top["nets"]],
                ports=[dict(name=nets[p["net"]], direction=p["direction"], net=nets[p["net"]])
                       for p in top["ports"]], notes=[])
    geo = dict(schema="cad.circuit.geometry.v1", project_ref=bindings["project_ref"],
               binding_snapshot=bindings["snapshot_ref"], binding_digest=digest(bindings),
               source_ref=bindings["source_ref"], evidence_kind="session_capture",
               coordinate_system="master_local_schematic_uu", instances=geometry)
    device_map = {d["id"]: d["source_name"] for d in top["devices"]}
    port_map = {p["name"]: nets[p["net"]] for p in top["ports"]}
    layout, report = plan_layout(spec, bindings, geo, relations(record), device_map,
                                dict(spacing="loose", clearance=0.125, stub_length=0.75,
                                     routing=dict(mode="end_to_end", engine="planner")),
                                port_map=port_map)
    return dict(spec=spec, bindings=bindings, geometry=geo, layout=layout,
                template_ref=record["template_ref"],
                mapping=dict(device_map=device_map, net_map=nets,
                             terminal_map={d["id"]: {p["name"]: p["name"] for p in d["pins"]}
                                           for d in top["devices"]},
                             port_map={p["name"]: nets[p["net"]] for p in top["ports"]},
                             omitted_groups=[]), allowed_rule_refs=[rule_ref("rename")],
                options=dict(match_mode="core", allowed_tiers=["project"],
                             return_alternatives=0)), report


def local_supply_spec(spec, layout):
    """Explicit standalone test choice; does not resolve the source's inheritance."""
    names = {n["name"]: n["name"].rstrip("!") for n in spec["nets"]}
    for net in spec["nets"]:
        net.update(name=names[net["name"]], scope="local")
    for inst in spec["instances"]:
        inst["connections"] = {p: names[n] for p, n in inst["connections"].items()}
    for port in spec["ports"]:
        port.update(name=names[port["name"]], net=names[port["net"]])
    layout["ports"] = {names[k]: v for k, v in layout["ports"].items()}


def create_with_diagnostics(bridge, server, args, path):
    try:
        return create(server, args, args["spec"]["target"]["cell"])
    except AssertionError:
        target = args["spec"]["target"]
        identity = " ".join(json.dumps(target[k]) for k in ("library", "cell", "view"))
        markers = bridge.skill(
            "let((gateCv) gateCv=dbFindOpenCellViewByName(" + identity + ") "
            "aiCircuitJson(foreach(mapcar gateMarker gateCv~>markers "
            "list(gateMarker~>bBox gateMarker~>prop~>name gateMarker~>prop~>value))))")
        save_json(path / (target["cell"] + "-markers.json"), json.loads(markers))
        raise


def test_inv_nand_public_template_creation_and_visual_evidence(tmp_path, monkeypatch):
    if not Path(PDK).is_dir():
        pytest.skip("installed test PDK required")
    manifest = json.loads((CATALOG / "custom_gates/SOURCES.json").read_text())
    refs = [t["template_ref"] for t in manifest["templates"] if t["family"] in {"INV", "NAND2"}]
    legacy = [TemplateCatalog(CATALOG).get(ref) for ref in refs]
    source = Path(os.environ.get(
        "SICO_AI_GATE_TEMPLATE_LIBRARY", legacy[0]["source"]["library_path"]))
    source_files = [source / r["source"]["cell"] / "schematic/sch.oa" for r in legacy]
    if not all(path.is_file() for path in source_files):
        pytest.skip("installed gate reference library required")
    source_before = {str(p): oa_hash(p) for p in source_files}
    package = tmp_path / "template-package"
    package.mkdir()
    members = package / "catalogs"
    members.mkdir()
    catalog = members / "explicit-gates.sqlite3"
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(package))
    with pdk_session(tmp_path, Path(PDK)) as (bridge, server):
        with (tmp_path / "cds.lib").open("a") as stream:
            stream.write("DEFINE gsclib045 " + str(source) + "\n")
        bridge.skill("ddUpdateLibList()")
        bound = {cell: bind_device(server, cell, ["l", "fingers", "w", "m"],
                                  dict(G="left", B="right", D="up" if cell == "nmos1v" else "down",
                                       S="down" if cell == "nmos1v" else "up"),
                                  classification=classification(cell))
                 for cell in ("nmos1v", "pmos1v")}
        save_json(tmp_path / "bindings.json", bound)
        # Retain the real library's inherited-supply gap as a negative case.
        captured = [capture(server, tmp_path, r["source"]["cell"], library="gsclib045")
                    for r in legacy]
        publish(captured, members / "inherited-gates.sqlite3")
        for record in captured:
            with pytest.raises(TemplateError, match="inherited_connections_not_resolved"):
                reusable(record)
            request, _ = request_for(record, bound)
            request["options"]["match_mode"] = "exact"
            request.pop("mapping")
            request.pop("allowed_rule_refs")
            rejected = mcp_call(server, "prepare_circuit_template_plan", request)
            assert rejected["status"] == "inconclusive" and rejected["plan"] is None
            save_json(tmp_path / (record["source"]["cell"] + "-inherited-rejected.json"), rejected)

        # Create explicit standalone reference circuits using the known gate
        # topology. They have no inherited-connection equivalence claim.
        records = []
        for record in captured:
            request, _ = request_for(record, bound)
            args = {k: request[k] for k in ("spec", "bindings", "geometry", "layout")}
            args["spec"]["target"]["cell"] = record["source"]["cell"] + "_explicit"
            local_supply_spec(args["spec"], args["layout"])
            args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])[
                "preview_digest"]
            preview = preview_geometry(**args)
            assert preview["plan"]["geometry_checks_passed"], preview["plan"]["issues"]
            args["geometry_plan_digest"] = preview["geometry_plan_digest"]
            seeded = create_with_diagnostics(bridge, server, args, tmp_path)
            save_json(tmp_path / (record["source"]["cell"] + "-explicit-source.json"), seeded)
            records.append(reusable(capture(server, tmp_path, args["spec"]["target"]["cell"])))
        publish_reusable(records, catalog)
        refs = [r["template_ref"] for r in records]
        before = oa_hash(catalog)
        query = mcp_call(server, "query_circuit_templates", dict(tier="project"))
        assert set(refs) <= {r["template_ref"] for r in query["templates"]}
        for record in records:
            request, placement = request_for(record, bound)
            save_json(tmp_path / (record["source"]["cell"] + "-request.json"), request)
            prepared = mcp_call(server, "prepare_circuit_template_plan", request)
            save_json(tmp_path / (record["source"]["cell"] + "-prepare.json"), prepared)
            assert prepared["status"] == "ready_for_creation_prepare", prepared
            plan = prepared["plan"]
            preview = plan["geometry"]
            ports = preview["plan"]["ports"]
            assert all(p["placement_source"] == "template" for p in ports)
            port_stubs = [w for w in preview["plan"]["wires"] if "port" in w["endpoint"]]
            assert {w["endpoint"]["port"] for w in port_stubs} == {p["name"] for p in ports}
            assert all(len(w["points"]) == 2 and w.get("label") and not w.get("kind")
                       for w in port_stubs)
            assert preview["plan"]["routing"]["port_stub_terminals"] == len(ports)
            args = dict(spec=plan["spec"], bindings=plan["preview"]["plan"]["bindings"],
                        template_use=plan["template_use"],
                        preview_digest=plan["preview"]["preview_digest"],
                        geometry=plan["geometry_input"], layout=preview["plan"]["layout"],
                        geometry_plan_digest=preview["geometry_plan_digest"])
            done = create_with_diagnostics(bridge, server, args, tmp_path)
            physical = readback(bridge, plan["spec"], preview["plan"]["wires"])
            screenshot = export_schematic(bridge, tmp_path, plan["spec"]["target"],
                                          record["source"]["cell"] + "-target")
            assert done["created"]["svg_preview"]["status"] == "ready"
            assert oa_hash(catalog) == before
            assert {str(p): oa_hash(p) for p in source_files} == source_before
            save_json(tmp_path / (record["source"]["cell"] + "-acceptance.json"),
                      dict(query=query, prepared=prepared, placement=placement, **done,
                           physical_readback=physical, screenshot=screenshot,
                           package_sha256=before, source_sha256=source_before))
