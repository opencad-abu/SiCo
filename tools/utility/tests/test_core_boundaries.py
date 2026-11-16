"""EQ-08 core contracts stay below host and transport implementations."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import load_manifest


ROOT = Path(__file__).resolve().parents[3]
for source_root in (
    ROOT / "tools/ai/python",
    ROOT / "tools/sico/python",
    ROOT / "tools/common/python",
):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
ALL_RULES = {
    rule["id"]: rule
    for rule in load_manifest()["rules"]
}
RULES = {key: value for key, value in ALL_RULES.items() if key.startswith("core-")}


def test_core_sources_do_not_cross_upward_into_runtime_layers():
    for rule in RULES.values():
        path = ROOT / rule["paths"][0]
        assert not violations(
            path.read_text(encoding="utf-8"),
            rule,
            path=path.relative_to(ROOT).as_posix(),
            module="sico.core." + path.stem,
        )


def test_storage_host_routes_do_not_import_service_dtos():
    rule = ALL_RULES["storage-host-routes-one-way-boundary"]
    path = ROOT / rule["paths"][0]
    assert not violations(
        path.read_text(encoding="utf-8"),
        rule,
        path=path.relative_to(ROOT).as_posix(),
        module="sico.storage.host_routes",
    )


def test_host_contracts_import_without_service_or_transport(tmp_path):
    source_roots = [
        ROOT / "tools/ai/python",
        ROOT / "tools/sico/python",
        ROOT / "tools/common/python",
    ]
    script = "import sys\nsys.path[:0] = " + repr([str(path) for path in source_roots]) + "\n" + r'''
import importlib.abc
import sys

BLOCKED = ("sico.transport", "sico.service", "sico.providers", "PyQt5", "PySide")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".")
               for prefix in BLOCKED):
            raise ImportError("blocked runtime import: " + fullname)
        return None

sys.meta_path.insert(0, Block())
from sico.core.host_contracts import host_identity, session_address_record

host = {"instance_id": "i", "generation": "g", "bridge_id": "b", "router_id": "r"}
assert host_identity(host) == host
address = {"project_id": "p", "service_id": "s", "session_id": "q", "runtime_id": "r"}
assert session_address_record(address, project_id="p", service_id="s", session_id="q",
                              require_runtime=True) == address
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in source_roots)
    result = subprocess.run(
        [sys.executable, "-B", "-S", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_registry_accepts_only_the_narrow_execution_capability():
    from sico.core.contracts import BoundContext, ToolCall, ToolExecution, ToolResult
    from sico.core.tools import Tool, ToolRegistry, no_arguments

    calls = []
    registry = ToolRegistry()
    registry.register(Tool(
        "probe", "probe", {"type": "object"}, no_arguments,
        lambda inputs, context: calls.append(True) or ToolResult(),
    ))
    context = BoundContext("instance", "generation", "target", {})
    blocked = registry.execute(
        ToolCall("id", "probe", {}),
        context,
        ToolExecution(cancelled=lambda: True),
    )
    assert blocked.status == "cancelled_before_start"
    assert calls == []

    admission = registry.execute(
        ToolCall("id2", "probe", {}),
        context,
        ToolExecution(before_start=lambda: ToolResult("blocked", "wait")),
    )
    assert admission.status == "blocked"
    assert calls == []


def test_core_contracts_and_tools_import_without_host_runtimes(tmp_path):
    source_roots = [
        ROOT / "tools/ai/python",
        ROOT / "tools/sico/python",
        ROOT / "tools/common/python",
    ]
    script = "import sys\nsys.path[:0] = " + repr([str(path) for path in source_roots]) + "\n" + r'''
import importlib.abc
import sys

BLOCKED = ("PyQt5", "PySide", "sico.transport", "sico.service", "sico.providers")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".")
               for prefix in BLOCKED):
            raise ImportError("blocked runtime import: " + fullname)
        return None

sys.meta_path.insert(0, Block())
from sico.core.contracts import BoundContext, ToolExecution
from sico.core.tools import Tool, ToolRegistry, no_arguments

seen = []
registry = ToolRegistry()
registry.register(Tool("probe", "probe", {"type": "object"}, no_arguments,
                       lambda inputs, context: seen.append(True) or None))
assert ToolExecution(cancelled=lambda: True).cancelled()
assert registry.resolve("probe") is not None
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in source_roots)
    result = subprocess.run(
        [sys.executable, "-B", "-S", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
