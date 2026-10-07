"""Schemas for inbound live-model commands and outbound status notifications."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .live_protocol import (
    _VIEW_KIND,
    DEFAULT_DEBOUNCE_MS,
    LIVE_PROTOCOL_VERSION,
    MAX_DEBOUNCE_MS,
    MAX_STATUS_BYTES,
    LiveModelProtocolError,
    _bounded_sequence,
    _copy_checked,
    _finite,
    _identifier,
    _object,
    _require_bounded_text,
    _validate_target,
    _validate_target_view_binding,
    _validate_view,
)

_STATES = frozenset(
    {
        "IDLE",
        "DEBOUNCING",
        "SNAPSHOTTING",
        "GENERATING",
        "VALIDATING",
        "AWAITING_APPROVAL",
        "STALE",
        "STOPPED",
        "ERROR",
    }
)


_EVENTS = frozenset(
    {
        "live_model.start",
        "live_model.source_changed",
        "live_model.status",
        "live_model.stop",
        "live_model.approve_publish",
        "live_model.cancel_publish",
    }
)


_STATUS_PUBLICATIONS = frozenset(
    {
        "DISABLED",
        "HUMAN_APPROVAL_REQUIRED",
        "REFUSED_NO_PUBLISHER",
        "CANCELLED",
    }
)


_STATUS_FIELDS = frozenset(
    {
        "protocol_version",
        "event",
        "session_id",
        "state",
        "publication",
        "last_sequence",
        "event_sequence",
        "active_sequence",
        "source_generation",
        "active_source_generation",
        "recipe_id",
        "through",
        "profile",
        "target",
        "view_identity",
        "result",
        "code",
        "rejected_sequence",
        "discarded_sequence",
        "discarded_source_generation",
    }
)


def parse_live_message(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy one inbound live-model event."""

    if not isinstance(value, Mapping):
        raise LiveModelProtocolError("invalid_message", "live-model message must be an object")
    event = value.get("event")
    if not isinstance(event, str) or event not in _EVENTS:
        raise LiveModelProtocolError("invalid_event", "unsupported live-model event")
    if event == "live_model.start":
        message = _object(
            value,
            event,
            {"event", "recipe_id", "through", "profile", "debounce_ms", "target", "view_identity"},
            {"event", "recipe_id", "through", "profile"},
        )
        for key in ("recipe_id", "through", "profile"):
            _identifier(message[key], key)
        debounce = message.get("debounce_ms", DEFAULT_DEBOUNCE_MS)
        number = _finite(debounce, "debounce_ms")
        if int(number) != number or not 0 <= number <= MAX_DEBOUNCE_MS:
            raise LiveModelProtocolError("invalid_number", "debounce_ms is outside the bounded range")
        message["debounce_ms"] = int(number)
        if "target" in message:
            message["target"] = _validate_target(message["target"], "target")
        if "view_identity" in message:
            message["view_identity"] = _validate_view(message["view_identity"], "view_identity")
        if "target" in message and "view_identity" in message:
            _validate_target_view_binding(message["target"], message["view_identity"])
        return message
    if event == "live_model.source_changed":
        message = _object(
            value,
            event,
            {"event", "sequence", "source_generation", "target", "view_identity", "changed_kind"},
            {"event", "sequence", "source_generation", "target", "view_identity", "changed_kind"},
        )
        sequence = message["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= 2**63 - 1:
            raise LiveModelProtocolError("invalid_sequence", "sequence must be a positive bounded integer")
        _identifier(message["source_generation"], "source_generation", generation=True)
        message["target"] = _validate_target(message["target"], "target")
        message["view_identity"] = _validate_view(message["view_identity"], "view_identity")
        _validate_target_view_binding(message["target"], message["view_identity"])
        changed_kind = message["changed_kind"]
        if not isinstance(changed_kind, str) or changed_kind not in _VIEW_KIND:
            raise LiveModelProtocolError("invalid_view", "changed_kind is unsupported")
        return message
    if event == "live_model.status":
        # ``{"event": ...}`` is the bounded status request sent to the
        # controller.  Rich status notifications carry the protocol/version
        # fields and are validated by ``parse_live_status`` below.
        if set(value) == {"event"}:
            return _object(value, event, {"event"}, {"event"})
        return parse_live_status(value)
    if event in {"live_model.stop", "live_model.cancel_publish"}:
        allowed = {"event"}
        message = _object(value, event, allowed, {"event"})
        return message
    message = _object(value, event, {"event", "approval_id"}, {"event", "approval_id"})
    _identifier(message["approval_id"], "approval_id")
    return message


def parse_live_status(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a controller-to-Virtuoso live status notification.

    Status notifications are deliberately a separate schema from inbound
    control events.  Keeping the two shapes distinct prevents a rich status
    object from being mistaken for a command or silently dropped by the
    stdio transport.
    """
    message = _object(value, "live_model.status", set(_STATUS_FIELDS), {
        "protocol_version", "event", "session_id", "state", "publication", "last_sequence",
    })
    if not isinstance(message["protocol_version"], str) or message["protocol_version"] != LIVE_PROTOCOL_VERSION:
        raise LiveModelProtocolError("invalid_protocol", "unsupported live-model status protocol")
    _identifier(message["session_id"], "session_id")
    if not isinstance(message["state"], str) or message["state"] not in _STATES:
        raise LiveModelProtocolError("invalid_state", "live-model state is unsupported")
    if not isinstance(message["publication"], str) or message["publication"] not in _STATUS_PUBLICATIONS:
        raise LiveModelProtocolError("invalid_publication", "live-model publication state is unsupported")
    for key in ("last_sequence", "event_sequence", "active_sequence", "rejected_sequence", "discarded_sequence"):
        if key in message:
            message[key] = _bounded_sequence(message[key], key)
    for key in (
        "source_generation", "active_source_generation", "discarded_source_generation",
    ):
        if key in message:
            _identifier(message[key], key, generation=True)
    for key in ("recipe_id", "through", "profile"):
        if key in message:
            # These values are identifiers, not arbitrary status text.  Keep
            # the same character policy as inbound start requests so a
            # controller status cannot carry control characters or paths with
            # unbounded syntax.
            _identifier(message[key], key)
    if "code" in message:
        _require_bounded_text(message["code"], "code", 128, nonempty=True, ascii_only=True)
    if "target" in message:
        message["target"] = _validate_target(message["target"], "target")
    if "view_identity" in message:
        message["view_identity"] = _validate_view(message["view_identity"], "view_identity")
    if "target" in message and "view_identity" in message:
        _validate_target_view_binding(message["target"], message["view_identity"])
    if "result" in message:
        if not isinstance(message["result"], Mapping):
            raise LiveModelProtocolError("invalid_payload", "status.result must be an object")
        message["result"] = _copy_checked(dict(message["result"]), key="result")
    encoded = json.dumps(message, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_STATUS_BYTES:
        raise LiveModelProtocolError("status_too_large", "live-model status exceeds the size limit")
    return message
