"""Controlled host decisions and declared project files; never uses a live EDA session."""

import json
from types import SimpleNamespace

from sico.adapters.virtuoso import context_tools
from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext, ToolResult
from sico.core.read_dependencies import MANIFEST
from sico.core.tools import Tool, no_arguments
from sico.service.workbench import attach_workbench

QUESTION = {"id": "choice", "header": "Input", "question": "Which project input?",
            "options": [{"label": "first", "description": "First project input"},
                        {"label": "second", "description": "Second project input"}]}


def project(root):
    (root / "docs").mkdir(exist_ok=True)
    for name in ("independent", "pdk", "model", "parameters", "target", "decision", "undeclared"):
        (root / "docs" / (name + ".txt")).write_text("Evidence for " + name)
    from sicostate import project_root

    path = project_root(root, create=True) / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "files": [
        {"path": "docs/" + name + ".txt", "depends_on": [] if name == "independent" else [name]}
        for name in ("independent", "pdk", "model", "parameters", "target", "decision")
    ]}))


class Broker:
    def __init__(self):
        self.calls = []

    def get_context(self, context):
        self.calls.append(context.record())
        return {"valid": True}

    def read(self, context, method, args=None):
        raise AssertionError("Unexpected EDA read " + method)


def backend(journal, root, provider=None):
    tools = context_tools(Broker(), journal)
    tools.register(Tool("decision_probe", "Controlled host prerequisite",
                        {"type": "object", "properties": {}, "additionalProperties": False},
                        no_arguments, lambda args, context: ToolResult(data={
                            "schema_version": "cad.pdk.preparation.v1",
                            "selection_required": True, "selection_ref": "pdk:fixture",
                            "candidates": [{"library": "first"}, {"library": "second"}],
                            "selection_question": {**QUESTION, "id": "pdk_library"},
                        })))
    context = BoundContext("instance", "generation", "target", {"cwd": str(root)})
    loop = CodexBackend(provider, tools, journal, context)
    loop.workbench = attach_workbench(loop)
    return loop


def start(loop):
    loop._begin("Read independent evidence and clarify inputs", loop.state.context, "input", "chat")
    loop.active, loop.cancelled = True, lambda: False
    from codex_thread_fixture import bind_thread
    bind_thread(loop)
    loop.native_history.bind("turn")
    loop.steering.open("turn")
    loop.runtime = SimpleNamespace(rpc=SimpleNamespace(send=lambda message, **kwargs: None))
    loop.input_connection()


def invoke(loop, name, args=None):
    return json.loads(loop._execute(name, args or {})["content"][0]["text"])


def host_question(loop, kind="pdk"):
    if kind == "pdk":
        assert invoke(loop, "decision_probe")["status"] == "ok"
    else:
        data = {"target_ref": "target:fixture", "decision_required": True,
                "selection_question": {**QUESTION, "id": "circuit_target"}}
        loop._event("tool.started", {"id": "target_probe", "name": "target_probe", "input": {}})
        event = loop._event("tool.finished", {"id": "target_probe", "name": "target_probe",
                                            "result": ToolResult(data=data).record()})
        loop.audit.require_target_choice(data, f"d_{event['sequence']}_tool", loop.state.context)
    return next(reversed(loop.audit.index.audits.values()))


def native_question(loop, *, questions=None, blocking=False, rpc_id=17):
    loop.audit.open({"id": rpc_id, "params": {"threadId": "thread", "turnId": "turn",
                    "itemId": "item" + str(rpc_id), "isBlocking": blocking,
                    "questions": questions or [QUESTION]}})
    return next(reversed(loop.audit.index.audits.values()))
