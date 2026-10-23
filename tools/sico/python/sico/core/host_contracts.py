"""Runtime-free host and session identity values shared across package layers."""

from __future__ import annotations

import hashlib
import json

from .contracts import identifier

HOST_FIELDS = frozenset({"instance_id", "generation", "bridge_id", "router_id"})
SESSION_ADDRESS_FIELDS = frozenset({"project_id", "service_id", "session_id", "runtime_id"})


def digest(value):
    """Return the stable JSON digest used by durable host references."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def host_identity(descriptor):
    """Copy and validate the four opaque values that identify a host bridge."""
    if not isinstance(descriptor, dict) or not HOST_FIELDS <= set(descriptor):
        raise ValueError("Invalid host identity")
    result = {key: descriptor[key] for key in HOST_FIELDS}
    for value in result.values():
        identifier(value)
    return result


def session_address_record(value, *, project_id=None, service_id=None,
                           session_id=None, require_runtime=False):
    """Validate a persisted session address without importing service DTOs.

    The result is a detached value copy.  Service code may turn it into its
    richer ``SessionAddress`` object at the application boundary.
    """
    if not isinstance(value, dict) or set(value) != SESSION_ADDRESS_FIELDS:
        raise ValueError("Invalid session address")
    result = dict(value)
    for key in ("project_id", "service_id", "session_id"):
        identifier(result[key])
    runtime = result["runtime_id"]
    if runtime is not None:
        identifier(runtime)
    if require_runtime and runtime is None:
        raise ValueError("A live session address is required")
    if project_id is not None and result["project_id"] != project_id:
        raise ValueError("Session address belongs to another project")
    if service_id is not None and result["service_id"] != service_id:
        raise ValueError("Session address belongs to another service")
    if session_id is not None and result["session_id"] != session_id:
        raise ValueError("Session address belongs to another session")
    return result
