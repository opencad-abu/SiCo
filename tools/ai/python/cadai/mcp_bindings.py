"""Explicit MCP composition: bind domain handlers to their minimum capabilities.

This module owns the execution registration. The server lists and calls these
records directly; there is no second name-to-route switch or fallback dispatch.
Resource factories preserve lazy ownership in the server without handing a
handler the server or its mutable state containers.
"""

from functools import partial

from .aivw_tool import AivwArgumentError
from .candidate_ledger import CandidateLedgerError
from .cdf_tools import CDF_NAMES
from .cdf_update import CDF_UPDATE_NAMES
from .circuit_create import CREATE_NAMES
from .circuit_edit import EDIT_NAMES
from .circuit_generic import GENERIC_NAMES
from .circuit_library import LIBRARY_NAMES
from .circuit_tools import LIVE_CIRCUIT_TOOLS, CircuitArgumentError
from .entry_context import NAMES as ENTRY_NAMES
from .entry_context import call_context
from .inspection import READ_ONLY_INSPECTION_TOOLS, InspectionArgumentError
from .mcp_aivw_handler import dispatch_aivw
from .mcp_candidate_handler import (
    CANDIDATE_NAMES,
    CandidateHandlerArgumentError,
    dispatch_candidate,
)
from .mcp_cdf_handler import CdfHandlerArgumentError, dispatch_cdf
from .mcp_creation_handler import CreationHandlerArgumentError, dispatch_creation
from .mcp_definitions import TOOLS_BY_NAME
from .mcp_edit_handler import EditHandlerArgumentError, dispatch_edit
from .mcp_inspection_handler import dispatch_inspection
from .mcp_library_handler import LibraryHandlerArgumentError, dispatch_library
from .mcp_live_handler import LIVE_NAMES, LiveHandlerArgumentError, dispatch_live
from .mcp_measurement_handler import MEASUREMENT_NAMES, dispatch_measurement
from .mcp_pdk_handler import PdkHandlerArgumentError, dispatch_pdk
from .mcp_project_handler import ProjectHandlerArgumentError, dispatch_project
from .mcp_recipe_handler import dispatch_catalog, dispatch_preview, dispatch_recipe
from .mcp_registry import bind_tools
from .mcp_resource_handler import RESOURCE_NAMES, ResourceHandlerArgumentError, dispatch_resource
from .mcp_simulation_handler import SimulationHandlerArgumentError, dispatch_simulation
from .mcp_snapshot_handler import SNAPSHOT_NAMES, dispatch_snapshot
from .mcp_template_handler import (
    TEMPLATE_CATALOG_NAMES,
    TemplateHandlerArgumentError,
    dispatch_template,
)
from .mcp_workflow_handler import dispatch_workflow
from .pdk_binding_schema import BINDING_NAMES
from .pdk_schema import PDK_NAMES
from .project_schema import PROJECT_NAMES
from .simulation_recipe import SIMULATION_NAMES
from .skill_diagnostics import record_output
from .template_circuit import TOOL_NAME as TEMPLATE_CIRCUIT_NAME
from .template_placement import TOOL_NAMES as TEMPLATE_PLACEMENT_NAMES
from .template_prepare import TOOL_NAME as TEMPLATE_PREPARE_NAME
from .template_symbol import TOOL_NAMES as TEMPLATE_SYMBOL_NAMES
from .workflow_tools import WORKFLOW_TOOL_NAMES, WorkflowArgumentError


