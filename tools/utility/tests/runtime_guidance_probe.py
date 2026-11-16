"""Isolated guidance consumers: real session state, synthetic external endpoints."""

import importlib.abc
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT, MODE = Path(sys.argv[1]), sys.argv[2]
sys.path[:0] = [str(ROOT / value) for value in (
    "tools/sico/python", "tools/ai/python", "tools/common/python")]


class BlockService(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "sico.service" or fullname.startswith("sico.service."):
            raise ImportError("blocked: " + fullname)
        return None


def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected process or network operation")


if MODE != "python":
    sys.meta_path.insert(0, BlockService())
subprocess.Popen = os.system = socket.socket.connect = socket.socket.connect_ex = forbidden
os.environ.clear()

from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.core.workbench_instructions import INSTRUCTIONS as WORKBENCH
from sico.storage.journal import SessionJournal

CONTEXT = BoundContext("instance", "generation", "window", {"valid": True})


def python_backend(journal):
    from sico.service.python_backend import PythonBackend

    loop = PythonBackend(None, ToolRegistry(), journal, CONTEXT)
    plain = loop._system()
    assert WORKBENCH not in plain
    loop.workbench = object()
    assert loop._system() == plain + "\n" + WORKBENCH


def codex(journal, *, audit=False, workbench=False, failure=None):
    from sico.codex import runtime_assembly as assembly
    from sico.codex.audit_instructions import INSTRUCTIONS as AUDIT
    from sico.codex.backend import CodexBackend
    from sico.codex.backend_instructions import INSTRUCTIONS as BASE

    provider = SimpleNamespace(model="test", options={}, environment={})
    loop = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
    calls, closed = [], []
    fault = RuntimeError("injected preparation or RPC failure")
    previous = "parent" if MODE == "resume" else None
    thread = previous or "started"
    if previous:
        loop.resources.prepare()
        loop._bind_thread("start", {"id": previous, "historyMode": "legacy"}, expected=None)
    if audit:
        loop.audit = SimpleNamespace(end_inputs=lambda *args, **kwargs: None)
    if workbench:
        loop.workbench = object()

    def request(method, params, **kwargs):
        calls.append((method, params))
        if method == "config/read":
            return {"config": {}}
        if failure == "rpc" and method.startswith("thread/"):
            raise fault
        if method == "thread/goal/get":
            return {"goal": None}
        return {"thread": {"id": thread, "historyMode": "legacy", "turns": []}}

    def prepare_failure():
        raise fault

    assembly.ToolBridge = lambda *args, **kwargs: SimpleNamespace(
        close=lambda: closed.append("bridge"))
    assembly.CodexRuntime = lambda *args, **kwargs: SimpleNamespace(
        cwd=str(journal.root), rpc=SimpleNamespace(request=request),
        close=lambda: closed.append("runtime"))
    if failure == "prepare":
        loop.resources.prepare = prepare_failure
    if failure:
        try:
            loop._ensure_runtime()
        except RuntimeError as caught:
            assert caught is fault
        else:
            raise AssertionError("Failure was swallowed")
        assert loop.thread_id is None
        assert closed == (["bridge"] if failure == "prepare" else ["runtime", "bridge"])
    else:
        loop._ensure_runtime()
        expected = BASE + (" " + WORKBENCH if workbench else "") + (" " + AUDIT if audit else "")
        connection = next(call for call in calls if call[0] in {"thread/start", "thread/resume"})
        assert connection[0] == ("thread/resume" if previous else "thread/start")
        assert connection[1]["developerInstructions"] == expected
        assert connection[1]["approvalPolicy"] == {"granular": {
            "mcp_elicitations": audit, "sandbox_approval": audit, "rules": audit,
            "request_permissions": audit, "skill_approval": audit}}
        methods = [method for method, params in calls]
        assert methods == ["config/read"] + (
            ["thread/goal/get", "thread/resume", "thread/settings/update"] if previous else
            ["thread/start", "thread/inject_items", "thread/settings/update"]
        ) + ["thread/memoryMode/set"], methods
        assert loop.thread_id == thread
        loop._ensure_runtime()
        assert len(calls) == len(methods)
        loop.close()
        assert closed == ["runtime", "bridge"]
    assert loop.runtime is None and loop.bridge is None
    loop.close()
    assert len(closed) == (1 if failure == "prepare" else 2)


with tempfile.TemporaryDirectory() as directory:
    if MODE == "python":
        with SessionJournal(Path(directory), "core") as journal:
            python_backend(journal)
    else:
        cases = ({"failure": value} for value in ("prepare", "rpc")) if MODE == "failure" else (
            {"audit": audit, "workbench": workbench}
            for audit in (False, True) for workbench in (False, True))
        for index, case in enumerate(cases):
            with SessionJournal(Path(directory), "codex-" + str(index)) as journal:
                codex(journal, **case)
if MODE != "python":
    assert not any(name == "sico.service" or name.startswith("sico.service.") for name in sys.modules)
