"""Shared context readers in an owned foreground Virtuoso, without simulation."""

import json
import os
import shlex
import sys

import pytest
from circuit_create_support import AI_ROOT
from template_smic28_support import migration_session
from test_circuit_gate import ROOT as GPDK_ROOT
from test_circuit_rc_gui import mcp_call

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ToolCall
from sico.transport.broker import ContextBroker

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed foreground context acceptance"
)


def bind_fixture_technology(bridge, tmp_path, library):
    # Only the isolated test library is bound; the reference PDK is read-only.
    with (tmp_path / "cds.lib").open("a") as definitions:
        definitions.write(f"DEFINE gpdk045 {GPDK_ROOT}/gpdk045\n")
    bridge.skill(
        "progn(ddUpdateLibList() techBindTechFile(ddGetObj("
        + json.dumps(library)
        + ') "gpdk045") t)'
    )


def test_shared_entry_windows_and_target_lifetime(tmp_path):
    with migration_session(tmp_path) as (bridge, server, proc):
        bridge.skill('envSetVal("graphic" "maskLayoutDefaultApp" \'string "Layout L")')
        for name in (
            "AI_pdk_core.il",
            "AI_window_context.il",
            "AI_entry_context.il",
            "SICO_entry.il",
            "SICO_context.il",
            "SICO_sessions.il",
        ):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        bridge.skill('ddCreateLib("ContextWork" ' + json.dumps(str(tmp_path / "library")) + ")")
        bind_fixture_technology(bridge, tmp_path, "ContextWork")
        bridge.skill(
            'progn(c= dbOpenCellViewByType("ContextWork" "dut" "schematic" "schematic" "w") '
            'dbCreateNet(c "IN") dbSave(c) dbClose(c) '
            'c=dbOpenCellViewByType("ContextWork" "dut" "layout" "maskLayout" "w") '
            "dbSave(c) dbClose(c) t)"
        )
        bridge.skill(
            'progn(cwSch=deOpenCellView("ContextWork" "dut" "schematic" "schematic" nil "a") '
            'cwLayout=deOpenCellView("ContextWork" "dut" "layout" "maskLayout" nil "r") t)'
        )
        refs = {
            kind: bridge.skill("aiWindowCapture(" + expr + ")")
            for kind, expr in (
                ("schematic", "cwSch"),
                ("layout", "cwLayout"),
                ("ciw", "hiGetCIWindow()"),
            )
        }
        observations = {}
        for kind, ref in refs.items():
            data = mcp_call(server, "get_entry_context", {"window_ref": ref})
            assert data.get("ok"), data
            assert data["source"]["kind"] == kind, data
            observations[kind] = data
        assert observations["schematic"]["design"]["nets"] == ["IN"]
        assert observations["layout"]["source"]["edit_cellview"]["mode"] == "r"
        libraries = observations["ciw"]["libraries"]
        assert "analogLib" not in {lib["name"] for lib in libraries["items"]}
        full = mcp_call(
            server, "get_entry_context", {"window_ref": refs["ciw"], "include_base_libraries": True}
        )
        assert "analogLib" in {lib["name"] for lib in full["libraries"]["items"]}
        listed = mcp_call(server, "list_windows", {"max_items": 2})
        assert listed["truncated"] and listed["next_offset"] == 2
        all_windows = mcp_call(server, "list_windows", {"max_items": 200})
        assert any(row["window_class"] == "session" for row in all_windows["windows"])
        assert mcp_call(server, "inspect_window", {"window_ref": refs["schematic"]})["window"][
            "screen_bbox"
        ]
        bridge.skill("progn(hiSetCurrentWindow(cwLayout) t)")
        assert (
            mcp_call(server, "get_entry_context", {"window_ref": refs["schematic"]})["source"][
                "kind"
            ]
            == "schematic"
        )
        bridge.skill(
            'progn(cwBind=makeTable("cwBind" nil) cwBind[\'window]=cwSch '
            'cwBind[\'entry]="schematic_rmb" sicoContextJson(cwBind))'
        )

        class SharedBroker:
            def read(self, context, method, args):
                from cadai.entry_context import wire_arguments

                fields = " ".join(json.dumps(v) for v in wire_arguments(method, args))
                return json.loads(
                    bridge.skill(
                        "sicoEntryRead(cwBind " + json.dumps(method) + " list(" + fields + "))"
                    )
                )

        tools = context_tools(SharedBroker())
        bound = BoundContext("instance", "generation", "target", {})
        shared = tools.execute(ToolCall("read", "get_entry_context", {}), bound)
        assert shared.status == "ok", shared
        assert shared.data["source"]["window_ref"] == refs["schematic"]
        assert shared.data["source"]["kind"] == "schematic"
        assert shared.data["guidance"] == observations["schematic"]["guidance"]
        with ContextBroker(10) as broker:
            config = tmp_path / "relay.json"
            config.write_text(
                json.dumps(
                    {"host": broker.address[0], "port": broker.address[1], "token": broker.token}
                )
            )
            config.chmod(0o600)
            command = shlex.join(
                [
                    sys.executable,
                    "-s",
                    str(AI_ROOT.parent / "sico/python/sico-entry"),
                    "relay",
                    "--connection-file",
                    str(config),
                ]
            )
            bridge.skill(
                "sicoConnect("
                + json.dumps(command)
                + ' "native-entry" "generation" cwSch cwBind)'
            )
            try:
                native_bound = broker.context("native-entry", timeout=10)
                native = context_tools(broker).execute(
                    ToolCall("full-path", "get_entry_context", {}), native_bound
                )
                assert native.status == "ok", native
                assert native.data["source"]["window_ref"] == refs["schematic"]
                assert native.data["design"] == observations["schematic"]["design"]
                observations["native_copilot_relay"] = native.data
            finally:
                bridge.skill("foreach(cid sicoBindings sicoDisconnect(cid))")
        bridge.skill(
            'progn(aiIpcState=makeTable("testIpc" nil) '
            "aiIpcState['entryWindowRef]=aiWindowCapture(cwSch) t)"
        )
        initialized = server.handle({"id": 3, "method": "initialize"})
        assert refs["schematic"] in initialized["result"]["instructions"]
        assert '"kind":"schematic"' in initialized["result"]["instructions"]
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill/AI_window_context.il")) + ")")
        assert bridge.skill("aiWindowCapture(cwSch)") == refs["schematic"]
        bridge.skill(
            "progn(cwCv=geGetEditCellView(cwSch) "
            'dbReplaceProp(cwCv "userNote" "string" "preserve") t)'
        )
        changed = mcp_call(server, "get_entry_context", {"window_ref": refs["schematic"]})
        assert changed["source"]["edit_cellview"]["modified"]
        assert bridge.skill("dbIsCellViewModified(cwCv)") == "t"
        bridge.skill("progn(dbSave(cwCv) hiCloseWindow(cwLayout) t)")
        stale = mcp_call(server, "inspect_window", {"window_ref": refs["layout"]})
        assert stale["code"] == "stale_context"
        observations.update(windows=all_windows, shared=shared.data, modified=changed, stale=stale)
        (tmp_path / "context-evidence.json").write_text(json.dumps(observations, indent=2))
        assert proc.poll() is None


