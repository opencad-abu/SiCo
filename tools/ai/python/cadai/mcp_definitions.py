"""Ordered composition of the canonical domain tool schemas.

Entry-context definitions replace the legacy focus-based aliases. No schema is
redeclared here; MCP and the managed-agent adapters consume this same index.
"""

from . import mcp_bridge_schema as bridge
from . import mcp_inspection_schema as inspection
from . import mcp_snapshot_schema as snapshot
from . import mcp_verification_schema as verification
from . import workflow_tools
from .ac_response_schema import RESPONSE_TOOLS
from .ac_schema import AC_TOOLS
from .cdf_tools import CDF_TOOLS
from .cdf_update import CDF_UPDATE_TOOLS
from .circuit_create import CREATE_TOOLS
from .circuit_edit import EDIT_TOOLS
from .circuit_generic import GENERIC_TOOLS
from .circuit_library import LIBRARY_TOOLS
from .circuit_tools import CIRCUIT_TOOLS
from .document_tools import DOCUMENT_TOOLS
from .entry_context import NAMES as ENTRY_NAMES
from .entry_context import TOOLS as ENTRY_TOOLS
from .mcp_live_handler import LIVE_MODEL_TOOLS
from .mcp_registry import build_registry
from .measurement_catalog import CATALOG_TOOLS
from .pdk_binding_schema import BINDING_TOOLS
from .pdk_schema import PDK_TOOLS
from .project_schema import PROJECT_TOOLS
from .result_tools import RESULT_TOOLS
from .search_tools import SEARCH_TOOL
from .simulation_recipe import SIMULATION_TOOLS
from .skill_check_tools import SKILL_CHECK_TOOL
from .skill_reference import SKILL_REFERENCE_TOOLS
from .template_circuit import TOOLS as TEMPLATE_CIRCUIT_TOOLS
from .template_placement import TOOLS as TEMPLATE_PLACEMENT_TOOLS
from .template_prepare import TOOLS as TEMPLATE_PREPARE_TOOLS
from .template_schema import TOOLS as TEMPLATE_TOOLS
from .template_symbol import TOOLS as TEMPLATE_SYMBOL_TOOLS
from .tool_help_schema import TOOL_HELP_TOOL
from .waveform_schema import WAVEFORM_TOOLS
from .waveform_spec_schema import SPEC_TOOLS

_BASE_TOOLS = [
    bridge.GET_CONTEXT,
    verification.RUN_AIVW_RECIPE,
    verification.SUBMIT_CANDIDATE,
    verification.GET_CANDIDATE,
    inspection.INSPECT_SCHEMATIC,
    snapshot.SNAPSHOT_SCHEMATIC,
    snapshot.QUERY_SCHEMATIC,
    snapshot.GET_SCHEMATIC_ITEM,
    snapshot.EXPORT_SCHEMATIC_TEXT,
    inspection.INSPECT_LAYOUT,
    inspection.INSPECT_SYMBOL_PORTS,
    inspection.LIST_LIBRARIES,
    inspection.INSPECT_LIBRARY,
    inspection.INSPECT_CONFIG_BINDING,
    bridge.EVAL_SKILL,
    bridge.LOAD_SKILL_FILE,
    SKILL_CHECK_TOOL, SEARCH_TOOL, TOOL_HELP_TOOL,
]

TOOLS, TOOLS_BY_NAME = build_registry(
    _BASE_TOOLS,
    DOCUMENT_TOOLS, LIVE_MODEL_TOOLS, SKILL_REFERENCE_TOOLS,
    workflow_tools.WORKFLOW_TOOLS, CIRCUIT_TOOLS, RESULT_TOOLS, WAVEFORM_TOOLS,
    SPEC_TOOLS, AC_TOOLS, RESPONSE_TOOLS, CATALOG_TOOLS, GENERIC_TOOLS,
    CREATE_TOOLS, SIMULATION_TOOLS, TEMPLATE_TOOLS, TEMPLATE_SYMBOL_TOOLS,
    TEMPLATE_CIRCUIT_TOOLS, TEMPLATE_PLACEMENT_TOOLS, TEMPLATE_PREPARE_TOOLS, EDIT_TOOLS,
    PDK_TOOLS, BINDING_TOOLS, CDF_TOOLS, CDF_UPDATE_TOOLS, PROJECT_TOOLS,
    LIBRARY_TOOLS,
    replace_names=ENTRY_NAMES,
    replacements=ENTRY_TOOLS,
)
