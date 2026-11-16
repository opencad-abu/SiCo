"""Session envelope and active-workflow resolution without EDA access."""

from sico.adapters.profiles import session_workflow
from sico.adapters.profiles.tool_profiles import host_scope
from sico.core.contracts.contracts import BoundContext, RunState
from sico.storage.journal.journal import SessionJournal

from cadai.mcp.mcp_definitions import TOOLS


def bound(workspace):
    return BoundContext(
        "instance", "generation", "bound",
        {"cwd": str(workspace), "capabilities": ["assistant_call"]},
    )


def seed_profile(journal, workspace, profile):
    journal.append("codex.tool.profile", {"profile": profile, "source": "session"},
                   RunState(bound(workspace)))


def test_unseeded_session_uses_the_automatic_surface(tmp_path):
    with SessionJournal(tmp_path, "workflow-auto") as journal:
        assert session_workflow.profile_id(journal) == "desktop_compat"
        assert session_workflow.enter(
            journal, "circuit_create", TOOLS, bound(tmp_path))["ok"] is True


def test_circuit_and_simulation_have_distinct_allowed_workflows():
    circuit = session_workflow.allowed_workflows(
        host_scope("circuit", TOOLS), TOOLS)
    assert {"library_create", "circuit_create", "testbench_create"} <= set(circuit)
    assert "simulation_run" not in circuit

    simulation = session_workflow.allowed_workflows(
        host_scope("simulation", TOOLS), TOOLS)
    assert "simulation_run" in simulation
    assert "library_create" not in simulation

    mixed = session_workflow.allowed_workflows(
        host_scope("mixed_design", TOOLS), TOOLS)
    assert {"library_create", "circuit_create", "simulation_run"} <= set(mixed)

    desktop = session_workflow.allowed_workflows(
        host_scope("desktop_context", TOOLS), TOOLS)
    assert "simulation_run" in desktop

    assert session_workflow.operations_for("library_create", TOOLS) == (
        "create_circuit_library",)
    assert "create_circuit_from_plan" in session_workflow.operations_for(
        "circuit_create", TOOLS)


def test_enter_records_a_validated_workflow_and_idle_clears_it(tmp_path):
    with SessionJournal(tmp_path, "workflow") as journal:
        seed_profile(journal, tmp_path, "circuit")
        selected = session_workflow.enter(
            journal, "library_create", TOOLS, bound(tmp_path))
        assert selected["ok"] is True
        assert selected["workflow"] == "library_create"
        assert session_workflow.workflow_id(journal) == "library_create"
        assert selected["allowed_operations"] == ["create_circuit_library"]

        cleared = session_workflow.enter(journal, "idle", TOOLS, bound(tmp_path))
        assert cleared["ok"] is True
        assert cleared["workflow"] is None
        assert session_workflow.workflow_id(journal) is None


def test_enter_rejects_workflows_outside_the_envelope(tmp_path):
    with SessionJournal(tmp_path, "workflow-reject") as journal:
        seed_profile(journal, tmp_path, "simulation")
        rejected = session_workflow.enter(
            journal, "library_create", TOOLS, bound(tmp_path))
        assert rejected["ok"] is False
        assert rejected["code"] == "workflow_not_allowed"
        assert "library_create" not in rejected["allowed_workflows"]
        assert "allowed workflows:" in rejected["message"]
        assert "Omit workflow" in rejected["message"]
        assert session_workflow.workflow_id(journal) is None


def test_enter_suggests_the_closest_allowed_workflow(tmp_path):
    with SessionJournal(tmp_path, "workflow-suggest") as journal:
        seed_profile(journal, tmp_path, "simulation")
        rejected = session_workflow.enter(
            journal, "circuit", TOOLS, bound(tmp_path))
        assert rejected["ok"] is False
        assert rejected["suggestion"] == "circuit_create"
