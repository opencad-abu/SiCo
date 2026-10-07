"""Schema, policy, capability and checkpoint validation for tool broker."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from .context import ArtifactLocator
from .policy import AgentPolicy
from .protocol import Action, AgentError, ActionKind, ErrorCode, ProtocolError

_FORBIDDEN_VERDICT_FIELDS = frozenset({"verdict", "gate_verdict", "deterministic_verdict", "ai_verdict", "pass_fail"})
_ACTION_TOOL_DEFAULTS = {
    ActionKind.PLAN_EXPERIMENT.value: "plan_experiment",
    ActionKind.SUBMIT_EXPERIMENT.value: "submit_experiment",
    ActionKind.PROPOSE_MODEL.value: "propose_model",
    ActionKind.REQUEST_REVISION.value: "request_revision",
}

def _validate_schema(schema: Mapping[str, Any], _seen: set[int] | None = None) -> None:
    """Validate the deliberately small JSON-schema subset used by tools."""
    if not isinstance(schema, Mapping):
        raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "tool schema must be an object")
    seen = set() if _seen is None else _seen
    identity = id(schema)
    if identity in seen:
        raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "tool schema contains a cyclic object")
    seen.add(identity)
    try:
        allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "minimum", "maximum"}
        unknown = [key for key in schema if not isinstance(key, str) or key not in allowed]
        if unknown:
            raise ProtocolError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "tool schema contains unknown fields",
                {"fields": sorted(str(item) for item in unknown)},
            )
        if "type" in schema and schema["type"] not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
            raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "unsupported tool schema type")
        if "properties" in schema:
            properties = schema["properties"]
            if not isinstance(properties, Mapping):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema.properties must be an object")
            if any(not isinstance(key, str) for key in properties):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema property names must be text")
            for child in properties.values():
                _validate_schema(child, seen)
        if "required" in schema:
            required = schema["required"]
            if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema.required must be a text array")
            if len(set(required)) != len(required):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema.required contains duplicate names")
            properties = schema.get("properties", {})
            if isinstance(properties, Mapping) and any(item not in properties for item in required):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema.required names an unknown property")
        if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
            raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema.additionalProperties must be boolean")
        for bound in ("minimum", "maximum"):
            if bound in schema and (
                not isinstance(schema[bound], (int, float))
                or isinstance(schema[bound], bool)
                or not math.isfinite(float(schema[bound]))
            ):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema numeric bound is invalid")
        if "minimum" in schema and "maximum" in schema:
            minimum = schema["minimum"]
            maximum = schema["maximum"]
            if (
                isinstance(minimum, (int, float))
                and not isinstance(minimum, bool)
                and isinstance(maximum, (int, float))
                and not isinstance(maximum, bool)
                and minimum > maximum
            ):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema minimum exceeds maximum")
        if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
            raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "schema.enum must be a non-empty array")
        if "enum" in schema:
            _ensure_json_safe(schema["enum"], "schema.enum")
        if "items" in schema:
            _validate_schema(schema["items"], seen)
    finally:
        seen.remove(identity)


def _clone_schema(value: Any, _seen: set[int] | None = None) -> Any:
    """Deep-copy a validated schema so registrations do not retain aliases."""
    seen = set() if _seen is None else _seen
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "tool schema contains a cyclic object")
        seen.add(identity)
        try:
            return {str(key): _clone_schema(child, seen) for key, child in value.items()}
        finally:
            seen.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "tool schema contains a cyclic object")
        seen.add(identity)
        try:
            return [_clone_schema(child, seen) for child in value]
        finally:
            seen.remove(identity)
    raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "tool schema contains a non-JSON value")


def _validate_instance(value: Any, schema: Mapping[str, Any], path: str) -> None:
    if not schema:
        return
    expected = schema.get("type")
    valid = True
    if expected == "object":
        valid = isinstance(value, Mapping)
        if valid:
            properties = schema.get("properties", {})
            required_values = schema.get("required", [])
            required = set(required_values)
            if not isinstance(properties, Mapping) or not isinstance(schema.get("required", []), list):
                raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "invalid object schema")
            missing = sorted(str(item) for item in required - set(value))
            if missing:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool arguments miss required fields", {"fields": missing})
            if schema.get("additionalProperties", True) is False:
                unknown = [key for key in value if not isinstance(key, str) or key not in properties]
                if unknown:
                    raise ProtocolError(
                        ErrorCode.INVALID_ARGUMENTS,
                        "tool arguments contain unknown fields",
                        {"fields": sorted(str(item) for item in unknown)},
                    )
            for key, child in properties.items():
                if key in value:
                    _validate_instance(value[key], child, path + "." + str(key))
    elif expected == "array":
        valid = isinstance(value, list)
        if valid and "items" in schema:
            for index, item in enumerate(value):
                _validate_instance(item, schema["items"], "%s[%d]" % (path, index))
    elif expected == "string":
        valid = isinstance(value, str)
    elif expected == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if valid:
            import math
            valid = math.isfinite(float(value))
    elif expected == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
        if valid and "minimum" in schema:
            valid = value >= schema["minimum"]
        if valid and "maximum" in schema:
            valid = value <= schema["maximum"]
    elif expected == "boolean":
        valid = isinstance(value, bool)
    elif expected == "null":
        valid = value is None
    if not valid:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool argument has wrong type", {"path": path, "expected": expected})
    if "enum" in schema and value not in schema["enum"]:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool argument is outside enum", {"path": path})
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool argument is below minimum", {"path": path})
        if "maximum" in schema and value > schema["maximum"]:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool argument exceeds maximum", {"path": path})


def _reject_result_verdict(value: Any, path: str) -> None:
    _reject_result_verdict_seen(value, path, set())


def _reject_result_verdict_seen(value: Any, path: str, seen: set[int]) -> None:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result contains a cyclic object", {"field": path})
        seen.add(identity)
        try:
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_VERDICT_FIELDS:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result contains a verdict field", {"field": path + "." + str(key)})
                _reject_result_verdict_seen(child, path + "." + str(key), seen)
        finally:
            seen.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result contains a cyclic object", {"field": path})
        seen.add(identity)
        try:
            for index, child in enumerate(value):
                _reject_result_verdict_seen(child, "%s[%d]" % (path, index), seen)
        finally:
            seen.remove(identity)


def _validate_policy_values(value: Any, policy: AgentPolicy, path: str = "params") -> None:
    """Apply path, environment, and network policy to nested tool arguments."""
    _validate_policy_values_seen(value, policy, path, set())


def _validate_policy_values_seen(value: Any, policy: AgentPolicy, path: str, seen: set[int]) -> None:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool arguments contain a cyclic object", {"path": path})
        seen.add(identity)
        try:
            for raw_key, child in value.items():
                key = str(raw_key)
                lowered = key.lower()
                child_path = path + "." + key
                if lowered in {"environment", "env", "environment_vars"}:
                    if not isinstance(child, Mapping):
                        raise ProtocolError(
                            ErrorCode.ENVIRONMENT_DENIED,
                            "tool environment must be an object",
                            {"path": child_path},
                        )
                    policy.sanitize_environment(child)
                    continue
                if lowered in {
                    "path",
                    "file",
                    "file_path",
                    "artifact_path",
                    "output_path",
                    "input_path",
                    "cwd",
                    "workdir",
                    "directory",
                    "root",
                    "working_directory",
                }:
                    if not isinstance(child, (str, Path)):
                        raise ProtocolError(
                            ErrorCode.PATH_DENIED,
                            "tool path must be text",
                            {"path": child_path},
                        )
                    policy.check_path(
                        child,
                        write=lowered in {"output_path", "artifact_path", "workdir", "directory", "root", "working_directory"},
                        must_exist=False,
                    )
                    continue
                if lowered in {"locator", "artifact", "artifact_locator"} and isinstance(
                    child, Mapping
                ):
                    policy.check_locator(ArtifactLocator.from_dict(child), must_exist=False)
                    continue
                if lowered in {"network", "url", "endpoint", "host", "proxy"} and not policy.allow_network:
                    raise ProtocolError(
                        ErrorCode.ENVIRONMENT_DENIED,
                        "network access is disabled",
                        {"path": child_path},
                    )
                if lowered in {
                    "shell",
                    "command",
                    "argv",
                    "exec",
                    "executable",
                    "process",
                    "program",
                    "subprocess",
                    "script",
                    "eval",
                    "code",
                } and not policy.allow_process:
                    raise ProtocolError(
                        ErrorCode.TOOL_NOT_ALLOWED,
                        "process or code execution is disabled",
                        {"path": child_path},
                    )
                _validate_policy_values_seen(child, policy, child_path, seen)
        finally:
            seen.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool arguments contain a cyclic object", {"path": path})
        seen.add(identity)
        try:
            for index, child in enumerate(value):
                _validate_policy_values_seen(child, policy, "%s[%d]" % (path, index), seen)
        finally:
            seen.remove(identity)


def _validate_capabilities(capabilities: frozenset[str]) -> None:
    """Reject malformed capability declarations at registration time."""
    if not isinstance(capabilities, (set, frozenset, tuple, list)):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool capabilities must be a set of names")
    allowed = {
        "read",
        "write",
        "network",
        "process",
        "shell",
        "exec",
        "ipc",
        "cadence",
    }
    invalid = sorted(str(item) for item in capabilities if not isinstance(item, str) or item not in allowed)
    if invalid:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool capability is unknown", {"capabilities": invalid})


def _capability_error(spec: Any, policy: AgentPolicy, tool_name: str) -> AgentError | None:
    """Enforce declared tool capabilities before a handler can run."""
    capabilities = {str(item).lower() for item in spec.capabilities}
    if {"network"} & capabilities and not policy.allow_network:
        return AgentError(
            ErrorCode.TOOL_NOT_ALLOWED.value,
            "tool requires network capability, which is disabled",
            {"tool": tool_name, "capability": "network"},
        )
    if {"process", "shell", "exec"} & capabilities and not policy.allow_process:
        capability = sorted({"process", "shell", "exec"} & capabilities)[0]
        return AgentError(
            ErrorCode.TOOL_NOT_ALLOWED.value,
            "tool requires process capability, which is disabled",
            {"tool": tool_name, "capability": capability},
        )
    if "write" in capabilities and not policy.write_roots:
        return AgentError(
            ErrorCode.TOOL_NOT_ALLOWED.value,
            "tool requires a configured write root",
            {"tool": tool_name, "capability": "write"},
        )
    if "read" in capabilities and not (policy.read_roots or policy.write_roots):
        return AgentError(
            ErrorCode.TOOL_NOT_ALLOWED.value,
            "tool requires a configured read root",
            {"tool": tool_name, "capability": "read"},
        )
    return None


def _tool_for_action(
    action: Action,
    policy: AgentPolicy | None = None,
    registered: Mapping[str, Any] | None = None,
) -> str:
    """Resolve the sole domain tool allowed for an action kind.

    Provider-controlled parameters may carry a redundant ``tool`` field for
    audit readability, but it must exactly match the action's fixed domain.
    This prevents an otherwise allowlisted action from being retargeted to a
    different handler.
    """
    expected = _ACTION_TOOL_DEFAULTS.get(action.kind)
    if expected is None:
        raise ProtocolError(
            ErrorCode.INVALID_ARGUMENTS,
            "action kind has no domain tool",
            {"kind": action.kind},
        )
    requested = action.params.get("tool") if isinstance(action.params, Mapping) else None
    if requested is not None and not isinstance(requested, str):
        raise ProtocolError(
            ErrorCode.INVALID_ARGUMENTS,
            "action tool selector must be text",
            {"received": str(requested)},
        )
    if requested is not None and requested != expected:
        # Preserve the policy error for a completely unknown/denylisted tool;
        # only an allowlisted alternate target is classified as an action
        # schema mismatch.  This distinction keeps authorization failures
        # observable without permitting retargeting.
        if policy is not None and not policy.check_tool(requested).allowed:
            raise ProtocolError(
                ErrorCode.TOOL_NOT_ALLOWED,
                "tool is not allowlisted",
                {"tool": requested},
            )
        # The four built-in domain tools are fixed to their corresponding
        # action kind whenever that canonical owner is registered.  A custom
        # evidence adapter is only usable as a bootstrap fallback when the
        # canonical owner is absent; once the owner is present, providers may
        # not retarget the action to any other handler.
        if registered is not None and expected in registered:
            raise ProtocolError(
                ErrorCode.INVALID_ARGUMENTS,
                "action cannot be retargeted to another domain tool",
                {"expected": expected, "received": requested},
            )
        if registered is not None and requested in registered:
            return requested
        raise ProtocolError(
            ErrorCode.TOOL_NOT_ALLOWED,
            "tool is not allowlisted",
            {"tool": requested},
        )
    return expected


def _validate_result_artifacts(artifacts: Any, policy: AgentPolicy) -> None:
    """Validate output locators against the configured run roots."""
    if not isinstance(artifacts, (tuple, list)):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result artifacts must be a sequence")
    for item in artifacts:
        locator = ArtifactLocator.from_dict(item)
        # A relative locator is still not useful if no controlled artifact
        # root exists.  Fail closed instead of persisting an unresolvable
        # reference that a later consumer might interpret as an absolute path.
        policy.check_locator(locator, must_exist=False)


def _strict_checkpoint_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = [key for key in value if not isinstance(key, str) or key not in allowed]
    if unknown:
        raise ProtocolError(
            ErrorCode.UNKNOWN_FIELD,
            "%s contains unknown fields" % label,
            {"fields": sorted(str(item) for item in unknown)},
        )


def _ensure_json_safe(value: Any, path: str) -> None:
    """Reject non-finite/non-JSON values before they are persisted."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s contains a non-finite number" % path)
        return
    _ensure_json_safe_seen(value, path, set())


def _ensure_json_safe_seen(value: Any, path: str, seen: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s contains a non-finite number" % path)
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s contains a cyclic object" % path)
        seen.add(identity)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s contains a non-text key" % path)
                _ensure_json_safe_seen(child, "%s.%s" % (path, key), seen)
        finally:
            seen.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s contains a cyclic object" % path)
        seen.add(identity)
        try:
            for index, child in enumerate(value):
                _ensure_json_safe_seen(child, "%s[%d]" % (path, index), seen)
        finally:
            seen.remove(identity)
        return
    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s contains a non-JSON value" % path)
