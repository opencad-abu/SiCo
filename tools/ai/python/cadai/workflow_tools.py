"""Strict MCP contracts for CAD flows and Maestro simulation workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runtime import MAX_EVAL_BYTES
from .workflow_schema import (
    ANALYSIS_NAMES as _ANALYSIS_NAMES,
)
from .workflow_schema import (
    CREATE_FIELDS as _CREATE_FIELDS,
)
from .workflow_schema import (
    EXPRESSION_OUTPUT_TYPES as _EXPRESSION_OUTPUT_TYPES,
)
from .workflow_schema import (
    FLOW_NAMES as _FLOW_NAMES,
)
from .workflow_schema import (
    MAX_ARRAY_ITEMS,
    MAX_JOB_ID_CHARS,
    MAX_MAESTRO_ANALYSES,
    MAX_MAESTRO_JOB_ID_CHARS,
    MAX_PATH_CHARS,
    MAX_STRING_CHARS,
    MAX_VALUE_CHARS,
    MUTATING_WORKFLOW_TOOL_NAMES,
    READ_ONLY_WORKFLOW_TOOL_NAMES,
    WORKFLOW_TOOL_NAMES,
    WORKFLOW_TOOLS,
)
from .workflow_schema import (
    OUTPUT_TYPES as _OUTPUT_TYPES,
)
from .workflow_schema import (
    RUN_MODES as _RUN_MODES,
)
from .workflow_schema import (
    SIGNAL_OUTPUT_TYPES as _SIGNAL_OUTPUT_TYPES,
)
from .workflow_schema import (
    SPEC_NAMES as _SPEC_NAMES,
)
from .workflow_schema import (
    STOP_AFTER as _STOP_AFTER,
)


class WorkflowArgumentError(ValueError):
    """Raised when workflow arguments cannot be represented safely in SKILL."""


def build_workflow_skill(name: str, arguments: dict[str, Any], *, guard_unavailable=False) -> str:
    """Validate one workflow request and build a fixed-shape SKILL call."""
    if not isinstance(arguments, dict):
        raise WorkflowArgumentError("workflow arguments must be an object")
    code = _build_workflow_skill(name, arguments)
    if guard_unavailable:
        function = code.split("(", 1)[0]
        alternatives = (["preview_simulation_recipe", "execute_circuit_operation"]
                        if "maestro" in name else ["get_project_context"])
        unavailable = json.dumps(dict(
            ok=False, code="capability_not_available", function=function,
            message="Legacy workflow backend is not loaded in this Virtuoso process",
            alternatives=alternatives,
        ), separators=(",", ":"))
        code = f"if(isCallable('{function}) then {code} else {_skill_string(unavailable)})"
    if len(code.encode("utf-8")) > MAX_EVAL_BYTES:
        raise WorkflowArgumentError(
            f"workflow SKILL exceeds {MAX_EVAL_BYTES} UTF-8 bytes"
        )
    return code


def _build_workflow_skill(name: str, arguments: dict[str, Any]) -> str:
    if name in _FLOW_NAMES:
        return _build_flow(name, arguments)
    if name == "analyze_dspf":
        _only(arguments, {"source_path"})
        return _call("aiWorkflowAnalyzeDspf", _path(arguments, "source_path"))
    if name == "get_cad_flow_status":
        return _status("aiWorkflowStatus", arguments)
    if name == "create_maestro_testbench":
        return _build_maestro(arguments)
    if name == "run_maestro_simulation":
        _only(arguments, {"library", "cell", "view", "run_mode"})
        mode = _enum(arguments, "run_mode", _RUN_MODES, "single")
        return _call(
            "aiMaestroRun",
            _required(arguments, "library"),
            _required(arguments, "cell"),
            _required(arguments, "view"),
            _skill_string(mode),
        )
    if name == "get_maestro_simulation_status":
        return _status("aiMaestroStatus", arguments, MAX_MAESTRO_JOB_ID_CHARS)
    if name == "stop_maestro_simulation":
        return _status("aiMaestroStop", arguments, MAX_MAESTRO_JOB_ID_CHARS)
    raise WorkflowArgumentError(f"unsupported workflow tool: {name}")


def _build_flow(name: str, arguments: dict[str, Any]) -> str:
    flow = _FLOW_NAMES[name]
    allowed = {"config_path", "mode", "dry_run"}
    if flow in _STOP_AFTER:
        allowed.add("stop_after")
    _only(arguments, allowed)
    mode = _enum(arguments, "mode", frozenset({"run", "generate"}), "run")
    dry_run = _bool(arguments, "dry_run", False)
    stop_after = _optional(arguments, "stop_after")
    if stop_after is not None and stop_after not in _STOP_AFTER[flow]:
        raise WorkflowArgumentError(f"stop_after is not valid for {flow}: {stop_after}")
    if flow == "rce" and mode == "generate" and (dry_run or stop_after is not None):
        raise WorkflowArgumentError("RCE generate does not accept dry_run or stop_after")
    return _call(
        "aiWorkflowStart",
        _skill_string(flow),
        _path(arguments, "config_path"),
        _skill_string(mode),
        "t" if dry_run else "nil",
        _skill_string(stop_after) if stop_after is not None else "nil",
    )


def _build_maestro(arguments: dict[str, Any]) -> str:
    _only(arguments, _CREATE_FIELDS)
    policy = _enum(
        arguments,
        "existing_view_policy",
        frozenset({"error", "update"}),
        "error",
    )
    analyses = _items(
        arguments,
        "analyses",
        objects=True,
        required=True,
        minimum=1,
        maximum=MAX_MAESTRO_ANALYSES,
    )
    variables = _items(arguments, "variables", objects=True)
    models = _items(arguments, "model_files")
    stimuli = _items(arguments, "stimulus_files")
    outputs = _items(arguments, "outputs", objects=True)
    corners = _items(arguments, "corners", objects=True)
    return _call(
        "aiMaestroCreate",
        *(_required(arguments, key) for key in ("setup_library", "setup_cell", "setup_view")),
        _skill_string(policy),
        *(
            _required(arguments, key)
            for key in (
                "test_name",
                "testbench_library",
                "testbench_cell",
                "testbench_view",
            )
        ),
        _skill_string(_text(arguments.get("simulator", "spectre"), "simulator")),
        _skill_list(_analysis(item, index) for index, item in enumerate(analyses)),
        _skill_list(_pair(item, f"variables[{index}]") for index, item in enumerate(variables)),
        _skill_list(_model(item, index) for index, item in enumerate(models)),
        _skill_list(
            _skill_string(_absolute(item, f"stimulus_files[{index}]"))
            for index, item in enumerate(stimuli)
        ),
        _skill_list(_output(item, index) for index, item in enumerate(outputs)),
        _skill_list(_corner(item, index) for index, item in enumerate(corners)),
    )


def _analysis(item: dict[str, Any], index: int) -> str:
    owner = f"analyses[{index}]"
    _only(item, {"name", "enabled", "options"}, owner)
    name = _enum(item, "name", _ANALYSIS_NAMES, owner=owner)
    options = _items(item, "options", owner, objects=True)
    return _skill_values(
        _skill_string(name),
        "t" if _bool(item, "enabled", True, owner) else "nil",
        _skill_list(_pair(value, f"{owner}.options[{i}]") for i, value in enumerate(options)),
    )


def _pair(item: dict[str, Any], owner: str) -> str:
    _only(item, {"name", "value"}, owner)
    return _skill_values(
        _required(item, "name", owner=owner),
        _required(item, "value", MAX_VALUE_CHARS, owner),
    )


def _model(item: Any, index: int) -> str:
    owner = f"model_files[{index}]"
    if isinstance(item, str):
        return _skill_string(_absolute(item, owner))
    if not isinstance(item, dict):
        raise WorkflowArgumentError(f"{owner} must be an absolute path or path/section object")
    _only(item, {"path", "section"}, owner)
    return _skill_values(
        _skill_string(_absolute(item.get("path"), f"{owner}.path")),
        _required(item, "section", owner=owner),
    )


def _output(item: dict[str, Any], index: int) -> str:
    owner = f"outputs[{index}]"
    _only(
        item,
        {"name", "output_type", "signal_name", "expression", "plot", "save", "specs"},
        owner,
    )
    kind = _enum(item, "output_type", _OUTPUT_TYPES, owner=owner)
    signal = _optional(item, "signal_name", owner, MAX_STRING_CHARS)
    expression = _optional(item, "expression", owner, MAX_VALUE_CHARS)
    if kind in _SIGNAL_OUTPUT_TYPES and (signal is None or expression is not None):
        raise WorkflowArgumentError(f"{owner} {kind} output requires only signal_name")
    if kind in _EXPRESSION_OUTPUT_TYPES and (expression is None or signal is not None):
        raise WorkflowArgumentError(f"{owner} {kind} output requires only expression")
    specs = item.get("specs", {})
    if not isinstance(specs, dict):
        raise WorkflowArgumentError(f"{owner}.specs must be an object")
    _only(specs, set(_SPEC_NAMES), f"{owner}.specs")
    spec_list = _skill_list(
        _skill_values(
            _skill_string(key),
            _required(specs, key, MAX_VALUE_CHARS, f"{owner}.specs"),
        )
        for key in _SPEC_NAMES
        if key in specs
    )
    return _skill_values(
        _required(item, "name", owner=owner),
        _skill_string(kind),
        _skill_string(signal) if signal is not None else "nil",
        _skill_string(expression) if expression is not None else "nil",
        "t" if _bool(item, "plot", False, owner) else "nil",
        "t" if _bool(item, "save", False, owner) else "nil",
        spec_list,
    )


def _corner(item: dict[str, Any], index: int) -> str:
    owner = f"corners[{index}]"
    _only(item, {"name", "enabled", "enabled_tests", "disabled_tests", "variables"}, owner)
    enabled = [
        _text(value, f"{owner}.enabled_tests[{i}]")
        for i, value in enumerate(_items(item, "enabled_tests", owner))
    ]
    disabled = [
        _text(value, f"{owner}.disabled_tests[{i}]")
        for i, value in enumerate(_items(item, "disabled_tests", owner))
    ]
    overlap = set(enabled) & set(disabled)
    if overlap:
        test = sorted(overlap)[0]
        raise WorkflowArgumentError(f"{owner} enables and disables the same test: {test}")
    variables = _items(item, "variables", owner, objects=True)
    return _skill_values(
        _required(item, "name", owner=owner),
        "t" if _bool(item, "enabled", True, owner) else "nil",
        _skill_list(map(_skill_string, enabled)),
        _skill_list(map(_skill_string, disabled)),
        _skill_list(
            _pair(value, f"{owner}.variables[{i}]") for i, value in enumerate(variables)
        ),
    )


def decode_workflow_result(detail: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Compatibility entry; remove after external workflow callers migrate."""
    from .skill_result import decode_skill_result

    return decode_skill_result(detail, context="workflow")


