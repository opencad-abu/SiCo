"""MCP schemas and shared enums for CAD and Maestro workflow tools."""

from __future__ import annotations

from typing import Any

MAX_PATH_CHARS = 4_096
MAX_STRING_CHARS = 256
MAX_VALUE_CHARS = 4_096
MAX_ARRAY_ITEMS = 64
MAX_JOB_ID_CHARS = 128
MAX_MAESTRO_ANALYSES = 32
MAX_MAESTRO_JOB_ID_CHARS = 96
_PATH_PATTERN = r"^/(?:[^\u0000-\u001F\u007F]*[^\s\u0000-\u001F\u007F])?$"

FLOW_NAMES = {
    "run_rce": "rce",
    "run_drc": "drc",
    "run_lvs": "lvs",
    "stream_gds": "gds",
    "export_cdl": "cdl",
    "generate_lef": "lef",
}
STOP_AFTER = {
    "rce": frozenset({"cdl", "gds", "lvs", "query", "extract", "xrc_lvs", "xrc_pdb", "xrc_fmt"}),
    "drc": frozenset({"gds", "drc"}),
    "lvs": frozenset({"cdl", "gds", "lvs"}),
}
RUN_MODES = frozenset({"single", "monte_carlo"})
ANALYSIS_NAMES = frozenset({"dc", "ac", "tran"})
SPEC_NAMES = ("minimum", "maximum", "gt", "lt", "tolerance")
EXPRESSION_OUTPUT_TYPES = frozenset({"point", "corners", "sweeps", "all"})
SIGNAL_OUTPUT_TYPES = frozenset({"terminal", "terminalV", "net"})
OUTPUT_TYPES = EXPRESSION_OUTPUT_TYPES | SIGNAL_OUTPUT_TYPES
CREATE_FIELDS = {
    "setup_library",
    "setup_cell",
    "setup_view",
    "existing_view_policy",
    "test_name",
    "testbench_library",
    "testbench_cell",
    "testbench_view",
    "simulator",
    "analyses",
    "variables",
    "model_files",
    "stimulus_files",
    "outputs",
    "corners",
}

MUTATING_WORKFLOW_TOOL_NAMES = frozenset(
    {
        *FLOW_NAMES,
        "analyze_dspf",
        "create_maestro_testbench",
        "run_maestro_simulation",
        "stop_maestro_simulation",
    }
)
READ_ONLY_WORKFLOW_TOOL_NAMES = frozenset({"get_cad_flow_status", "get_maestro_simulation_status"})
WORKFLOW_TOOL_NAMES = MUTATING_WORKFLOW_TOOL_NAMES | READ_ONLY_WORKFLOW_TOOL_NAMES


