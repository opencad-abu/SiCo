"""Tool profile declarations: one eligible snapshot per session scope."""

import pytest

from cadai.mcp import mcp_definitions
from cadai.workflows.project_workflow.project_schema import OPERATIONS, operation_tool
from cadai.mcp.task_group_schema import validate as validate_groups
from cadai.mcp.tool_exposure import ExposureError, allowed_operations, snapshot
from cadai.workflows.knowledge.tool_profile_schema import ProfileDeclarationError, merge, validate


def row(name):
    return {
        "name": name,
        "description": name + " does something",
        "inputSchema": {"type": "object", "properties": {}, "required": [],
                        "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }


CATALOG = {name: row(name) for name in
           ("alpha", "beta", "gamma", "delta", "live_x")}
GROUPS = validate_groups([
    {
        "id": "pair_read",
        "owner": "cadai",
        "status": "draft",
        "purpose": "read two",
        "keywords": [],
        "tools": ["alpha", "beta"],
        "subgroups": [],
        "conditions": [{"when": "需要", "tools": ["gamma"]}],
        "recovery": ["beta"],
        "connections": [],
    },
    {
        "id": "live_ops",
        "owner": "sico",
        "status": "draft",
        "purpose": "host-only",
        "keywords": [],
        "tools": ["live_x", "delta"],
        "subgroups": [],
        "conditions": [],
        "recovery": [],
        "connections": [],
    },
])


def availability(name):
    if name == "live_x":
        return {"state": "unavailable", "reason": "host never supports it"}
    return {"state": "available"}


DECLARATIONS = validate([
    {
        "id": "scope_a",
        "owner": "cadai",
        "status": "draft",
        "purpose": "no live tools",
        "scope": "declared",
        "tools": ["delta"],
        "groups": ["pair_read"],
        "defaults": ["pair_read"],
    },
    {
        "id": "scope_host",
        "owner": "sico",
        "status": "draft",
        "purpose": "everything the host supports",
        "scope": "host",
        "tools": [],
        "groups": [],
        "defaults": [],
    },
], groups=GROUPS)


def test_declared_scope_resolves_groups_and_filters_host_facts():
    resolved = snapshot("scope_a", declarations=DECLARATIONS, groups=GROUPS,
                        catalog=CATALOG, availability=availability)
    assert resolved["tools"] == ("alpha", "beta", "gamma", "delta")
    assert resolved["required"] == ("alpha", "beta", "delta")
    assert resolved["conditional"] == ("gamma",)
    assert resolved["recovery"] == ("beta",)
    assert resolved["defaults"] == ("pair_read",)
    assert resolved["missing"] == []


def test_unavailable_and_unregistered_members_are_reported_not_granted():
    declarations = validate([
        {
            "id": "scope_live",
            "owner": "sico",
            "status": "draft",
            "purpose": "declares an unsupported tool",
            "scope": "declared",
            "tools": ["live_x", "ghost"],
            "groups": [],
            "defaults": [],
        },
    ], groups=GROUPS)
    resolved = snapshot("scope_live", declarations=declarations, groups=GROUPS,
                        catalog=CATALOG, availability=availability)
    assert resolved["tools"] == ()
    reasons = {(entry["name"], entry["state"]) for entry in resolved["missing"]}
    assert reasons == {("ghost", "unregistered"), ("live_x", "unavailable")}
    assert all(entry.get("reason") for entry in resolved["missing"])


def test_host_scope_is_the_registered_minus_host_unsupported():
    resolved = snapshot("scope_host", declarations=DECLARATIONS, groups=GROUPS,
                        catalog=CATALOG, availability=availability)
    assert resolved["tools"] == ("alpha", "beta", "gamma", "delta")
    assert resolved["missing"] == [{"name": "live_x", "state": "unavailable",
                                    "reason": "host never supports it"}]


def test_snapshot_is_deterministic_and_plain_data():
    first = snapshot("scope_a", declarations=DECLARATIONS, groups=GROUPS,
                     catalog=CATALOG, availability=availability)
    second = snapshot("scope_a", declarations=DECLARATIONS, groups=GROUPS,
                      catalog=CATALOG, availability=availability)
    assert first == second and first["digest"] == second["digest"]
    assert isinstance(first["tools"], tuple) and isinstance(first["missing"], list)
    with pytest.raises(ExposureError):
        snapshot("nope", declarations=DECLARATIONS, groups=GROUPS, catalog=CATALOG)


def test_narrow_task_profiles_include_the_engineering_report_tools():
    from sico.adapters.profiles.tool_profiles import PROFILES

    required = {"set_copilot_stage", "update_copilot_stage", "list_copilot_data",
                "publish_copilot_report", "prepare_copilot_audit"}
    for profile in ("circuit", "simulation"):
        assert required <= set(PROFILES[profile]["tools"])
    # Planning is created in the circuit profile; simulation selects the
    # already-planned stages to stay within its frozen byte budget.
    assert "plan_copilot_stages" in PROFILES["circuit"]["tools"]
    assert "plan_copilot_stages" in PROFILES["mixed_design"]["tools"]


def test_unknown_groups_and_shape_errors_are_rejected():
    base = {
        "id": "bad", "owner": "sico", "status": "draft", "purpose": "x",
        "scope": "declared", "tools": [], "groups": [], "defaults": [],
    }
    for change in (
        {"groups": ["missing"]},
        {"defaults": ["pair_read"]},
        {"scope": "everything"},
        {"status": "final"},
        {"extra": True},
        {"tools": [1]},
    ):
        with pytest.raises(ProfileDeclarationError):
            validate([{**base, **change}], groups=GROUPS)
    with pytest.raises(ProfileDeclarationError, match="scope=host"):
        validate([{**base, "id": "hosty", "scope": "host", "tools": ["alpha"]}],
                 groups=GROUPS)
    with pytest.raises(ProfileDeclarationError, match="duplicate"):
        merge([base, base], groups=GROUPS)
    with pytest.raises(ProfileDeclarationError, match="unknown fields"):
        validate([{**base, "bogus": 1}], groups=GROUPS)


def test_journalled_operations_need_their_target_tool_inside_the_snapshot():
    operations = {operation: operation_tool(operation) for operation in OPERATIONS}
    circuit_like = set(allowed_operations({"execute_circuit_operation",
                                           "create_circuit_from_plan"}, operations))
    assert "create_circuit_from_plan" in circuit_like
    assert "apply_cdf_update" not in circuit_like
    maintenance_like = set(allowed_operations({"execute_circuit_operation",
                                               "apply_cdf_update"}, operations))
    assert "apply_cdf_update" in maintenance_like
    assert allowed_operations({"alpha"}, operations) == ()


# --- the real SiCo host declarations -----------------------------------------

def host_catalog():
    """Canonical definitions plus one row per host-registered extra tool."""
    from sico.adapters.profiles.task_groups import TASK_GROUPS
    from sico.adapters.profiles.tool_profiles import PROFILES

    catalog = {tool["name"]: tool for tool in mcp_definitions.TOOLS}
    declared = {name for profile in PROFILES.values() for name in profile["tools"]}
    for group in TASK_GROUPS.values():
        members = [*group["tools"], *group.get("recovery", [])]
        members += [member for condition in group.get("conditions", [])
                    for member in condition["tools"]]
        declared |= {member if isinstance(member, str) else member["tool"]
                     for member in members}
    for name in sorted(declared - set(catalog)):
        catalog[name] = row(name)
    return catalog


def host_snapshot(profile_id):
    from sico.adapters.profiles.task_groups import TASK_GROUPS
    from sico.adapters.profiles.tool_profiles import PROFILES, host_availability

    catalog = host_catalog()
    registered = set(catalog)
    return snapshot(profile_id, declarations=PROFILES, groups=TASK_GROUPS,
                    catalog=catalog,
                    availability=lambda name: host_availability(name, registered))


def test_host_profiles_resolve_with_the_core_scope_in_every_task_profile():
    from sico.adapters.profiles.tool_profiles import PROFILES

    assert {"desktop_compat", "desktop_context", "desktop_core", "circuit", "simulation",
            "pdk_maintenance", "physical_flow", "verification",
            "mixed_design", "adaptive"} <= set(PROFILES)
    core = set(host_snapshot("desktop_core")["tools"])
    assert "get_circuit_operation" in core and "execute_circuit_operation" not in core
    for profile in ("circuit", "simulation", "pdk_maintenance", "physical_flow",
                    "adaptive"):
        resolved = host_snapshot(profile)
        assert core <= set(resolved["tools"]), profile
        assert resolved["missing"] == [], profile


def test_mixed_design_unions_the_design_families_without_maintenance():
    from sico.adapters.profiles.tool_profiles import host_scope

    mixed = host_scope("mixed_design", list(host_catalog().values()))
    tools = set(mixed["tools"])
    assert {
        "create_circuit_library", "create_circuit_from_plan",
        "run_simulation_setup", "run_drc", "run_lvs", "get_pdk_data",
    } <= tools
    assert "prepare_pdk_data" not in tools
    assert "apply_pdk_data_update" not in tools
    assert "run_aivw_recipe" not in tools
    assert {
        "create_circuit_library", "create_circuit_from_plan",
        "run_simulation_setup",
    } <= set(mixed["operations"])


def test_adaptive_unions_design_and_maintenance_without_verification():
    from sico.adapters.profiles.tool_profiles import host_scope

    adaptive = host_scope("adaptive", list(host_catalog().values()))
    tools = set(adaptive["tools"])
    assert {
        "create_circuit_library", "create_circuit_from_plan",
        "run_simulation_setup", "run_drc", "run_lvs",
        "get_pdk_data", "prepare_pdk_data", "advance_pdk_collection",
        "apply_pdk_data_update", "publish_pdk_data",
    } <= tools
    assert "run_aivw_recipe" not in tools
    assert "submit_candidate" not in tools
    assert {
        "create_circuit_library", "create_circuit_from_plan",
        "run_simulation_setup",
    } <= set(adaptive["operations"])


def test_product_surface_reaches_every_registered_flow():
    from sico.adapters.profiles.tool_profiles import host_availability, host_scope
    from sico.adapters.profiles.profile_setting import PRODUCT_DEFAULT
    from sico.adapters.profiles.desktop_compat import FROZEN_DESKTOP_COMPAT_TOOLS

    catalog = host_catalog()
    registered = set(catalog)
    product = host_scope(PRODUCT_DEFAULT, list(catalog.values()))
    assert PRODUCT_DEFAULT == "desktop_compat"
    assert set(product["tools"]) == set(FROZEN_DESKTOP_COMPAT_TOOLS)
    assert "simulation_job" not in product["tools"]
    assert {
        "create_simulation_setup", "run_simulation_setup",
        "inspect_simulation_setup", "get_simulation_recipe_status",
    } <= set(product["tools"])
    assert all(host_availability(name, registered)["state"] == "available"
               for name in product["tools"])


def test_simple_cdf_comparison_needs_no_profile_or_workflow():
    from sico.adapters.profiles.session_workflow import operations_for
    from sico.adapters.profiles.tool_profiles import host_scope
    from sico.adapters.profiles.profile_setting import PRODUCT_DEFAULT

    catalog = list(host_catalog().values())
    product = host_scope(PRODUCT_DEFAULT, catalog)
    assert {
        "inspect_cdf", "inspect_schematic", "inspect_symbol_ports",
        "get_pdk_data", "bind_pdk_device", "prepare_pdk_data", "mcp_help",
    } <= set(product["tools"])
    assert operations_for(None, catalog) == ()


def test_compat_and_verification_follow_the_host_facts():
    compat = host_snapshot("desktop_compat")
    assert compat["missing"] == []
    assert "live_model_status" not in compat["tools"]

    verification = set(host_snapshot("verification")["tools"])
    assert "eval_skill" not in verification
    assert {"run_aivw_recipe", "inspect_schematic"} <= verification
    assert "check_skill" not in verification
    assert not any(name.startswith("live_model_") for name in verification)
    from sico.adapters.profiles.tool_profiles import PROFILES

    declared = set(PROFILES["verification"]["tools"])
    assert declared - verification == {"live_model_status", "live_model_events"}


def test_desktop_context_is_the_frozen_surface_plus_context_tools():
    from sico.adapters.profiles.desktop_compat import FROZEN_DESKTOP_COMPAT_TOOLS
    from sico.adapters.profiles.tool_profiles import PROFILES

    tools = PROFILES["desktop_context"]["tools"]
    assert tools[:len(FROZEN_DESKTOP_COMPAT_TOOLS)] == list(FROZEN_DESKTOP_COMPAT_TOOLS)
    assert tools[len(FROZEN_DESKTOP_COMPAT_TOOLS):] == [
        "inspect_selection", "inspect_object", "inspect_hierarchy",
    ]
    assert "inspect_selection" not in PROFILES["desktop_compat"]["tools"]
    assert "inspect_object" not in PROFILES["desktop_compat"]["tools"]
    assert "inspect_hierarchy" not in PROFILES["desktop_compat"]["tools"]


def test_new_host_tool_does_not_widen_the_frozen_compat_surface():
    from sico.adapters.profiles.tool_profiles import host_scope

    catalog = host_catalog()
    before = host_scope("desktop_compat", list(catalog.values()))
    catalog["future_tool"] = row("future_tool")
    after = host_scope("desktop_compat", list(catalog.values()))
    assert "future_tool" not in after["tools"]
    assert after["tools"] == before["tools"]
    assert after["bytes"] == before["bytes"]


def test_circuit_and_physical_scopes_keep_their_own_families():
    circuit = set(host_snapshot("circuit")["tools"])
    assert {"create_circuit_from_plan", "prepare_circuit_edit", "draw_symbol",
            "prepare_template_circuit"} <= circuit
    assert "prepare_pdk_data" not in circuit and "run_drc" not in circuit
    # 标准门槛要求的 write/execution.order 只能经 get_pdk_data 读到：
    # 绑定入口所在档位必须同时能读已发布规则，否则绑定只能靠猜。
    assert "bind_pdk_device" in circuit and "get_pdk_data" in circuit
    # CDF hooks/callbacks are a consume-time fact: the design scope must be
    # able to discover installed/built-in support without opening maintenance.
    assert "list_project_extensions" in circuit
    assert "resolve_pdk_baseline" in circuit

    physical = set(host_snapshot("physical_flow")["tools"])
    assert {"run_drc", "run_lvs", "stream_gds"} <= physical
    assert "create_circuit_from_plan" not in physical

    simulation = set(host_snapshot("simulation")["tools"])
    assert {"create_simulation_setup", "run_simulation_setup",
            "measure_waveform_pair", "bind_result_context",
            "simulation_job"} <= simulation
    assert "create_circuit_from_plan" not in simulation
    assert "bind_pdk_device" in simulation and "get_pdk_data" in simulation

    maintenance = set(host_snapshot("pdk_maintenance")["tools"])
    assert {"prepare_pdk_data", "apply_cdf_update"} <= maintenance
    assert "get_pdk_data" in maintenance
    assert "list_project_extensions" in maintenance
    assert "resolve_pdk_baseline" in maintenance
    assert "list_project_extensions" in set(host_snapshot("mixed_design")["tools"])
    assert "resolve_pdk_baseline" in set(host_snapshot("mixed_design")["tools"])
    ops = {operation: operation_tool(operation) for operation in OPERATIONS}
    assert "apply_cdf_update" in allowed_operations(maintenance, ops)


def test_every_write_capable_scope_reaches_the_project_preflight():
    # The controlled write entry (execute_circuit_operation) refuses to dispatch
    # until a successful project preflight covered its exact source, targets and
    # directories. A group or profile that carries the write entry without the
    # preflight tool dead-ends its own sanctioned flow (observed live in the
    # simulation profile), so both layers must carry the prerequisite.
    from sico.adapters.profiles.task_groups import TASK_GROUPS

    for group in TASK_GROUPS.values():
        writes = [row for condition in group.get("conditions", [])
                  for row in condition["tools"]
                  if isinstance(row, dict)
                  and row.get("tool") == "execute_circuit_operation"]
        if writes:
            assert "preflight_circuit_project" in group["tools"], group["id"]
    for profile in ("circuit", "simulation", "pdk_maintenance"):
        tools = set(host_snapshot(profile)["tools"])
        if "execute_circuit_operation" in tools:
            assert "preflight_circuit_project" in tools, profile


def test_task_profiles_keep_the_recovery_members_of_their_groups():
    # Group recovery members are part of the scope, so switching the default
    # group inside a profile can never drop an undecided operation's recovery.
    circuit = set(host_snapshot("circuit")["tools"])
    assert {"get_circuit_operation", "inspect_created_circuit"} <= circuit
    assert "get_circuit_operation" in host_snapshot("circuit")["recovery"]
    simulation = set(host_snapshot("simulation")["tools"])
    assert "simulation_job" in simulation
    assert "get_simulation_recipe_status" in host_snapshot("simulation")["recovery"]
    assert {"get_simulation_recipe_status", "stop_simulation_recipe",
            "release_simulation_setup"} <= simulation


def test_selection_inspection_belongs_only_to_the_context_profile():
    from sico.adapters.profiles.tool_profiles import profile_includes

    assert profile_includes("desktop_context", "inspect_selection")
    assert profile_includes("desktop_context", "inspect_hierarchy")
    assert not profile_includes("desktop_compat", "inspect_selection")
    assert not profile_includes("desktop_compat", "inspect_hierarchy")
    assert not profile_includes("no_such_profile", "inspect_selection")
