"""Pinned app-server actually enforces the selected native filesystem boundary."""

import json
import shlex
import shutil
import tempfile

import pytest
from test_agent_codex_native import CONTEXT, answer, gateway, native_codex, registry, settings, sse
from test_codex_native_evidence import execute

from sico.codex.access_policy import default_access
from sico.codex.backend import CodexBackend
from sico.storage.journal import SessionJournal
from sico.service.workbench import attach_workbench


@pytest.mark.parametrize("mode,allowed", [("read-only", False), ("workspace-write", True),
                                         ("danger-full-access", True)])
def test_native_scope_enforces_write_then_survives_resume(tmp_path, gateway, native_codex, mode, allowed):
    target = tmp_path / "permission-proof.txt"
    gateway.responses.put(execute("printf allowed > " + shlex.quote(str(target)), "probe"))
    gateway.responses.put(answer("Permission probe completed."))
    provider = settings(gateway, native_codex, "native")
    access = {**default_access(), "sandbox": mode, "approval": "never"}
    with SessionJournal(tmp_path, "scope") as journal:
        loop = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            options = loop.turn_options.freeze({"access": access})
            events = list(loop.run("Execute the permission probe once.", turn_options=options))
            assert events[-1]["kind"] == "task.completed", events[-3:]
            assert target.exists() == allowed
            if allowed:
                assert target.read_text() == "allowed"
            (tmp_path / "scope-evidence.json").write_text(json.dumps(events, ensure_ascii=False))
        finally:
            loop.close()

    with SessionJournal(tmp_path, "scope") as journal:
        loop = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            assert loop.turn_options.selected["access"] == options["access"]
            loop.resume_thread()
            assert len(gateway.requests) == 2
        finally:
            loop.close()


@pytest.mark.parametrize("choice,allowed", [("仅批准本次", True), ("取消回合", False)])
def test_real_escalation_waits_for_user_decision(tmp_path, gateway, native_codex, choice, allowed):
    target = tmp_path / "approved-once.txt"
    def escalation(request):
        for group in request["tools"]:
            for tool in group.get("tools", [group]):
                if tool.get("name") == "exec_command":
                    item = {"type": "function_call", "id": "fc_escalate", "call_id": "escalate",
                            "name": "exec_command", "arguments": json.dumps({
                                "cmd": "printf approved > " + shlex.quote(str(target)),
                                "sandbox_permissions": "require_escalated",
                                "justification": "Approve the permission test write?",
                                "yield_time_ms": 1000, "login": False})}
                    if group.get("type") == "namespace":
                        item["namespace"] = group["name"]
                    return sse(item)
        raise AssertionError("Missing native command tool")
    gateway.responses.put(escalation)
    gateway.responses.put(answer("Approval test finished."))
    with SessionJournal(tmp_path, "approval") as journal:
        loop = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
        loop.workbench = attach_workbench(loop)
        count = 0
        try:
            options = loop.turn_options.freeze({"access": {**default_access(), "sandbox": "read-only"}})
            for event in loop.run("Run the authorized permission test.", turn_options=options):
                if event["kind"] == "workbench.audit.opened":
                    assert not target.exists()
                    row = loop.audit.index.audits[event["payload"]["id"]]
                    loop.audit.answer(row["id"], {"codex_approval": {"choice": choice, "text": ""}},
                                      "reply", "user", row["task_id"])
                    count += 1
            assert count == 1
            assert loop.state.task["status"] == ("completed" if allowed else "cancelled"), loop.state.task
            assert target.exists() == allowed
        finally:
            loop.close()


def test_workspace_blocks_write_outside_project_and_temporary_roots(tmp_path, gateway, native_codex):
    from pathlib import Path

    outside = Path(tempfile.mkdtemp(prefix="sico-access-", dir="/var/tmp"))
    target = outside / "outside.txt"
    gateway.responses.put(execute("printf forbidden > " + shlex.quote(str(target)), "outside"))
    gateway.responses.put(answer("Outside write probe finished."))
    try:
        with SessionJournal(tmp_path, "outside") as journal:
            loop = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
            try:
                options = loop.turn_options.freeze({"access": {**default_access(), "approval": "never"}})
                assert list(loop.run("Probe the filesystem boundary.", turn_options=options))[-1]["kind"] == "task.completed"
                assert not target.exists()
            finally:
                loop.close()
    finally:
        shutil.rmtree(outside)


def test_command_network_switch_controls_local_connection(tmp_path, gateway, native_codex, monkeypatch):
    connections = []
    def get(handler):
        connections.append(handler.path)
        handler.send_response(200)
        handler.send_header("Content-Length", "2")
        handler.end_headers()
        handler.wfile.write(b"ok")
    monkeypatch.setattr(gateway.RequestHandlerClass, "do_GET", get, raising=False)
    with SessionJournal(tmp_path, "network") as journal:
        loop = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
        try:
            for enabled in (False, True):
                gateway.responses.put(execute("curl --noproxy '*' --max-time 2 -s " +
                    shlex.quote(f"http://127.0.0.1:{gateway.server_port}/network-probe"), str(enabled)))
                gateway.responses.put(answer("Network probe finished."))
                options = loop.turn_options.freeze({"access": {
                    **default_access(), "network": enabled, "approval": "never"}})
                assert list(loop.run("Probe command networking.", turn_options=options))[-1]["kind"] == "task.completed"
                # The session catalog fetch is host-side and not part of the sandboxed probe.
                probes = [path for path in connections if path == "/network-probe"]
                assert probes == (["/network-probe"] if enabled else [])
        finally:
            loop.close()
