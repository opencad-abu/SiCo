"""Shared status, identity and bounded qualification value contracts."""

from __future__ import annotations


from datetime import datetime, timezone

import hashlib

import json




import re


from typing import Any, Mapping


from .context import redact_secrets


from .protocol import (
    ErrorCode,
    ProtocolError,
)

from .value_codec import ensure_json, freeze, thaw







QUALIFICATION_SCHEMA_VERSION = 1


QUALIFICATION_PASS = "PASS"


QUALIFICATION_BLOCKED_PROVIDER = "BLOCKED_PROVIDER"


QUALIFICATION_BLOCKED_INPUT = "BLOCKED_INPUT"


QUALIFICATION_FAIL_REVISION = "FAIL_REVISION"


QUALIFICATION_FAIL = "FAIL"


QUALIFICATION_CONFIG_VALIDATED = "CONFIG_VALIDATED"


QUALIFICATION_STATUSES = frozenset(
    {
        QUALIFICATION_PASS,
        QUALIFICATION_BLOCKED_PROVIDER,
        QUALIFICATION_BLOCKED_INPUT,
        QUALIFICATION_FAIL_REVISION,
        QUALIFICATION_FAIL,
        QUALIFICATION_CONFIG_VALIDATED,
    }
)


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


_GENERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,255}$")


_SECRET_WORD = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|private[_-]?key|session[_-]?token|credential)",
    re.IGNORECASE,
)


_FORBIDDEN_VERDICT_KEYS = frozenset(
    {"verdict", "gate_verdict", "deterministic_verdict", "ai_verdict", "pass_fail"}
)


_MAX_EVIDENCE_BYTES = 256 * 1024


_PROVIDER_FAILURE_CODES = frozenset(
    {
        ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
        ErrorCode.PROVIDER_UNAVAILABLE.value,
        ErrorCode.PROVIDER_DISCONNECTED.value,
        ErrorCode.PROVIDER_TIMEOUT.value,
        ErrorCode.UNKNOWN_PROVIDER_STATE.value,
    }
)


def _canonical(value: object) -> str:
    """Return a strict, deterministic JSON representation."""

    safe = redact_secrets(value)
    ensure_json(safe)
    return json.dumps(
        safe,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _raw_digest(value: object) -> str:
    """Hash validated JSON without applying a second redaction pass.

    ProviderRequest/Response/Action objects are already redacted at their
    construction boundary.  Their audit digest must describe the exact
    serialized object in the report, including non-secret accounting keys
    such as ``tokens``.
    """

    ensure_json(value)
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded(value: object, *, cap: int = _MAX_EVIDENCE_BYTES) -> Any:
    """Redact and bound an observable payload without retaining aliases."""

    safe = redact_secrets(value)
    ensure_json(safe)
    encoded = json.dumps(
        safe,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) <= cap:
        return thaw(freeze(safe))
    # A digest and a fixed marker preserve auditability while preventing a
    # provider response from expanding a qualification record without bound.
    return {
        "_truncated": True,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "bytes": len(encoded.encode("utf-8")),
    }


def _assert_no_forbidden_verdict(value: object, path: str = "value", seen: set[int] | None = None) -> None:
    """Reject provider-owned verdict fields at the qualification boundary."""

    active = set() if seen is None else seen
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification payload contains a cyclic object", {"path": path})
        active.add(identity)
        try:
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_VERDICT_KEYS:
                    raise ProtocolError(
                        ErrorCode.INVALID_ARGUMENTS,
                        "provider payload contains a verdict field",
                        {"path": path + "." + str(key)},
                    )
                _assert_no_forbidden_verdict(child, path + "." + str(key), active)
        finally:
            active.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification payload contains a cyclic object", {"path": path})
        active.add(identity)
        try:
            for index, child in enumerate(value):
                _assert_no_forbidden_verdict(child, "%s[%d]" % (path, index), active)
        finally:
            active.remove(identity)


def _validate_generation(value: str, label: str) -> None:
    if not isinstance(value, str) or not _GENERATION.fullmatch(value):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s is invalid" % label)


def _validate_template_lock(value: str | None, label: str) -> None:
    if value is not None:
        _validate_generation(value, label)


def _safe_generation(value: object) -> str:
    """Return a report-safe generation placeholder for rejected input."""

    return value if isinstance(value, str) and _GENERATION.fullmatch(value) else "invalid"


def _safe_template_lock(value: object) -> str | None:
    """Return a report-safe lock value without re-raising validation errors."""

    return value if value is None or (isinstance(value, str) and _GENERATION.fullmatch(value)) else None


def _provider_identity(provider: object) -> dict[str, Any]:
    """Return a stable, secret-free provider identity record."""

    name = getattr(provider, "name", type(provider).__name__)
    if not isinstance(name, str) or not name:
        name = type(provider).__name__
    version = getattr(provider, "version", None)
    if not isinstance(version, str) or not version:
        version = getattr(provider, "protocol_version", None)
    if not isinstance(version, str) or not version:
        version = "unspecified"
    identity = {
        "name": name[:128],
        "version": version[:128],
        "class": "%s.%s" % (type(provider).__module__, type(provider).__qualname__),
    }
    # Configuration identity is useful for qualification provenance, but raw
    # API keys, endpoints with credentials, and arbitrary object reprs are not.
    config = getattr(provider, "config", None)
    if config is not None and hasattr(config, "endpoint"):
        endpoint = getattr(config, "endpoint", "")
        if isinstance(endpoint, str):
            identity["endpoint_digest"] = _digest({"endpoint": endpoint})
        reference = getattr(provider, "secret_reference", None)
        if isinstance(reference, Mapping):
            # Only the environment-variable reference is part of provenance;
            # the resolved value is intentionally never read here.
            name = reference.get("name")
            if isinstance(name, str):
                identity["secret_reference"] = {"source": "environment", "name": name}
    socket_path = getattr(provider, "socket_path", None)
    if socket_path is not None:
        identity["socket_path_digest"] = _digest({"socket_path": str(socket_path)})
    configured_model_id = getattr(provider, "model_id", None)
    if not isinstance(configured_model_id, str) and config is not None:
        configured_model_id = getattr(config, "model_id", None)
    if isinstance(configured_model_id, str) and configured_model_id:
        identity["model_id"] = configured_model_id[:256]
    return identity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