def _items(
    source: dict[str, Any],
    key: str,
    owner: str = "",
    *,
    objects: bool = False,
    required: bool = False,
    minimum: int = 0,
    maximum: int = MAX_ARRAY_ITEMS,
) -> list[Any]:
    label = f"{owner}.{key}" if owner else key
    if key not in source:
        if required:
            raise WorkflowArgumentError(f"{label} is required")
        return []
    value = source[key]
    if not isinstance(value, list):
        raise WorkflowArgumentError(f"{label} must be an array")
    if not minimum <= len(value) <= maximum:
        raise WorkflowArgumentError(
            f"{label} must contain between {minimum} and {maximum} items"
        )
    if objects and any(not isinstance(item, dict) for item in value):
        raise WorkflowArgumentError(f"{key} items must be objects")
    return value


def _text(value: Any, label: str, maximum: int = MAX_STRING_CHARS) -> str:
    if not isinstance(value, str):
        raise WorkflowArgumentError(f"{label} must be a string")
    if not value or value != value.strip():
        raise WorkflowArgumentError(f"{label} must be a non-empty trimmed string")
    if len(value) > maximum:
        raise WorkflowArgumentError(f"{label} exceeds {maximum} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise WorkflowArgumentError(f"{label} contains a control character")
    return value


def _absolute(value: Any, label: str) -> str:
    text = _text(value, label, MAX_PATH_CHARS)
    if not Path(text).is_absolute():
        raise WorkflowArgumentError(f"{label} must be an absolute path")
    return text


def _path(source: dict[str, Any], key: str) -> str:
    if key not in source:
        raise WorkflowArgumentError(f"{key} is required")
    return _skill_string(_absolute(source[key], key))


def _required(
    source: dict[str, Any],
    key: str,
    maximum: int = MAX_STRING_CHARS,
    owner: str = "",
) -> str:
    label = f"{owner}.{key}" if owner else key
    if key not in source:
        raise WorkflowArgumentError(f"{label} is required")
    return _skill_string(_text(source[key], label, maximum))


def _optional(
    source: dict[str, Any],
    key: str,
    owner: str = "",
    maximum: int = MAX_STRING_CHARS,
) -> str | None:
    label = f"{owner}.{key}" if owner else key
    value = source.get(key)
    return None if value is None else _text(value, label, maximum)


def _bool(source: dict[str, Any], key: str, default: bool, owner: str = "") -> bool:
    value = source.get(key, default)
    if not isinstance(value, bool):
        label = f"{owner}.{key}" if owner else key
        raise WorkflowArgumentError(f"{label} must be a boolean")
    return value


def _enum(
    source: dict[str, Any],
    key: str,
    allowed: frozenset[str],
    default: str | None = None,
    owner: str = "",
) -> str:
    label = f"{owner}.{key}" if owner else key
    if key not in source and default is None:
        raise WorkflowArgumentError(f"{label} is required")
    value = source.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise WorkflowArgumentError(f"{label} must be one of: {', '.join(sorted(allowed))}")
    return value


def _only(source: dict[str, Any], allowed: set[str], owner: str = "arguments") -> None:
    unknown = set(source) - allowed
    if unknown:
        raise WorkflowArgumentError(
            f"{owner} contains unsupported field(s): {', '.join(sorted(unknown))}"
        )


def _status(
    function: str, arguments: dict[str, Any], maximum: int = MAX_JOB_ID_CHARS
) -> str:
    _only(arguments, {"job_id"})
    return _call(function, _required(arguments, "job_id", maximum))


def _skill_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _skill_list(values: Any) -> str:
    items = tuple(values)
    return "nil" if not items else f"list({' '.join(items)})"


def _skill_values(*values: str) -> str:
    return _skill_list(values)


def _call(function: str, *arguments: str) -> str:
    return f"{function}({' '.join(arguments)})"


__all__ = [
    "MAX_ARRAY_ITEMS",
    "MAX_JOB_ID_CHARS",
    "MAX_PATH_CHARS",
    "MAX_STRING_CHARS",
    "MAX_VALUE_CHARS",
    "MUTATING_WORKFLOW_TOOL_NAMES",
    "READ_ONLY_WORKFLOW_TOOL_NAMES",
    "WORKFLOW_TOOL_NAMES",
    "WORKFLOW_TOOLS",
    "WorkflowArgumentError",
    "build_workflow_skill",
    "decode_workflow_result",
]
