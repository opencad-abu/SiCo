"""Actual Copilot relay -> shared project preflight/recovery in an owned GUI session."""

import json
import os
import shlex
import sys

import pytest
from circuit_create_support import AI_ROOT
from template_smic28_support import migration_session
from test_circuit_rc_gui import mcp_call

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import ToolCall
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed foreground project adapter"
)


def test_native_project_adapter_preflight_and_shared_recovery(tmp_path):
    with migration_session(tmp_path) as (bridge, server, _):
        bridge.skill("load(" + json.dumps(str(AI_ROOT.parent / "sico/skill++/SICO.ils")) + ")")
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill/AI_circuit_config.il")) + ")")
        bridge.skill(
            'progn(ddCreateLib("ProjectAdapterWork" ' + json.dumps(str(tmp_path / "worklib")) + ") "
            'projectCv=dbOpenCellViewByType("ProjectAdapterWork" "top" '
            '"schematic" "schematic" "w") '
            "schCheck(projectCv) dbSave(projectCv) dbClose(projectCv) "
            'projectWindow=deOpenCellView("ProjectAdapterWork" "top" '
            '"schematic" "schematic" nil "r") t)'
        )
        requirements = {
            "presentation": "foreground",
            "targets": [
                {
                    "library": "ProjectAdapterWork",
                    "cell": "top",
                    "view": "schematic",
                    "intent": "read",
                    "oa_view_type": "schematic",
                }
            ],
            "libraries": [
                {"library": "ProjectAdapterWork", "expected_path": str(tmp_path / "worklib")}
            ],
            "directories": [
                {
                    "path": str(tmp_path),
                    "purpose": "project",
                    "min_free_bytes": 1024,
                    "min_free_inodes": 1,
                }
            ],
            "model_files": [],
        }
        mcp_call(
            server,
            "begin_circuit_task",
            {
                "task_id": "adapter",
                "presentation": "foreground",
                "choice_confirmed": True,
            },
        )
        created = mcp_call(
            server,
            "execute_circuit_operation",
            {
                "operation": "create_circuit_config",
                "arguments": {
                    "library": "ProjectAdapterWork",
                    "cell": "top",
                    "top_library": "ProjectAdapterWork",
                    "top_cell": "top",
                    "task_ref": "task:adapter",
                    "request_id": "adapter_config",
                },
            },
        )
        assert created["state"] == "retained_receipt" and created["receipt"]["saved"], created
        with SessionJournal(tmp_path, "adapter") as journal, ContextBroker(10) as broker:
            configuration = journal.directory / "relay.json"
            configuration.write_text(
                json.dumps(
                    {
                        "host": broker.address[0],
                        "port": broker.address[1],
                        "token": broker.token,
                    }
                )
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
                "projectCid=sicoConnect("
                + json.dumps(command)
                + ' "project-instance" "project-generation" projectWindow)'
            )
            try:
                target = broker.context("project-instance", timeout=10)
                tools = context_tools(broker, journal)
                direct = mcp_call(server, "preflight_circuit_project", requirements)
                checked = tools.execute(
                    ToolCall("check", "preflight_circuit_project", requirements), target
                )
                assert checked.status == "ok", checked
                assert checked.data["checks_passed"] and direct["checks_passed"]
                for key in ("checks", "paths", "session", "requirements_digest", "not_checked"):
                    # Directory free space can change between observations.
                    if key != "paths":
                        assert checked.data[key] == direct[key], key
                args = {"operation": "create_circuit_config", "request_id": "adapter_config"}
                recovery = tools.execute(ToolCall("recover", "get_circuit_operation", args), target)
                assert recovery.status == "ok" and recovery.data["receipt"] == created["receipt"]
                assert recovery.data["automatic_resume_allowed"] is False
                bad = tools.execute(
                    ToolCall(
                        "bad",
                        "preflight_circuit_project",
                        {
                            **requirements,
                            "model_files": [str(tmp_path / "missing.scs")],
                        },
                    ),
                    target,
                )
                assert bad.status == "preflight_failed" and not bad.data["checks_passed"]
                # Validate the actual decoder, not only the Python token producer.
                rejected = bridge.skill(
                    "progn(projectInvalid=errset(aiEntryProjectRead(sicoBindings[projectCid] "
                    '"project_observe" list("foreground" "ProjectAdapterWork,top,schematic;" '
                    '"ProjectAdapterWork")) nil) if(projectInvalid "accepted" "rejected"))'
                )
                assert rejected == "rejected"
                bridge.skill("progn(hiCloseWindow(projectWindow) t)")
                closed = tools.execute(
                    ToolCall("closed", "preflight_circuit_project", requirements), target
                )
                assert closed.status == "ok" and closed.data["checks_passed"]
                offline = tools.execute(
                    ToolCall(
                        "offline",
                        "get_circuit_operation",
                        {
                            **args,
                            "refresh": False,
                        },
                    ),
                    target,
                )
                assert offline.status == "ok" and offline.data["state"] == "offline_record"
                assert offline.data["last_observation"]["receipt"]["saved"]
                (tmp_path / "adapter-evidence.json").write_text(
                    json.dumps(
                        {
                            "preflight": checked.record(),
                            "recovery": recovery.record(),
                            "failed_preflight": bad.record(),
                            "offline_after_close": offline.record(),
                            "invalid_native_tokens": rejected,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            finally:
                bridge.skill("sicoDisconnect(projectCid)")
