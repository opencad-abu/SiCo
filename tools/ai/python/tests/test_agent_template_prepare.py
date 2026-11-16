"""Integrated template preparation must share the circuit adapter's retained bindings."""

from sico.adapters import circuit
from sico.core.tools import ToolRegistry


def test_prepare_is_owned_by_shared_circuit_adapter(tmp_path):
    registry = ToolRegistry()
    circuit.register_circuit(registry, object(), tmp_path)
    names = {entry["name"] for entry in registry.schemas()}
    assert "prepare_circuit_template_plan" in names
    assert "prepare_circuit_template_plan" in circuit.NAMES