def create_bindings(*, client, workspace, skill_reference, get_pdk_session,
                    get_pdk_bindings, get_snapshot_store, get_candidate_ledger):
    """Return the ordered registration used for both listing and invocation."""
    def entry(name, arguments):
        return call_context(name, arguments, client)

    def project(name, arguments):
        detail = dispatch_project(
            name, arguments, client=client, workspace=workspace,
            definitions=TOOLS_BY_NAME, pdk_bindings=get_pdk_bindings(),
        )
        return detail.get("ok") is True, detail

    def pdk(name, arguments):
        return dispatch_pdk(
            name, arguments, client=client,
            session=get_pdk_session(), bindings=None,
        )

    def binding(name, arguments):
        bindings = None if name == "list_project_extensions" else get_pdk_bindings()
        return dispatch_pdk(name, arguments, client=client, session=None, bindings=bindings)

    def creation(name, arguments):
        return dispatch_creation(
            name, arguments, client=client, workspace=workspace,
            pdk_bindings=get_pdk_bindings(),
        )

    def edit(name, arguments):
        return dispatch_edit(
            name, arguments, client=client, workspace=workspace,
            pdk_bindings=get_pdk_bindings(),
        )

    def template_plan(name, arguments):
        return dispatch_template(
            name, arguments, client=client, workspace=workspace,
            pdk_bindings=(get_pdk_bindings() if "binding_refs" in arguments
                          else get_pdk_bindings(create=False)),
        )

    def bridge(name, arguments):
        ok, detail = client.call(name, arguments)
        # Raw eval/load return arbitrary SKILL values; record without JSON decoding.
        record_output(detail)
        return ok, detail

    resource = partial(dispatch_resource, workspace=workspace, skill_reference=skill_reference)
    return bind_tools(TOOLS_BY_NAME, (
        ({"get_context", "eval_skill", "load_skill_file"}, bridge, (), False),
        ({"Search", "tool_help"}, resource, (ResourceHandlerArgumentError,), True),
        (RESOURCE_NAMES - {"Search", "tool_help"}, resource,
         (ResourceHandlerArgumentError,), False),
        (ENTRY_NAMES, entry, (ValueError, OSError), False),
        (PROJECT_NAMES, project, (ProjectHandlerArgumentError,), False),
        (LIBRARY_NAMES, partial(dispatch_library, client=client, workspace=workspace),
         (LibraryHandlerArgumentError,), False),
        (PDK_NAMES, pdk, (PdkHandlerArgumentError,), False),
        (BINDING_NAMES, binding, (PdkHandlerArgumentError,), False),
        ({"query_device_catalog"}, partial(dispatch_catalog, workspace=workspace),
         (CircuitArgumentError,), False),
        (TEMPLATE_CATALOG_NAMES | TEMPLATE_SYMBOL_NAMES,
         partial(dispatch_template, client=client, workspace=workspace),
         (TemplateHandlerArgumentError,), False),
        (GENERIC_NAMES | TEMPLATE_PLACEMENT_NAMES | {TEMPLATE_CIRCUIT_NAME, TEMPLATE_PREPARE_NAME},
         template_plan, (TemplateHandlerArgumentError,), False),
        (CDF_NAMES | CDF_UPDATE_NAMES, partial(dispatch_cdf, client=client, workspace=workspace),
         (CdfHandlerArgumentError,), False),
        (SIMULATION_NAMES, partial(dispatch_simulation, client=client),
         (SimulationHandlerArgumentError,), False),
        (CREATE_NAMES, creation, (CreationHandlerArgumentError,), False),
        (EDIT_NAMES, edit, (EditHandlerArgumentError,), False),
        ({"preview_rc_circuit", "preview_gpdk_gate"}, dispatch_preview,
         (CircuitArgumentError,), False),
        (MEASUREMENT_NAMES, partial(dispatch_measurement, client=client, workspace=workspace),
         (), False),
        (LIVE_CIRCUIT_TOOLS, partial(dispatch_recipe, client=client),
         (CircuitArgumentError,), False),
        ({"run_aivw_recipe"}, partial(dispatch_aivw, client=client, workspace=workspace,
                                    get_ledger=get_candidate_ledger),
         (AivwArgumentError, CandidateLedgerError), False),
        (CANDIDATE_NAMES, partial(dispatch_candidate, get_ledger=get_candidate_ledger),
         (CandidateHandlerArgumentError,), False),
        (LIVE_NAMES, partial(dispatch_live, client=client), (LiveHandlerArgumentError,), False),
        (READ_ONLY_INSPECTION_TOOLS - ENTRY_NAMES, partial(dispatch_inspection, client=client),
         (InspectionArgumentError,), False),
        (SNAPSHOT_NAMES, partial(dispatch_snapshot, client=client, get_store=get_snapshot_store),
         (), False),
        (WORKFLOW_TOOL_NAMES, partial(dispatch_workflow, client=client),
         (WorkflowArgumentError,), False),
    ))
