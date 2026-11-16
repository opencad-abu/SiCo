"""Actual discovery MCP -> project adapter -> native foreground creation acceptance."""

import json
import os
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT
from template_smic28_support import migration_session
from test_circuit_rc_gui import mcp_call

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest
from cadai.pdk_bridge import PdkBridge
from cadai.pdk_session import PdkSession


def selected(server, library, cell, key, escapes, writable, extension=None):
    search = mcp_call(server, "search_pdk_devices", dict(library=library, cell=cell))
    assert len(search["items"]) == 1, search
    query = dict(
        snapshot_ref=search["context"]["snapshot_ref"], device_ref=search["items"][0]["device_ref"]
    )
    pages, figures = [], []
    while True:
        page = mcp_call(server, "get_pdk_device", query)
        pages.append(page)
        figures.extend(page["device"]["geometry"]["items"])
        if page["page"]["next_cursor"] is None:
            break
        query["cursor"] = page["page"]["next_cursor"]
    args = dict(
        snapshot_ref=query["snapshot_ref"],
        device_ref=query["device_ref"],
        revision=pages[0]["device"]["revision"],
        master_id=key,
        kind="device",
        project_ref="project:formal-pdk-acceptance",
        project_policy_ref="test:explicit-choices-v1",
        writable_parameters=[
            dict(name=n, policy=p, evidence="Explicit acceptance project setting")
            for n, p in writable.items()
        ],
        pin_escapes=[
            dict(figure_ref=f["figure_ref"], escape=escapes[f["terminal"]]) for f in figures
        ],
    )
    if extension:
        args["extension_ref"] = extension
    record = mcp_call(server, "bind_pdk_device", args)
    assert record.get("ok") and "master" in record, record
    return record, pages


def inputs(records, cell, connections, parameters, positions, ports):
    bindings = dict(
        schema="cad.circuit.bindings.v1",
        project_ref=records[0]["project_ref"],
        snapshot_ref="project:chosen-devices",
        source_ref="project:formal-pdk-adapters",
        evidence_kind="session_capture",
        masters=[r["master"] for r in records],
    )
    spec = dict(
        schema="cad.circuit.spec.v1",
        project_ref=bindings["project_ref"],
        binding_snapshot=bindings["snapshot_ref"],
        kind="circuit",
        target=dict(library="genericWork", cell=cell, view="schematic"),
        instances=[
            dict(
                id=r["master"]["id"],
                name=r["master"]["id"],
                master=r["master"]["id"],
                role="device",
                parameters=parameters[r["master"]["id"]],
                connections=connections[r["master"]["id"]],
                unconnected={},
            )
            for r in records
        ],
        nets=[dict(name=n, scope="local") for n in ports],
        ports=[dict(name=n, direction=d[0], net=n) for n, d in ports.items()],
    )
    geometry = dict(
        schema="cad.circuit.geometry.v1",
        project_ref=bindings["project_ref"],
        binding_snapshot=bindings["snapshot_ref"],
        binding_digest=digest(bindings),
        source_ref=bindings["source_ref"],
        evidence_kind="session_capture",
        coordinate_system="master_local_schematic_uu",
        instances=[
            dict(r["geometry"], parameters_digest=digest(parameters[r["master"]["id"]]))
            for r in records
        ],
    )
    layout = dict(
        instances={k: dict(xy=xy, orientation="R0") for k, xy in positions.items()},
        ports={
            n: dict(xy=d[1], escape="left" if d[0] == "output" else "right")
            for n, d in ports.items()
        },
        anchor_choices={},
        grid=0.0625,
        stub_length=0.5,
        clearance=0,
        routing={"mode": "stub", "decision": {"source": "user", "chosen": "stub"}},
    )
    args = dict(
        spec=spec,
        bindings=bindings,
        geometry=geometry,
        layout=layout,
        preview_digest=preview_circuit(spec, bindings)["preview_digest"],
    )
    plan = preview_geometry(**args)
    assert plan["plan"]["geometry_checks_passed"], plan
    return dict(args, geometry_plan_digest=plan["geometry_plan_digest"])


