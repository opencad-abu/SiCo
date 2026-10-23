"""SiCo host tool-profile declarations.

Interactive product sessions use the host-supported registered catalog as one
automatic envelope.  ``desktop_compat`` keeps its historical id but now means
"all registered tools this host can execute"; new flow tools become available
without adding or selecting a profile.  Task groups describe flow discovery
and active-workflow routing, not user authorization.  The narrower profiles
remain explicit surfaces for CI, batch jobs and fault isolation; they are not
selected by a project setting.  ``adaptive`` remains an explicit subset and is
no longer the product default.  ``verification`` keeps its explicit whitelist
(host-unsupported live_model members are reported, never silently granted).
Group references come from the merged task-group table; resolution lives in
``cadai.tool_exposure``.
"""

from __future__ import annotations

from cadai.agent_profile import VERIFICATION_TOOL_NAMES
from cadai.mcp_live_handler import LIVE_NAMES as LIVE_MODEL_NAMES
from cadai.project_schema import OPERATIONS, operation_tool
from cadai.tool_exposure import allowed_operations, snapshot
from cadai.tool_profile_schema import merge

from .task_groups import TASK_GROUPS
from .terminal import TERMINAL_TOOL_NAMES

_CORE_GROUPS = ["virtuoso_ui", "cds_reference"]
_CORE_TOOLS = [
    "get_entry_context",
    "list_libraries",
    "inspect_library",
    "mcp_help",
    "list_project_documents",
    "read_project_document",
    "search_project_documents",
    "get_circuit_operation",
]

HOST_PROFILES = [
    {
        "id": "desktop_compat",
        "owner": "sico",
        "status": "draft",
        "purpose": "交互会话自动全能力面：当前宿主可实现的全部已注册工具（含历史迁移兼容）",
        "scope": "host",
        "tools": [],
        "groups": [],
        "defaults": [],
    },
    {
        "id": "desktop_core",
        "owner": "sico",
        "status": "draft",
        "purpose": "工程/窗口/库只读、SKILL 参考与执行、帮助、工程文档与只读恢复",
        "scope": "declared",
        "tools": _CORE_TOOLS,
        "groups": _CORE_GROUPS,
        "defaults": ["virtuoso_ui", "cds_reference"],
    },
    {
        "id": "circuit",
        "owner": "sico",
        "status": "draft",
        "purpose": "core + 电路/模板/symbol 创建编辑与 PDK 消费审阅",
        "scope": "declared",
        "tools": [
            *_CORE_TOOLS,
            "inspect_layout",
            "preflight_circuit_project",
            "prepare_library",
            "create_circuit_library",
            "create_circuit_from_plan",
            "prepare_circuit_edit",
            "execute_circuit_edit",
            "create_circuit_config",
            "inspect_circuit_config",
            "project_circuit_template",
            "extract_circuit_templates",
            "query_circuit_templates",
            "get_circuit_template",
            "match_circuit_template",
            "prepare_template_circuit",
            "preview_template_placement",
            "prepare_circuit_template_plan",
            "create_template_symbol",
            "draw_symbol",
            "list_project_extensions",
            "resolve_pdk_baseline",
            "execute_circuit_operation",
            "set_copilot_stage",
            "plan_copilot_stages",
            "update_copilot_stage",
            "list_copilot_data",
            "publish_copilot_report",
            "prepare_copilot_audit",
        ],
        "groups": [*_CORE_GROUPS, "schematic_read", "pdk_consume", "symbol_prepare",
                   "circuit_create", "cdf_edit"],
        "defaults": ["circuit_create", "symbol_prepare", "schematic_read"],
    },
    {
        "id": "simulation",
        "owner": "sico",
        "status": "draft",
        "purpose": "core + setup/运行/状态/停止、结果绑定、波形测量与审阅",
        "scope": "declared",
        "tools": [
            *_CORE_TOOLS,
            "create_simulation_setup",
            "run_simulation_setup",
            "create_maestro_testbench",
            "run_maestro_simulation",
            "get_maestro_simulation_status",
            "stop_maestro_simulation",
            "open_design_view",
            "inspect_design_context",
            "release_design_context",
            "inspect_schematic",
            "snapshot_schematic",
            "query_schematic",
            "query_circuit_templates",
            "get_circuit_template",
            "read_maestro_waveform",
            "measure_waveform",
            "query_waveform",
            "measure_waveform_pair",
            "evaluate_waveform_specs",
            "query_waveform_spec_report",
            "read_maestro_ac_waveform",
            "query_ac_waveform",
            "measure_ac_waveform",
            "measure_ac_transfer",
            "measure_ac_response",
            "query_ac_response",
            "list_measurement_capabilities",
            "get_measurement_capability",
            "execute_circuit_operation",
            "set_copilot_stage",
            "update_copilot_stage",
            "list_copilot_data",
            "publish_copilot_report",
            "prepare_copilot_audit",
        ],
        "groups": [*_CORE_GROUPS, "testbench_create", "simulation_run", "results_analyze"],
        "defaults": ["simulation_run", "results_analyze", "testbench_create"],
    },
    {
        "id": "pdk_maintenance",
        "owner": "sico",
        "status": "draft",
        "purpose": "core + 标准采集/CDF/事实/发布与维护审阅",
        "scope": "declared",
        "tools": [
            *_CORE_TOOLS,
            "prepare_pdk_data",
            "advance_pdk_collection",
            "cancel_pdk_collection",
            "get_pdk_data",
            "collect_pdk_data",
            "collect_pdk_parameter_facts",
            "get_pdk_fact_report",
            "get_pdk_source_impact",
            "import_pdk_facts",
            "get_pas_dpt",
            "publish_pdk_data",
            "prepare_pdk_data_update",
            "apply_pdk_data_update",
            "prepare_pdk_baseline_update",
            "apply_pdk_baseline_update",
            "prepare_pdk_cdf_validation",
            "advance_pdk_cdf_validation",
            "get_pdk_cdf_validation",
            "cancel_pdk_cdf_validation",
            "finalize_pdk_cdf_validation",
            "prepare_pdk_parameter_draft",
            "get_pdk_parameter_draft",
            "submit_pdk_parameter_batch",
            "cancel_pdk_parameter_draft",
            "probe_cdf_parameters",
            "get_cdf_probe",
            "prepare_cdf_update",
            "apply_cdf_update",
            "list_project_extensions",
            "resolve_pdk_baseline",
            "execute_circuit_operation",
        ],
        "groups": [*_CORE_GROUPS, "pdk_consume", "cdf_edit"],
        "defaults": ["pdk_consume"],
    },
    {
        "id": "physical_flow",
        "owner": "sico",
        "status": "draft",
        "purpose": "core + DRC/LVS/RC/流片格式与作业生命周期",
        "scope": "declared",
        "tools": [
            *_CORE_TOOLS,
            "run_rce",
            "run_drc",
            "run_lvs",
            "stream_gds",
            "export_cdl",
            "generate_lef",
            "analyze_dspf",
            "get_cad_flow_status",
        ],
        "groups": _CORE_GROUPS,
        "defaults": [],
    },
    {
        "id": "verification",
        "owner": "sico",
        "status": "draft",
        "purpose": "既有显式白名单：不因 core 含 eval 而继承 eval，不自行扩权",
        "scope": "declared",
        "tools": sorted(VERIFICATION_TOOL_NAMES),
        "groups": [],
        "defaults": [],
    },
]