def _string(maximum: int = MAX_STRING_CHARS, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": maximum, **extra}


def _path() -> dict[str, Any]:
    return _string(MAX_PATH_CHARS, pattern=_PATH_PATTERN)


def _array(
    items: dict[str, Any], minimum: int = 0, maximum: int = MAX_ARRAY_ITEMS
) -> dict[str, Any]:
    return {
        "type": "array",
        "items": items,
        "minItems": minimum,
        "maxItems": maximum,
    }


def _object(properties: dict[str, Any], *required: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = list(required)
    return result


_PAIR = _object({"name": _string(), "value": _string(MAX_VALUE_CHARS)}, "name", "value")
_ANALYSIS = _object(
    {
        "name": {"type": "string", "enum": sorted(ANALYSIS_NAMES)},
        "enabled": {"type": "boolean", "default": True},
        "options": _array(_PAIR),
    },
    "name",
)
_MODEL_FILE = {
    "oneOf": [
        _path(),
        _object({"path": _path(), "section": _string()}, "path", "section"),
    ]
}
_OUTPUT = _object(
    {
        "name": _string(),
        "output_type": {"type": "string", "enum": sorted(OUTPUT_TYPES)},
        "signal_name": {"type": ["string", "null"], "maxLength": MAX_STRING_CHARS},
        "expression": {"type": ["string", "null"], "maxLength": MAX_VALUE_CHARS},
        "plot": {"type": "boolean", "default": False},
        "save": {"type": "boolean", "default": False},
        "specs": _object({name: _string(MAX_VALUE_CHARS) for name in SPEC_NAMES}),
    },
    "name",
    "output_type",
)
_OUTPUT["oneOf"] = [
    {
        "properties": {
            "output_type": {"enum": sorted(EXPRESSION_OUTPUT_TYPES)},
            "signal_name": {"type": "null"},
            "expression": _string(MAX_VALUE_CHARS),
        },
        "required": ["expression"],
    },
    {
        "properties": {
            "output_type": {"enum": sorted(SIGNAL_OUTPUT_TYPES)},
            "signal_name": _string(MAX_STRING_CHARS),
            "expression": {"type": "null"},
        },
        "required": ["signal_name"],
    },
]
_CORNER = _object(
    {
        "name": _string(),
        "enabled": {"type": "boolean", "default": True},
        "enabled_tests": _array(_string()),
        "disabled_tests": _array(_string()),
        "variables": _array(_PAIR),
    },
    "name",
)


def _flow_schema(flow: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "config_path": _path(),
        "mode": {"type": "string", "enum": ["run", "generate"], "default": "run"},
        "dry_run": {"type": "boolean", "default": False},
    }
    if flow in STOP_AFTER:
        properties["stop_after"] = {"type": "string", "enum": sorted(STOP_AFTER[flow])}
    return _object(properties, "config_path")


def _tool(
    name: str,
    title: str,
    description: str,
    schema: dict[str, Any],
    read_only: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": not read_only,
            "idempotentHint": read_only,
            "openWorldHint": not read_only,
        },
    }


_CREATE_SCHEMA = _object(
    {
        "setup_library": _string(),
        "setup_cell": _string(),
        "setup_view": _string(),
        "existing_view_policy": {
            "type": "string",
            "enum": ["error", "update"],
            "default": "error",
        },
        "test_name": _string(),
        "testbench_library": _string(),
        "testbench_cell": _string(),
        "testbench_view": _string(),
        "simulator": _string(),
        "analyses": _array(_ANALYSIS, 1, MAX_MAESTRO_ANALYSES),
        "variables": _array(_PAIR),
        "model_files": _array(_MODEL_FILE),
        "stimulus_files": _array(_path()),
        "outputs": _array(_OUTPUT),
        "corners": _array(_CORNER),
    },
    "setup_library",
    "setup_cell",
    "setup_view",
    "test_name",
    "testbench_library",
    "testbench_cell",
    "testbench_view",
    "analyses",
)
_RUN_SCHEMA = _object(
    {
        "library": _string(),
        "cell": _string(),
        "view": _string(),
        "run_mode": {
            "type": "string",
            "enum": sorted(RUN_MODES),
            "default": "single",
        },
    },
    "library",
    "cell",
    "view",
)
_JOB_SCHEMA = _object({"job_id": _string(MAX_JOB_ID_CHARS)}, "job_id")
_MAESTRO_JOB_SCHEMA = _object({"job_id": _string(MAX_MAESTRO_JOB_ID_CHARS)}, "job_id")
_FLOW_TITLES = {
    "run_rce": "RC extraction",
    "run_drc": "DRC",
    "run_lvs": "LVS",
    "stream_gds": "GDS stream-out",
    "export_cdl": "CDL export",
    "generate_lef": "LEF generator",
}
_OTHER_TOOLS = (
    (
        "analyze_dspf",
        "Analyze DSPF",
        "Open the asynchronous DSPF Analyzer for an absolute source_path; returns a job_id.",
        _object({"source_path": _path()}, "source_path"),
    ),
    (
        "get_cad_flow_status",
        "Get CAD flow status",
        "Read state, log, and exit status for a CAD workflow job_id without changing the job.",
        _JOB_SCHEMA,
        True,
    ),
    (
        "create_maestro_testbench",
        "Create Maestro testbench",
        "Create or update a Maestro view from an existing schematic testbench and save it.",
        _CREATE_SCHEMA,
    ),
    (
        "run_maestro_simulation",
        "Run Maestro simulation",
        "Start an asynchronous run from an existing Maestro view; returns a simulation job_id.",
        _RUN_SCHEMA,
    ),
    (
        "get_maestro_simulation_status",
        "Get Maestro simulation status",
        "Read progress, checks, and messages for a Maestro simulation job_id.",
        _MAESTRO_JOB_SCHEMA,
        True,
    ),
    (
        "stop_maestro_simulation",
        "Stop or clean up Maestro session",
        "Stop an active run or force-close a session retained by a failed create/run "
        "job_id; keeps its Maestro setup.",
        _MAESTRO_JOB_SCHEMA,
    ),
)
WORKFLOW_TOOLS = [
    *[
        _tool(
            name,
            title,
            f"Start asynchronous {title} from absolute TOML config_path; returns a status job_id.",
            _flow_schema(FLOW_NAMES[name]),
        )
        for name, title in _FLOW_TITLES.items()
    ],
    *[_tool(*definition) for definition in _OTHER_TOOLS],
]