def test_formal_pdk_discovery_to_foreground_creation(tmp_path):
    if os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("opt-in licensed foreground test")
    with migration_session(tmp_path) as (bridge, server, proc):
        # This disposable native fixture validates live capture/creation, without
        # a project technology catalog or entry-window selection workflow.
        server._pdk_session = PdkSession(bridge=PdkBridge(server.client))
        for module in ("AI_pdk_core.il", "AI_pdk_init.il", "AI_pdk_cdf.il", "AI_pdk_db.il", "AI_pdk_capture.il"):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
        bridge.skill('ddCreateLib("genericWork" ' + json.dumps(str(tmp_path / "library")) + ")")
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="formal_pdk", presentation="foreground", choice_confirmed=True),
        )
        records, proofs, requests = [], [], []

        def create(data, request):
            # Reproduce the long TB workflow: unrelated discovery evicts source
            # snapshots between preview/prepare and again before native execution.
            def churn():
                for _ in range(9):
                    mcp_call(server, "search_pdk_devices", dict(library="analogLib", cell="cap"))

            churn()
            pre = mcp_call(
                server,
                "prepare_circuit_creation",
                dict(data, task_ref=task["task_ref"], request_id=request),
            )
            assert pre["ok"], pre
            execute = dict(
                prepare_ref=pre["prepare_ref"],
                geometry_plan_digest=data["geometry_plan_digest"],
                request_id=request,
            )
            # No adapter marker: native execution must stop without creating a view.
            guard = json.loads(
                bridge.skill(
                    "aiCreateExecute("
                    + " ".join(
                        json.dumps(execute[k])
                        for k in ("prepare_ref", "geometry_plan_digest", "request_id")
                    )
                    + ")"
                )
            )
            assert guard["code"] == "pdk_validation_required", guard
            churn()
            done = mcp_call(server, "create_circuit_from_plan", execute)
            assert done["ok"] and done["readback_verified"] and done["check"] == [0, 0], done
            assert mcp_call(
                server, "inspect_created_circuit", {"circuit_ref": done["circuit_ref"]}
            )["ok"]
            output_pins = json.loads(
                bridge.skill(
                    'let((opCv opRows) opCv=dbOpenCellViewByType("genericWork" '
                    + json.dumps(data["spec"]["target"]["cell"])
                    + ' "schematic" "" "r") '
                    "opRows=foreach(mapcar opInst setof(opI opCv~>instances "
                    'opI~>libName=="basic" opI~>cellName=="opin") '
                    "list(opInst~>orient opInst~>xy)) dbClose(opCv) aiCircuitJson(opRows))"
                )
            )
            assert output_pins and all(p[0] == "R0" for p in output_pins), output_pins
            proofs.append(dict(prepare=pre, guard=guard, created=done, output_pins=output_pins))
            requests.append(execute)

        resistor, pages = selected(
            server,
            "analogLib",
            "res",
            "R",
            {"PLUS": "up", "MINUS": "down"},
            {"r": "absent_condition"},
        )
        records.append(dict(binding=resistor, pages=pages))
        data = inputs(
            [resistor],
            "pdk_resistor",
            {"R": {"PLUS": "IN", "MINUS": "OUT"}},
            {"R": {"r": "1k"}},
            {"R": [0, 0]},
            {"IN": ("input", [-6, 0]), "OUT": ("output", [6, 0])},
        )
        create(data, "formal_res")
        pdk = os.environ.get("SICO_AI_SMIC28_PDK")
        if pdk:
            with (tmp_path / "cds.lib").open("a") as stream:
                stream.write("DEFINE smic28hkmg " + str(Path(pdk) / "smic28hkmg") + "\n")
            bridge.skill("ddUpdateLibList()")
            # Load the explicitly registered test-project callback policy, independent of discovery.
            bridge.skill(
                "load("
                + json.dumps(str(AI_ROOT / "python/tests/smic28_project_extension.il"))
                + ")"
            )
            mos = []
            for key, cell in (("P", "p09_ckt"), ("N", "n09_ckt")):
                bound, pages = selected(
                    server,
                    "smic28hkmg",
                    cell,
                    key,
                    {
                        "G": "left",
                        "B": "right",
                        "D": "down" if key == "P" else "up",
                        "S": "up" if key == "P" else "down",
                    },
                    {n: "project_extension" for n in ("w", "l", "m", "fingers")},
                    "test:smic28:" + cell + ":v1",
                )
                records.append(dict(binding=bound, pages=pages))
                mos.append(bound)
            bridge.skill('aiSmicTestInstall(ddGetObjReadPath(ddGetObj("smic28hkmg")))')
            inv = inputs(
                mos,
                "pdk_inverter",
                {
                    "P": dict(G="A", D="Y", S="VDD", B="VDD"),
                    "N": dict(G="A", D="Y", S="VSS", B="VSS"),
                },
                {k: dict(w="1u", l="100n", m="1", fingers="1") for k in ("P", "N")},
                {"P": [0, 4], "N": [0, -4]},
                {
                    "VDD": ("inputOutput", [-8, 6]),
                    "VSS": ("inputOutput", [-8, 5]),
                    "A": ("input", [-8, 0]),
                    "Y": ("output", [8, 0]),
                },
            )
            create(inv, "formal_inv")
        # Session replacement invalidates new work; completed request replay remains safe.
        bridge.skill('aiPdkSessionRef="session:acceptance-invalidated"')
        replay = mcp_call(server, "create_circuit_from_plan", requests[0])
        diagnostics = {"output", "output_capture", "output_truncated"}
        assert {k: v for k, v in replay.items() if k not in diagnostics} == {
            k: v for k, v in proofs[0]["created"].items() if k not in diagnostics
        }
        stale = server.handle(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(
                    name="prepare_circuit_creation",
                    arguments=dict(data, task_ref=task["task_ref"], request_id="stale"),
                ),
            )
        )
        assert "error" in stale and "changed" in stale["error"]["message"], stale
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(pid=proc.pid, records=records, proofs=proofs, replay=replay, stale=stale),
                indent=2,
            )
        )