# Merged declarations for one host, then the design and adaptive envelopes
# derived from the dedicated family scopes. Keeping both unions derived avoids
# a second hand-maintained tool list.
_BASE_PROFILES = merge(HOST_PROFILES, groups=TASK_GROUPS)
_MIXED_PROFILE = {
    "id": "mixed_design",
    "owner": "sico",
    "status": "draft",
    "purpose": "混合设计：电路/仿真/版图 + PDK 消费；不含 PDK 维护与 verification",
    "scope": "declared",
    "tools": list(dict.fromkeys([
        *_BASE_PROFILES["circuit"]["tools"],
        *_BASE_PROFILES["simulation"]["tools"],
        *_BASE_PROFILES["physical_flow"]["tools"],
    ])),
    "groups": list(dict.fromkeys([
        *_BASE_PROFILES["circuit"]["groups"],
        *_BASE_PROFILES["simulation"]["groups"],
        *_BASE_PROFILES["physical_flow"]["groups"],
        "library_create",
    ])),
    "defaults": ["circuit_create", "simulation_run"],
}
_DESIGN_PROFILES = merge([*HOST_PROFILES, _MIXED_PROFILE], groups=TASK_GROUPS)
_ADAPTIVE_PROFILE = {
    "id": "adaptive",
    "owner": "sico",
    "status": "draft",
    "purpose": "可选设计+PDK 维护窄面；产品会话不再默认使用",
    "scope": "declared",
    "tools": list(dict.fromkeys([
        *_DESIGN_PROFILES["mixed_design"]["tools"],
        *_BASE_PROFILES["pdk_maintenance"]["tools"],
    ])),
    "groups": list(dict.fromkeys([
        *_DESIGN_PROFILES["mixed_design"]["groups"],
        *_BASE_PROFILES["pdk_maintenance"]["groups"],
    ])),
    "defaults": list(_DESIGN_PROFILES["mixed_design"]["defaults"]),
}
# One validated profile map for this host.
PROFILES = merge([*HOST_PROFILES, _MIXED_PROFILE, _ADAPTIVE_PROFILE],
                 groups=TASK_GROUPS)


def host_availability(name: str, registered) -> dict:
    """Facts about this host, never a second capability list.

    The SiCo bridge does not proxy live modeling sessions, so those tools are
    unavailable here (the controller host keeps its own real rules), and a
    tool the registry does not carry is unavailable by registration.
    """
    if name in LIVE_MODEL_NAMES:
        return {"state": "unavailable", "reason": "SiCo 桥不代理控制器实时建模会话"}
    if name in registered:
        return {"state": "available"}
    return {"state": "unavailable", "reason": "宿主未注册该工具"}


def host_scope(profile_id: str, schemas) -> dict:
    """Resolve one profile against the registry's own advertised rows.

    The snapshot is the single source for the bridge's list and dispatch
    checks and for the Codex ``enabled_tools`` filter; ``operations`` carries
    the journalled operations whose target tool is inside the scope.
    """
    catalog = {row["name"]: row for row in schemas}
    registered = set(catalog)
    resolved = snapshot(profile_id, declarations=PROFILES, groups=TASK_GROUPS,
                        catalog=catalog,
                        availability=lambda name: host_availability(name, registered))
    terminal_tools = sorted(TERMINAL_TOOL_NAMES & registered)
    if terminal_tools:
        resolved = {**resolved,
                    "tools": list(dict.fromkeys([*resolved["tools"], *terminal_tools]))}
    operations = {operation: operation_tool(operation) for operation in OPERATIONS}
    return {**resolved, "operations": allowed_operations(resolved["tools"], operations)}

__all__ = ["HOST_PROFILES", "PROFILES", "host_availability", "host_scope"]
