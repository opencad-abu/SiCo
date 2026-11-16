"""Isolated Virtuoso M3: CIW → schematic → symbol → ADE → layout in one session."""

import json
import os
import time

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import AI_ROOT, IC_LIBS, sample_master, session
from test_entry_context_gui import bind_fixture_technology
from test_simulation_recipe_gui import load_backend, source_fixture

from cadai.circuit_create import build_create_call
from sico.core.contracts import BoundContext, CircuitCallError
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context
from sico.transport.circuit import literal_call

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="isolated licensed M3 GUI acceptance"
)


def test_native_workflow_binding_and_logical_task(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("CAD_PYTHON", sys.executable)
    monkeypatch.setenv("SICO_HOME", str(AI_ROOT.parents[1]))
    monkeypatch.setenv("SICO_SKILL_TIMEOUT", "30")
    with session(tmp_path) as (native, _server, process):
        bind_fixture_technology(native, tmp_path, "genericWork")
        load_backend(native, tmp_path)
        source_fixture(native, _server)
        if not os.environ.get("CAD_AI_COMPILED_ROOT"):
            native.skill(f"load({json.dumps(str(AI_ROOT.parent / 'sico/skill++/SICO.ils'))})")
        native.skill(
            'progn(m3Source=makeTable("m3Source" nil) m3Source[\'window]=hiGetCIWindow() '
            "m3Cid=sicoEnsureBridge(m3Source))"
        )
        record = json.loads(
            native.skill(
                'sprintf(nil "{\\"instance_id\\":\\"%s\\",'
                '\\"generation\\":\\"%s\\",\\"snapshot\\":%s}" '
                "sicoBindings[m3Cid]['instanceId] sicoBindings[m3Cid]['generation] "
                "sicoContextJson(sicoBindings[m3Cid]))"
            )
        )
        ciw = BoundContext(record["instance_id"], record["generation"], "bound", record["snapshot"])
        with (
            BridgeClient.discover(tmp_path, ciw) as client,
            BridgeClient.discover(tmp_path, ciw) as foreign,
        ):
            client.register_session("workflow", ciw)
            foreign.register_session("foreign", ciw)
            client.configure_bindings("workflow", auto_bind=True, timeout_seconds=1)

            def call(
                context, fn, values, claim=None, expected=None, owner="workflow", connection=client
            ):
                with skill_call_context(session_id=owner, task_id="agent-task"):
                    return connection.circuit_call(
                        context, dict(function=fn, values=values, claim=claim, expected=expected)
                    )["circuit"]

            def wait_for(observe, timeout=15):
                deadline = time.monotonic() + timeout
                while True:
                    value = observe()
                    if value:
                        return value
                    assert process.poll() is None and time.monotonic() < deadline, (
                        tmp_path / "process.log"
                    ).read_text()
                    time.sleep(0.05)

            with skill_call_context(session_id="workflow"):
                project = client.read(ciw, "project_session", {"candidate": "a" * 32})["project"]
            project_id = project["session_id"]
            assert call(ciw, "aiCircuitBeginTask", ["design", "foreground", True])["ok"]
            data = {cell: sample_master(native, cell) for cell in ("res", "cap")}
            args = dict(fixture(data, "Amp"), task_ref="task:design", request_id="Amp")
            fn, values = literal_call(
                build_create_call("prepare_circuit_creation", args, workspace=tmp_path)
            )
            prepared = call(ciw, fn, values)
            assert prepared["ok"], prepared

            def commit(context, operation, fn, values, key, ref=None, target=None):
                described = call(
                    context, "aiCopilotDescribe", [operation, ref, "task:design", target]
                )
                assert described["ok"], described
                claim = [project_id, key, "b" * 64]
                assert call(context, "aiProjectClaim", claim)["ok"]
                result = call(context, fn, values, claim, described["binding"])
                assert result["ok"], result
                return result

            created = commit(
                ciw,
                "create_circuit_from_plan",
                "aiCreateExecute",
                [prepared["prepare_ref"], args["geometry_plan_digest"], "Amp"],
                "create-execute:Amp",
                prepared["prepare_ref"],
            )
            assert created["saved"] and created["readback_verified"]

            def bound_view(view):
                state = client.binding_snapshot("workflow")
                return next(
                    (
                        BoundContext.from_record(row)
                        for row in state["targets"]
                        if (row["snapshot"].get("cellview") or {}).get("view_name") == view
                    ),
                    None,
                )

            schematic = wait_for(lambda: bound_view("schematic"))
            # Focus changes never alter the submitted source or logical owner.
            native.skill("hiSetCurrentWindow(hiGetCIWindow())")
            assert call(schematic, "aiCreateInspect", [created["circuit_ref"]])["ok"]
            assert call(schematic, "aiCircuitBeginTask", ["design", "foreground", True])["ok"]
            denied = call(
                ciw,
                "aiCircuitBeginTask",
                ["design", "foreground", True],
                owner="foreign",
                connection=foreign,
            )
            assert denied["code"] == "task_source_mismatch" and not denied["operation_dispatched"]
            with pytest.raises(CircuitCallError) as denied:
                call(
                    ciw,
                    "aiCreateInspect",
                    [created["circuit_ref"]],
                    owner="foreign",
                    connection=foreign,
                )
            assert denied.value.code == "task_source_mismatch"
            with pytest.raises(CircuitCallError) as conflict:
                call(
                    ciw,
                    "aiCopilotOpenTarget",
                    ["genericWork", "Amp", "schematic", "schematic", "a"],
                    owner="foreign",
                    connection=foreign,
                )
            assert conflict.value.code == "binding_conflict"

            rows = [
                ["IN", "input", 1, None, "signal", -0.3125, -0.5, None],
                ["OUT", "output", 1, None, "signal", 2.3125, -0.5, True],
                ["VSS", "inputOutput", 1, None, "signal", -0.3125, -1.5, None],
            ]
            symbol_preview = call(
                schematic,
                "aiTemplateSymbolPreview",
                [
                    "task:design",
                    "symbol",
                    "fixture",
                    "c" * 64,
                    "genericWork",
                    "Amp",
                    "analogLib",
                    str(IC_LIBS / "artist/analogLib"),
                    rows,
                    2,
                    2,
                ],
            )
            assert symbol_preview["ok"], symbol_preview
            symbol = commit(
                ciw,
                "create_template_symbol",
                "aiTemplateSymbolCreate",
                [symbol_preview["preview_ref"], "c" * 64, "symbol"],
                "template-symbol:symbol",
                symbol_preview["preview_ref"],
            )
            symbol_context = wait_for(lambda: bound_view("symbol"))
            assert call(symbol_context, "aiTemplateSymbolInspect", [symbol["symbol_ref"]])["ok"]

            native.skill(
                'envSetVal("asimenv.startup" "projectDir" \'string '
                + json.dumps(str(tmp_path / "simulation"))
                + ")"
            )
            payload = [
                ["genericWork", "Tb", "maestro"],
                [["RVAL", "2K"]],
                "27",
                [
                    [
                        "nominal",
                        ["genericWork", "network", "schematic"],
                        "spectre",
                        [],
                        [],
                        [["dc", [["saveOppoint", True]]]],
                        [["dc_out", "expression", 'VDC("/OUT")', True, True],
                         ["OUT", "signal", "/OUT", True, True]],
                        ["spectre", "cmos_sch", "schematic"],
                        ["spectre"],
                    ]
                ],
            ]
            setup = commit(
                schematic,
                "create_simulation_setup",
                "aiSimCreate",
                ["task:design", "setup", "d" * 64, payload, []],
                "simulation-create:setup",
                target=payload[0],
            )
            ade = wait_for(lambda: bound_view("maestro"))
            assert ade.snapshot["ade_session"]
            assert call(ade, "aiSimModels", [setup["setup_ref"]])["ok"]
            assert call(schematic, "aiSimInspect", [setup["setup_ref"], []])["ok"]
            with pytest.raises(CircuitCallError) as denied:
                call(ciw, "aiSimModels", [setup["setup_ref"]], owner="foreign", connection=foreign)
            assert denied.value.code == "task_source_mismatch"
            # Run from the schematic source using a claim acquired from CIW.
            run_binding = call(
                schematic,
                "aiCopilotDescribe",
                ["run_simulation_setup", setup["setup_ref"], None, None],
            )["binding"]
            run_claim = [project_id, "simulation-run:nominal", "e" * 64]
            assert call(ciw, "aiProjectClaim", run_claim)["ok"]
            # The guarded run reads the retained model list under the same claim
            # and target binding; the broker reserves that declared row first.
            models = call(
                schematic, "aiSimModels", [setup["setup_ref"]], run_claim, run_binding
            )
            assert models["ok"] and not models["models"], models
            run = call(
                schematic, "aiSimRun", [setup["setup_ref"], [], "nominal"], run_claim, run_binding
            )
            assert run["state"] == "running", run
            assert client.binding_snapshot("workflow")["holds"][run["run_ref"]] == "running"
            terminal = wait_for(
                lambda: (
                    (row if row["state"] in {"completed", "failed", "stopped"} else None)
                    if (row := call(ciw, "aiSimStatus", [run["run_ref"]]))
                    else None
                ),
                timeout=60,
            )
            assert terminal["state"] == "completed", terminal
            bound = call(ade, "aiCopilotBindResults", [run["history"], setup["setup_ref"], "m3"])
            assert bound["ok"], bound
            captured = call(ade, "aiResultsCapture", [bound["context_ref"], run["history"], "m3"])
            assert captured["ok"] and captured["test_points"] == 1, captured
            results = []
            for page in range(captured["pages"]):
                results.extend(call(ade, "aiResultsPage", ["m3", page])["records"])
            measured = next(row for row in results if row[0] == "output" and row[4] == "dc_out")
            assert measured[5] == "scalar" and measured[6] == pytest.approx(1.0, abs=0.002)
            assert call(ade, "aiResultsRelease", ["m3"])["ok"]
            assert not client.binding_snapshot("workflow")["holds"]
            native.skill('envSetVal("graphic" "maskLayoutDefaultApp" \'string "Layout L")')
            client.configure_bindings("workflow", auto_bind=True, timeout_seconds=30)
            # Existing layout open needs an explicit choice; closing/reopening gets a new ref.
            native.skill(
                "let((m3Layout) m3Layout=dbOpenCellViewByType("
                '"genericWork" "Physical" "layout" "maskLayout" "a") '
                "dbSave(m3Layout) dbClose(m3Layout))"
            )
            assert call(
                ciw, "aiCopilotOpenTarget", ["genericWork", "Physical", "layout", "maskLayout", "a"]
            )["ok"]
            proposal = wait_for(
                lambda: next(
                    (
                        row
                        for row in client.binding_snapshot("workflow")["proposals"]
                        if row["target"]["snapshot"]["cellview"]["view_name"] == "layout"
                    ),
                    None,
                )
            )
            assert proposal["timeout_policy"] == "expire"
            client.resolve_binding("workflow", proposal["event_id"], "add_only")
            layout = BoundContext.from_record(proposal["target"])
            native.skill(
                "hiCloseWindow(sicoBindings[m3Cid]['targets]["
                + json.dumps(layout.target_id)
                + "]['window])"
            )
            wait_for(lambda: layout.target_id in client.binding_snapshot("workflow")["invalidated"])
            assert call(
                ciw, "aiCopilotOpenTarget", ["genericWork", "Physical", "layout", "maskLayout", "a"]
            )["ok"]
            reopened = wait_for(
                lambda: next(
                    (
                        row
                        for row in client.binding_snapshot("workflow")["proposals"]
                        if row["target"]["snapshot"]["cellview"]["view_name"] == "layout"
                    ),
                    None,
                )
            )
            assert reopened["target"]["snapshot"]["window_ref"] != layout.snapshot["window_ref"]
            client.resolve_binding("workflow", reopened["event_id"], "decline")
            with pytest.raises(CircuitCallError) as closed:
                with skill_call_context(session_id="workflow"):
                    client.get_context(layout)
            assert closed.value.code == "binding_target_closed"
            # Closing ADE invalidates only its immutable snapshot and returns default to CIW.
            native.skill('hiCloseWindow(aiSimulationSetups["simulation:setup"][\'window])')
            wait_for(lambda: ade.target_id in client.binding_snapshot("workflow")["invalidated"])
            final = client.binding_snapshot("workflow")
            assert final["default_target_id"] == "bound"
            assert not final["holds"] and len(final["reservations"]) == 7, final["reservations"]
            assert call(ciw, "aiCreateInspect", [created["circuit_ref"]])["ok"]
            (tmp_path / "m3-acceptance.json").write_text(
                json.dumps(
                    dict(created=created, symbol=symbol, setup=setup, run=terminal,
                         captured=captured, measurements=results, binding=final),
                    indent=2,
                )
            )
        native.skill("sicoDisconnect(m3Cid)")