def test_ade_source_selection_and_edit_in_place(tmp_path):
    with migration_session(tmp_path) as (bridge, server, proc):
        bridge.skill('envSetVal("graphic" "maskLayoutDefaultApp" \'string "Layout L")')
        for name in (
            "AI_pdk_core.il",
            "AI_window_context.il",
            "AI_entry_context.il",
            "SICO_entry.il",
            "SICO_context.il",
            "SICO_sessions.il",
        ):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        bridge.skill('ddCreateLib("ContextAde" ' + json.dumps(str(tmp_path / "ade-lib")) + ")")
        bind_fixture_technology(bridge, tmp_path, "ContextAde")
        bridge.skill(
            'progn(c=dbOpenCellViewByType("ContextAde" "tb" "schematic" "schematic" "w") '
            'dbCreateNet(c "VOUT") dbSave(c) dbClose(c) '
            'cwBack=maeOpenSetup("ContextAde" "tb" "maestro" ?application "Assembler") '
            'foreach(n list("nominal" "other") maeCreateTest(n ?lib "ContextAde" ?cell "tb" '
            '?view "schematic" ?simulator "spectre" ?session cwBack)) '
            "maeSaveSetup(?session cwBack) maeCloseSession(?session cwBack) "
            'cwAde=deOpenCellView("ContextAde" "tb" "maestro" "maestro" nil "a") t)'
        )
        bridge.skill(
            "progn(cwSession=axlGetWindowSession(cwAde) "
            'unless(cwSession error("ADE window unavailable")) t)'
        )
        ref = bridge.skill("aiWindowCapture(cwAde)")
        ade = mcp_call(server, "get_entry_context", {"window_ref": ref})
        assert ade["ok"], ade
        assert ade["source"]["kind"] == "ade" and ade["ade"]["test_count"] == 2
        bridge.skill(
            'progn(cwBinding=makeTable("cwBinding" nil) cwBinding[\'window]=cwAde '
            'cwBinding[\'testName]="nominal" cwBinding[\'historyName]="ContextSynthetic.1" '
            "cwBinding['historyHandle]=axlPutHistoryEntry(axlGetMainSetupDB(cwSession) "
            '"ContextSynthetic.1") hiSetCurrentWindow(hiGetCIWindow()) '
            "sicoContextJson(cwBinding))"
        )
        from cadai.entry_context import enrich

        selected = enrich(
            json.loads(
                bridge.skill(
                    'sicoEntryRead(cwBinding "get_entry_context" list("-" "20" "0" "0"))'
                )
            )
        )
        assert selected["source"]["window_ref"] == ref
        assert selected["ade"]["selected_test"] == "nominal"
        assert selected["ade"]["selected_history"] == "ContextSynthetic.1"
        assert [row["name"] for row in selected["ade"]["tests"]] == ["nominal"]
        assert selected["ade"]["data_basis"] == "current_setup_not_history_checkpoint"
        assert selected["ade"]["results_evaluated"] is False
        bridge.skill('progn(cwBinding[\'testName]="deleted_test" t)')
        assert not json.loads(bridge.skill("sicoContextJson(cwBinding)"))["valid"]

        bridge.skill(
            'progn(c=dbOpenCellViewByType("ContextAde" "leaf" "layout" "maskLayout" "w") '
            'dbCreateRect(c list("text" "drawing") list(0:0 1:1)) dbSave(c) '
            'top=dbOpenCellViewByType("ContextAde" "top" "layout" "maskLayout" "w") '
            'dbCreateInst(top c "I_leaf_0" 2:2 "R0") dbSave(top) dbClose(top) dbClose(c) '
            'cwTop=deOpenCellView("ContextAde" "top" "layout" "maskLayout" nil "a") t)'
        )
        top_ref = bridge.skill("aiWindowCapture(cwTop)")
        bridge.skill('geEditInPlace(cwTop "a" car(geGetEditCellView(cwTop)~>instances) 0 0 0)')
        stale = mcp_call(server, "inspect_window", {"window_ref": top_ref})
        assert stale["code"] == "stale_context", stale
        eip_ref = bridge.skill("aiWindowCapture(cwTop)")
        eip = mcp_call(server, "get_entry_context", {"window_ref": eip_ref})
        assert eip["source"]["display_cellview"]["cell"] == "top", eip
        assert eip["source"]["edit_cellview"]["cell"] == "leaf", eip
        assert eip["source"]["edit_hierarchy"][0][0] == "I_leaf_0", eip
        # Closing after recapture must invalidate the new reference through the
        # hook itself, before a later inspect can notice the missing window.
        bridge.skill("progn(hiCloseWindow(cwTop) t)")
        assert bridge.skill("aiWindowEntries[" + json.dumps(eip_ref) + "]['closed]") == "t"
        assert (
            mcp_call(server, "inspect_window", {"window_ref": eip_ref})["code"]
            == "stale_context"
        )
        (tmp_path / "ade-eip-evidence.json").write_text(
            json.dumps({"ade": ade, "selected": selected, "eip": eip, "stale": stale}, indent=2)
        )
        assert proc.poll() is None
