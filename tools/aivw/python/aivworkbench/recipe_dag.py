"""Workflow DAG validation kept independent from block-specific recipes."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .errors import RecipeError
from .registry import PluginRegistry


def validate_workflow(value: object, registry: PluginRegistry) -> None:
    if not isinstance(value, Mapping):
        raise RecipeError("workflow must be an object")
    gates = value.get("gates")
    if not isinstance(gates, list) or not gates:
        raise RecipeError("workflow.gates must be a non-empty array")
    dependencies: dict[str, tuple[str, ...]] = {}
    for raw in gates:
        if not isinstance(raw, Mapping):
            raise RecipeError("workflow.gate must be an object")
        gate_id = _text(raw.get("id"), "workflow.gate.id")
        if gate_id in dependencies:
            raise RecipeError(f"duplicate workflow gate: {gate_id}")
        registry.require_executor(_text(raw.get("executor"), f"gate {gate_id}.executor"))
        needs = raw.get("needs", [])
        if not isinstance(needs, list) or any(not isinstance(item, str) or not item for item in needs):
            raise RecipeError(f"gate {gate_id}.needs must be an array of gate IDs")
        dependencies[gate_id] = tuple(needs)
    for gate_id, needs in dependencies.items():
        missing = set(needs) - set(dependencies)
        if missing:
            raise RecipeError(f"gate {gate_id} has missing dependencies: {sorted(missing)}")
    _check_acyclic(dependencies)


def workflow_order(value: object) -> tuple[str, ...]:
    """Return a stable topological order, preserving recipe order where possible."""
    if not isinstance(value, Mapping) or not isinstance(value.get("gates"), list):
        raise RecipeError("workflow must contain gates before it can be ordered")
    gates = value["gates"]
    order = [_text(item.get("id"), "workflow.gate.id") for item in gates if isinstance(item, Mapping)]
    if len(order) != len(gates):
        raise RecipeError("workflow.gate must be an object")
    dependencies = {
        _text(item.get("id"), "workflow.gate.id"): tuple(item.get("needs", ()))
        for item in gates
        if isinstance(item, Mapping)
    }
    remaining = set(order)
    emitted: list[str] = []
    while remaining:
        ready = [name for name in order if name in remaining and set(dependencies[name]) <= set(emitted)]
        if not ready:
            raise RecipeError("workflow DAG cannot be ordered")
        for name in ready:
            emitted.append(name)
            remaining.remove(name)
    return tuple(emitted)


def workflow_dependency_order(value: object, targets: Sequence[str]) -> tuple[str, ...]:
    """Return the stable dependency closure needed to execute ``targets``.

    A DAG does not have a meaningful linear "prefix" when independent branches
    exist.  Selecting ``model_check`` therefore includes snapshot, structure,
    and generation, but it does not accidentally start the independent Spectre
    golden branch.
    """
    ordered = workflow_order(value)
    if not isinstance(value, Mapping) or not isinstance(value.get("gates"), list):
        raise RecipeError("workflow must contain gates before dependencies can be selected")
    dependencies = {
        _text(item.get("id"), "workflow.gate.id"): tuple(item.get("needs", ()))
        for item in value["gates"]
        if isinstance(item, Mapping)
    }
    requested = tuple(targets)
    if not requested:
        raise RecipeError("workflow execution requires at least one target gate")
    unknown = sorted(set(requested) - set(dependencies))
    if unknown:
        raise RecipeError(f"unknown workflow target gate(s): {unknown}")
    selected: set[str] = set()

    def include(name: str) -> None:
        if name in selected:
            return
        for dependency in dependencies[name]:
            include(dependency)
        selected.add(name)

    for target in requested:
        include(target)
    return tuple(name for name in ordered if name in selected)


def _check_acyclic(dependencies: Mapping[str, Sequence[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise RecipeError(f"workflow DAG contains a cycle at {name}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in dependencies[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in dependencies:
        visit(name)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RecipeError(f"{label} must be non-empty text")
    return value


__all__ = ["validate_workflow", "workflow_dependency_order", "workflow_order"]
