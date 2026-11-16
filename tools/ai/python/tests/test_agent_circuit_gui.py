"""Real relay -> shared creation/Symbol/Config/Maestro, with exact-history recovery."""

import copy
import json
import os
import shlex
import shutil
import sys
import time

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import AI_ROOT, sample_master
from simulation_recipe_fixture import recipe
from template_smic28_support import migration_session
from template_symbol_fixture import scratch_schematic
from test_draw_symbol import gate_record
from test_pdk_binding_gui import inputs, selected
from test_simulation_recipe_gui import load_backend, source_fixture

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import ToolCall
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker
from sico.transport.circuit import encode_value

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed foreground circuit adapter"
)


def test_shared_foreground_write_lifecycle(tmp_path):
    with migration_session(tmp_path) as (bridge, server, _):
        bridge.skill("load(" + json.dumps(str(AI_ROOT.parent / "sico/skill++/SICO.ils")) + ")")
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill/AI_templates.il")) + ")")
        load_backend(bridge, tmp_path)
        source_fixture(bridge, server)  # Test source only, outside production business tools.
        (tmp_path / "simulation").mkdir(exist_ok=True)
        masters = {cell: sample_master(bridge, cell) for cell in ("res", "cap")}
        with SessionJournal(tmp_path, "circuit") as journal, ContextBroker(25) as broker:
            configuration = journal.directory / "relay.json"
            configuration.write_text(
                json.dumps(dict(host=broker.address[0], port=broker.address[1], token=broker.token))
            )
            configuration.chmod(0o600)
            command = shlex.join(
                [
                    sys.executable,
                    "-s",
                    str(AI_ROOT.parent / "sico/python/sico-entry"),
                    "relay",
                    "--connection-file",
                    str(configuration),
                ]
            )
            bridge.skill(
                'progn(copilotEntry=deOpenCellView("genericWork" "entry" "schematic" '
                '"schematic" nil "w") copilotCid=sicoConnect('
                + json.dumps(command)
                + ' "write-instance" "write-generation" copilotEntry))'
            )
            evidence = []
            try:
                context = broker.context("write-instance", timeout=10)
                registry = context_tools(broker, journal)

                def call(name, args):
                    result = registry.execute(ToolCall("call", name, args), context)
                    evidence.append(dict(tool=name, result=result.record()))
                    assert result.status == "ok", result
                    return result.data

                def preflight(cell, view, oa_view_type, intent="create", run=False):
                    args = dict(
                        presentation="foreground",
                        targets=[
                            dict(
                                library="genericWork",
                                cell=cell,
                                view=view,
                                oa_view_type=oa_view_type,
                                intent=intent,
                            )
                        ],
                        libraries=[
                            dict(library="genericWork", expected_path=str(tmp_path / "library"))
                        ],
                        directories=[
                            dict(
                                path=str(tmp_path),
                                purpose="project",
                                min_free_bytes=1024,
                                min_free_inodes=1,
                            )
                        ],
                        model_files=[],
                    )
                    if run:
                        args["directories"].append(
                            dict(
                                path=str(tmp_path / "simulation"),
                                purpose="run",
                                min_free_bytes=1024,
                                min_free_inodes=1,
                            )
                        )
                        args["simulator_executable"] = shutil.which("spectre")
                    return call("preflight_circuit_project", args)

                def execute(operation, args):
                    request = dict(operation=operation, arguments=args)
                    result = call("execute_circuit_operation", request)
                    assert result["state"] in {"retained_receipt", "running", "completed"}, result
                    retry = call("execute_circuit_operation", request)
                    assert retry["receipt"] == result["receipt"], retry
                    return result["receipt"]

                task = call(
                    "begin_circuit_task",
                    dict(task_id="copilot", presentation="foreground", choice_confirmed=True),
                )
                creation = {
                    **fixture(masters, "shared_passive"),
                    "task_ref": task["task_ref"],
                    "request_id": "prepare",
                }
                prepared = call("prepare_circuit_creation", creation)
                args = dict(
                    prepare_ref=prepared["prepare_ref"],
                    geometry_plan_digest=creation["geometry_plan_digest"],
                    request_id="circuit",
                )
                denied = registry.execute(
                    ToolCall(
                        "denied",
                        "execute_circuit_operation",
                        dict(operation="create_circuit_from_plan", arguments=args),
                    ),
                    context,
                )
                assert denied.status == "preflight_failed"
                preflight("shared_passive", "schematic", "schematic")
                created = execute("create_circuit_from_plan", args)
                assert created["saved"] and created["readback_verified"] and created["window_open"]
                call("inspect_created_circuit", dict(circuit_ref=created["circuit_ref"]))

                class SharedMcp:
                    def handle(self, message):
                        params = message["params"]
                        data = call(params["name"], params["arguments"])
                        return {
                            "result": {
                                "content": [{"type": "text", "text": json.dumps(data)}],
                                "isError": False,
                            }
                        }

                # Real session discovery and binding traverse the same finite transport.
                resistor, _ = selected(
                    SharedMcp(),
                    "analogLib",
                    "res",
                    "R1",
                    {"PLUS": "up", "MINUS": "down"},
                    {"r": "absent_condition"},
                )
                pdk_input = inputs(
                    [resistor],
                    "shared_pdk",
                    {"R1": {"PLUS": "A", "MINUS": "B"}},
                    {"R1": {"r": "2K"}},
                    {"R1": [0, 0]},
                    {"A": ["input", [-3, 1]], "B": ["output", [3, -1]]},
                )
                prepared_pdk = call(
                    "prepare_circuit_creation",
                    dict(pdk_input, task_ref=task["task_ref"], request_id="pdk_prepare"),
                )
                preflight("shared_pdk", "schematic", "schematic")
                pdk_created = execute(
                    "create_circuit_from_plan",
                    dict(
                        prepare_ref=prepared_pdk["prepare_ref"],
                        geometry_plan_digest=pdk_input["geometry_plan_digest"],
                        request_id="pdk_create",
                    ),
                )
                assert pdk_created["saved"] and pdk_created["readback_verified"]

                # Actual template redraw through the shared handler and data-only relay.
                record = gate_record("INVX1")
                local, scopes = copy.deepcopy(record), {}
                for net in local["topology"]["nets"]:
                    if net["is_global"]:
                        scopes[net["id"]] = "local"
                        net.update(is_global=False, source_name=net["source_name"].rstrip("!"))
                scratch_schematic(bridge, "genericWork", "shared_gate", local)
                preview = call(
                    "preview_template_symbol",
                    dict(
                        template_ref=record["template_ref"],
                        task_ref=task["task_ref"],
                        request_id="gate",
                        library="genericWork",
                        cell="shared_gate",
                        style="template_outline",
                        port_roles={"VDD": "power", "VSS": "ground"},
                        net_scope_map=scopes,
                    ),
                )
                preflight("shared_gate", "symbol", "schematicSymbol")
                symbol = execute(
                    "draw_symbol",
                    dict(
                        preview_ref=preview["preview_ref"],
                        preview_digest=preview["preview_digest"],
                        request_id="symbol",
                    ),
                )
                assert symbol["saved"] and symbol["readback_verified"]
                call("inspect_template_symbol", dict(symbol_ref=symbol["symbol_ref"]))

                preflight("network", "config", "config")
                config = execute(
                    "create_circuit_config",
                    dict(
                        library="genericWork",
                        cell="network",
                        top_library="genericWork",
                        top_cell="network",
                        task_ref=task["task_ref"],
                        request_id="config",
                    ),
                )
                assert config["saved"]
                call("inspect_circuit_config", dict(config_ref=config["config_ref"]))

                spec = recipe(cell="shared_maestro")
                preview = call("preview_simulation_recipe", dict(recipe=spec))
                preflight("shared_maestro", "maestro", "maestro")
                setup = execute(
                    "create_simulation_setup",
                    dict(
                        recipe=spec,
                        recipe_digest=preview["recipe_digest"],
                        task_ref=task["task_ref"],
                        request_id="maestro",
                    ),
                )
                assert setup["saved"] and setup["readback_verified"] and setup["window_open"]
                call("inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))
                preflight("shared_maestro", "maestro", "maestro", "read", run=True)
                run = execute(
                    "run_simulation_setup", dict(setup_ref=setup["setup_ref"], request_id="run")
                )
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    status = call("get_simulation_recipe_status", dict(run_ref=run["run_ref"]))
                    if status["state"] != "running":
                        break
                    time.sleep(0.5)
                assert status["state"] == "completed", status
                recovered = call(
                    "get_circuit_operation",
                    dict(operation="run_simulation_setup", request_id="run"),
                )
                assert recovered["receipt"]["history"] == run["history"]
                assert (
                    recovered["state"] == "completed" and not recovered["automatic_resume_allowed"]
                )
                # Final native checks, using an absent target and a retained source claim.
                identity = recovered["current_session"]["session_id"]
                claim = [identity, "config:guard_probe", "b" * 64]

                def native(function, values, claim=None, expected=None):
                    return (
                        "aiCopilotDispatch(sicoBindings[copilotCid] "
                        + json.dumps(function)
                        + " aiCopilotDecode("
                        + json.dumps(encode_value([values, claim, expected]))
                        + "))"
                    )

                bridge.skill(native("aiProjectClaim", claim))
                expected = [
                    task["task_ref"],
                    "foreground",
                    ["genericWork", "network", "guard_probe"],
                    "config",
                    "",
                    str(tmp_path / "library"),
                    None,
                ]
                values = [
                    task["task_ref"],
                    "guard_probe",
                    "genericWork",
                    "network",
                    "guard_probe",
                    ["genericWork", "network", "schematic"],
                    [],
                ]
                for index, replacement in [
                    (0, "task:other"),
                    (1, "background"),
                    (2, ["genericWork", "network", "wrong"]),
                    (4, "changed"),
                    (5, str(tmp_path / "other_library")),
                ]:
                    bad = copy.deepcopy(expected)
                    bad[index] = replacement
                    rejected = bridge.skill(
                        "if(errset("
                        + native("aiConfigCreate", values, claim, bad)
                        + ' nil) "accepted" "rejected")'
                    )
                    assert rejected == "rejected"
                for missing in (None, [identity, "config:other", "b" * 64]):
                    rejected = bridge.skill(
                        "if(errset("
                        + native("aiConfigCreate", values, missing, expected)
                        + ' nil) "accepted" "rejected")'
                    )
                    assert rejected == "rejected"
                assert bridge.skill('ddGetObj("genericWork" "network" "guard_probe")') == "nil"
                assert bridge.skill('aiRcRequests["config:guard_probe"]') == "nil"
                # Closing the entry window preserves project operations with explicit targets.
                bridge.skill(
                    'progn(copilotClosedSource=makeTable("closedSource" nil) '
                    'copilotClosedSource[\'window]=deOpenCellView("genericWork" "network" '
                    '"schematic" "schematic" nil "r") '
                    "sicoContextJson(copilotClosedSource) "
                    "hiCloseWindow(copilotClosedSource['window]) t)"
                )
                rejected = bridge.skill(
                    'if(errset(aiCopilotDispatch(copilotClosedSource "aiCircuitBeginTask" '
                    'list(list("closed" "foreground" t) nil nil)) nil) "accepted" "rejected")'
                )
                assert rejected == "accepted"
                assert bridge.skill('aiCircuitTasks["task:closed"]') == "foreground"
                project = json.loads(bridge.skill("sicoProjectContextJson(copilotClosedSource)"))
                assert project["valid"] and not project["source_valid"]
                assert json.loads(bridge.skill(
                    'aiEntryProjectRead(copilotClosedSource "project_observe" '
                    'list("foreground" "genericWork,after_close,schematic" "genericWork"))'
                ))["valid"]
                bridge.skill("hiCloseWindow(copilotEntry)")
                project = call("get_project_context", {})
                assert project["valid"] and not project["source_valid"]
                after_close = {
                    **fixture(masters, "after_close"),
                    "task_ref": task["task_ref"], "request_id": "prepare_closed",
                }
                prepared_closed = call("prepare_circuit_creation", after_close)
                preflight("after_close", "schematic", "schematic")
                receipt_closed = execute("create_circuit_from_plan", {
                    "prepare_ref": prepared_closed["prepare_ref"],
                    "geometry_plan_digest": after_close["geometry_plan_digest"],
                    "request_id": "create_closed",
                })
                assert receipt_closed["ok"]
            finally:
                (tmp_path / "evidence.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2)
                )
                bridge.skill("sicoDisconnect(copilotCid)")
