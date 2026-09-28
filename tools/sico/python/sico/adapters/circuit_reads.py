"""Declared dependencies of circuit evidence reads during pending decisions."""

# Existence-with-read-effect contract for the evidence a host decision needs.
# Values name the subject each read is based on: a pending decision pauses a
# read only when it resolves that subject. Reading a design never writes it.
EVIDENCE_READS = {
    "inspect_circuit_target": frozenset({"target"}),
    "inspect_created_circuit": frozenset({"target"}),
    "inspect_circuit_config": frozenset({"target"}),
    "inspect_simulation_setup": frozenset({"target"}),
    "inspect_template_symbol": frozenset({"target"}),
    "get_symbol_binding": frozenset({"target"}),
    "get_circuit_operation": frozenset({"target"}),
    "get_simulation_recipe_status": frozenset({"target"}),
    "match_circuit_template": frozenset({"target"}),
    "query_circuit_templates": frozenset(),
    "get_circuit_template": frozenset(),
    "query_device_catalog": frozenset({"pdk"}),
    "search_pdk_devices": frozenset({"pdk"}),
    "get_pdk_device": frozenset({"pdk"}),
    "get_pdk_data": frozenset({"pdk"}),
    "get_pdk_preparation": frozenset({"pdk"}),
    "list_project_extensions": frozenset(),
}


def evidence_dependencies(subject):
    """Fix one read-effect subject per tool; arguments never declare it."""
    def dependencies(arguments, context):
        return subject
    return dependencies
