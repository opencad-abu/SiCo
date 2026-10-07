"""Small deterministic domain tools for M0 tests and offline demos."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .tool_broker import ToolBroker, ToolResult


@dataclass
class FakeDomainTools:
    """In-memory tools with observable call counts and bounded artifacts."""

    root: Path | None = None
    calls: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    fail_next: dict[str, BaseException] = field(default_factory=dict)
    gate_status: str = "PASS"

    def register(self, broker: ToolBroker) -> None:
        schema = {"type": "object", "additionalProperties": True}
        for name, handler, side_effect in (
            ("plan_experiment", self.plan_experiment, True),
            ("submit_experiment", self.submit_experiment, True),
            ("propose_model", self.propose_model, True),
            ("request_revision", self.request_revision, True),
        ):
            broker.register_tool(name, handler, schema=schema, side_effect=side_effect)

    def _run(self, name: str, params: Mapping[str, Any]) -> ToolResult:
        self.calls.append((name, dict(params)))
        failure = self.fail_next.pop(name, None)
        if failure is not None:
            raise failure
        return ToolResult(
            status="PASS",
            summary={"tool": name, "call_index": len(self.calls)},
            outputs={"accepted": True, "deterministic_gate": self.gate_status},
            source_generation=None,
            side_effect=True,
            deterministic_gate=self.gate_status,
        )

    def plan_experiment(self, params: Mapping[str, Any]) -> ToolResult:
        return self._run("plan_experiment", params)

    def submit_experiment(self, params: Mapping[str, Any]) -> ToolResult:
        return self._run("submit_experiment", params)

    def propose_model(self, params: Mapping[str, Any]) -> ToolResult:
        return self._run("propose_model", params)

    def request_revision(self, params: Mapping[str, Any]) -> ToolResult:
        return self._run("request_revision", params)


def register_fake_tools(broker: ToolBroker, *, root: Path | None = None) -> FakeDomainTools:
    tools = FakeDomainTools(root=root)
    tools.register(broker)
    return tools


__all__ = ["FakeDomainTools", "register_fake_tools"]
