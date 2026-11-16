"""Original 0.154 child inputs with a controlled local provider, no model account."""

import json

import pytest
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)
from test_codex_child_input import answer_child
from test_codex_elicitation_runtime import resources, wait_for
from test_codex_resources_runtime import output, tool_call

from sico.codex.rpc import AppServer
from sico.service.backend import create_backend
from sico.service.controller import SessionController
from sico.core.session_events import EventCursor
from sico.storage.journal import SessionJournal


def call_named(request, name, call_id, arguments):
    for group in request["tools"]:
        for tool in group.get("tools", [group]):
            if tool.get("name") in {name, "functions__" + name, "collaboration__" + name}:
                namespace = (group["name"] if group.get("type") == "namespace"
                             else tool["name"].rsplit("__", 1)[0])
                return tool_call(request, namespace, name, call_id, arguments)
    raise AssertionError("Missing controlled tool " + name)


@pytest.mark.parametrize("tool_format,kind", [("native", "native"), ("flat", "native"),
                                           ("native", "form"), ("flat", "url")])
def test_actual_child_input_on_original_connection(
    tmp_path, gateway, native_codex, monkeypatch, tool_format, kind,  # noqa: F811
):
    provider = settings(gateway, native_codex, tool_format)
    if kind != "native":
        provider.options["external_resources"] = str(resources(tmp_path, kind))
    rpc_methods, frames, sends, proofs = [], [], [], []
    original_request = AppServer.request
    original_next, original_send = AppServer._next, AppServer.send
    def rpc(self, method, params, **kwargs):
        rpc_methods.append(method)
        return original_request(self, method, params, **kwargs)
    def next_frame(self, timeout):
        frame = original_next(self, timeout)
        frames.append(frame)
        return frame
    def send(self, message, **kwargs):
        sends.append(message)
        return original_send(self, message, **kwargs)
    monkeypatch.setattr(AppServer, "request", rpc)
    monkeypatch.setattr(AppServer, "_next", next_frame)
    monkeypatch.setattr(AppServer, "send", send)

    def spawn(request):
        return call_named(request, "spawn_agent", "spawn-child", {
            "task_name": "scope_child", "fork_turns": "none",
            "message": "Ask the user which scope to inspect, then report the answer."})

    def respond(request):
        outputs = {i.get("call_id") for i in request["input"]
                   if i.get("type") == "function_call_output"}
        if "spawn-child" in outputs:
            return answer("Parent output complete; the child may still be waiting.")
        if "child-question" in outputs:
            proofs.append(output(request, "child-question"))
            return answer("Child response received.")
        if kind == "native":
            return call_named(request, "request_user_input", "child-question", {
                "questions": [{"id": "scope", "header": "Scope", "question": "Which scope?",
                    "options": [{"label": "全局", "description": "Full scope"},
                                {"label": "局部", "description": "Local scope"}]}]})
        for group in request["tools"]:
            for tool in group.get("tools", [group]):
                if tool.get("name", "").endswith("interaction_probe"):
                    namespace = (group["name"] if group.get("type") == "namespace"
                                 else tool["name"].rsplit("__", 1)[0])
                    return tool_call(request, namespace, "interaction_probe", "child-question", {})
        raise AssertionError("Missing child MCP tool")

    gateway.responses.put(spawn)
    for _ in range(12):
        gateway.responses.put(respond)
    with SessionJournal(tmp_path, "child-input-runtime") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        controller = SessionController(backend)
        def input_ready():
            # Pending and its durable opened event are published under this lock.
            with backend.lock:
                return bool(backend.audit.pending) or not controller.busy
        try:
            controller.submit("Spawn one inspection child and let it ask the user for scope.")
            wait_for(input_ready, timeout=15)
            if kind == "native":
                # Original 0.154 rejects this model tool in children before any server request.
                assert not backend.audit.pending and not controller.busy
                assert proofs == ["request_user_input can only be used by the root thread"]
                assert not any(f.get("method") == "item/tool/requestUserInput" for f in frames)
                assert backend.state.task["status"] == "completed"
                assert rpc_methods.count("turn/start") == 1
                assert not any(method.startswith("account/") for method in rpc_methods)
                return
            assert backend.audit.pending, {"task": backend.state.task, "frames": frames[-12:]}
            row = next(reversed(backend.audit.index.audits.values()))
            binding = row["binding"]
            assert binding["child_thread_id"] != backend.thread_id
            assert binding["child_turn_id"] != binding.get("host_turn_id")
            answer_child(backend, controller, row)
            wait_for(lambda: not controller.busy, timeout=15)
            assert backend.state.task["status"] == "completed", backend.state.task
            assert row["status"] == "answered" and proofs
            assert "全局" in str(proofs) if kind == "native" else "accept" in str(proofs)
            assert rpc_methods.count("turn/start") == 1
            assert not any(method.startswith("account/") for method in rpc_methods)
            assert not (backend.runtime.home / "auth.json").exists()
            assert len([e for e in journal.events() if e["kind"] == "task.started"]) == 1
            assert not controller.inbox.pending
            EventCursor(controller.session_id, controller.runtime_id, CONTEXT).consume(
                controller.updates.read_updates())
        finally:
            (tmp_path / "child-input-proof.json").write_text(json.dumps({
                "methods": rpc_methods, "frames": frames, "sends": sends,
                "proofs": proofs, "task": backend.state.task,
            }, ensure_ascii=False, indent=2))
            controller.close()
            assert controller.wait(8)
