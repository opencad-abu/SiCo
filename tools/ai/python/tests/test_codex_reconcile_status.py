"""Optional native inventories cannot strand an interrupted task on a dead runtime."""

import pytest
from sico.core.contracts import ToolCall
from sico.service.controller import SessionController
from sico.service.published import freeze
from sico.storage.journal import SessionJournal
from test_agent_interrupted_reconcile import status_tool
from test_codex_thread_operations import wired


def interrupt(controller, calls):
    loop = controller.loop
    loop._begin("original write", controller.current, "original", "test")
    for number in range(calls):
        call = ToolCall("call-" + str(number), "execute_circuit_operation", dict(
            operation="create", arguments=dict(request_id="request-" + str(number),
                library="work", cell="inv", view="schematic")))
        loop.state.task["pending_calls"].append(call.record())
        loop._event("tool.started", call.record())
    loop._finish("needs_reconcile", "Native runtime disconnected")
    controller._task = freeze(loop.state.task)
    controller.paused = True
    return loop.state.task["id"]


@pytest.mark.parametrize("calls", [0, 1])
def test_pending_reconcile_uses_snapshots_and_can_finish_without_native_rpc(
        tmp_path, monkeypatch, calls):
    with SessionJournal(tmp_path, "reconcile-status") as journal:
        loop, requests = wired(journal, monkeypatch)
        controller = SessionController(loop)
        task = interrupt(controller, calls=calls)
        observed = []
        status_tool(controller, observed)

        def dead_runtime(*_args, **_kwargs):
            pytest.fail("Waiting for reconciliation must not call the native runtime")

        monkeypatch.setattr(loop, "_ensure_runtime", dead_runtime)
        loop.runtime.rpc.request = dead_runtime
        assert controller.thread_commands.thread_status()["busy"]
        assert controller.backend_commands.turn_input_status()["busy"]
        assert controller.backend_commands.resource_status()["busy"]
        assert not requests
        if calls:
            controller.reconciliation.query_interrupted("call-0", expected_task=task)
            controller.reconciliation.resolve_interrupted("call-0", "completed",
                expected_task=task, evidence="Read original operation receipt")
            assert len(observed) == 1
        else:
            controller.reconciliation.resolve_interrupted("", "no_calls", expected_task=task)
        assert controller._task["status"] == "cancelled" and not controller.paused
        assert not requests
        loop.close()
