"""Adapt controller live-model requests and bind recipe results to saved events."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from threading import Event
from typing import Any

from .aivw_tool import AivwArgumentError, AivwExecutionError
from .live_model import LiveModelProtocolError, LiveModelSession
from .protocol import ProtocolError
from .socket_server import RequestFailure
from .transport import TransportClosed, VirtuosoTransport

_SOURCE_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def snapshot_binding_from_aivw(value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract the authoritative, target-bound snapshot record from a manifest.

    The SKILL callback's ``source_generation`` is an optimistic save-event
    token (for example ``save-12``).  AIVW's ``virtuoso.snapshot`` gate emits
    the actual structure digest in its bounded gate outputs.  Keep these
    namespaces separate and refuse to infer a digest from any other field.

    A snapshot hash by itself is insufficient for a live run: the result must
    also identify the exact library/cell/module and saved schematic view that
    produced it.  Returning ``None`` for any malformed or incomplete record
    makes the caller fail closed without guessing which gate or identity was
    intended.
    """
    details = value.get("details")
    if not isinstance(details, Mapping):
        return None
    gates = details.get("gate_results")
    if not isinstance(gates, Mapping):
        return None
    for _gate_id, raw_gate in gates.items():
        if not isinstance(raw_gate, Mapping):
            continue
        executor = raw_gate.get("executor")
        if executor != "virtuoso.snapshot":
            continue
        if raw_gate.get("status") != "PASS":
            continue
        outputs = raw_gate.get("outputs")
        if not isinstance(outputs, Mapping):
            continue
        # The snapshot executor emits one SHA-256 stable digest.  Accept only
        # that canonical field/shape so an unrelated executor output cannot be
        # mistaken for authoritative OA identity.
        candidate = outputs.get("source_generation")
        target = outputs.get("target")
        view_identity = outputs.get("view_identity")
        if not isinstance(candidate, str) or _SOURCE_DIGEST.fullmatch(candidate) is None:
            continue
        if not isinstance(target, Mapping) or not isinstance(view_identity, Mapping):
            continue
        required_target = ("library", "cell", "module")
        required_view = ("library", "cell", "view", "view_type", "kind")
        if any(
            not isinstance(target.get(key), str) or not target.get(key)
            for key in required_target
        ):
            continue
        if any(
            not isinstance(view_identity.get(key), str) or not view_identity.get(key)
            for key in required_view
        ):
            continue
        if view_identity.get("kind") != "schematic":
            continue
        return {
            "source_generation": candidate,
            "target": {key: target[key] for key in required_target},
            "view_identity": {key: view_identity[key] for key in required_view},
        }
    return None


def send_transport_event(transport: VirtuosoTransport, payload: Mapping[str, Any]) -> bool:
    try:
        transport.send(payload)
    except (OSError, ProtocolError, TransportClosed) as exc:
        transport.close(str(exc))
        return False
    return True


def dispatch_live(session: LiveModelSession | None, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if session is None:
        raise RequestFailure("live_unavailable", "live modeling session is unavailable")
    if not isinstance(params, dict):
        raise RequestFailure("invalid_params", "live-model params must be an object")
    try:
        if method == "live_model_start":
            value = dict(params)
            value["event"] = "live_model.start"
            return session.handle(value)
        if method == "live_model_source_changed":
            value = dict(params)
            value["event"] = "live_model.source_changed"
            return session.handle(value)
        if method == "live_model_status":
            if params:
                raise LiveModelProtocolError("unknown_field", "status takes no parameters")
            return session.handle({"event": "live_model.status"})
        if method == "live_model_events":
            limit = params.get("limit", 32)
            if set(params) - {"limit"}:
                raise LiveModelProtocolError("unknown_field", "events accepts only limit")
            return {"events": session.events(limit)}
        if method == "live_model_stop":
            if params:
                raise LiveModelProtocolError("unknown_field", "stop takes no parameters")
            return session.handle({"event": "live_model.stop"})
        if method == "live_model_approve_publish":
            value = dict(params)
            value["event"] = "live_model.approve_publish"
            return session.handle(value)
        if method == "live_model_cancel_publish":
            if params:
                raise LiveModelProtocolError(
                    "unknown_field", "cancel_publish takes no parameters"
                )
            return session.handle({"event": "live_model.cancel_publish"})
    except LiveModelProtocolError as exc:
        raise RequestFailure(exc.code, str(exc)) from exc
    raise RequestFailure("unknown_method", "unsupported live-model method")


def run_live_recipe(
    config: Mapping[str, Any], active: Mapping[str, Any], *,
    run_aivw_recipe, capability, workspace: Path, timeout: float,
    cancel_event: Event, snapshot_binding,
) -> Mapping[str, Any]:
    """Run one bounded recipe closure for the exact saved event."""
    recipe_id = config.get("recipe_id")
    through = config.get("through")
    profile = config.get("profile", "amsverify")
    if not all(isinstance(value, str) for value in (recipe_id, through, profile)):
        raise AivwArgumentError("live session recipe configuration is invalid")
    result = run_aivw_recipe(
        {
            "reference": recipe_id,
            "through": through,
            "profile": profile,
            "timeout": min(float(timeout), 3600.0),
        },
        client=capability,
        workspace=workspace,
        cancel_event=cancel_event,
    )
    aivw = result.get("aivw")
    if not isinstance(aivw, Mapping) or not isinstance(aivw.get("status"), str):
        raise AivwExecutionError("AIVW live result did not contain a bounded status")
    # Bind the run to the callback token, but never present that token
    # as an authoritative OA structure hash.
    result["status"] = str(aivw["status"])
    result["requested_source_generation"] = str(active["source_generation"])
    binding = snapshot_binding(aivw)
    if binding is not None:
        active_target = active.get("target")
        active_view = active.get("view_identity")
        if not isinstance(active_target, Mapping) or not isinstance(active_view, Mapping):
            result["status"] = "BLOCKED_INPUT"
            result["code"] = "snapshot_binding_unavailable"
            result["message"] = "saved event did not include a complete target/view binding"
        else:
            expected_target = {
                key: active_target.get(key) for key in ("library", "cell", "module")
            }
            expected_view = {
                key: active_view.get(key)
                for key in ("library", "cell", "view", "view_type", "kind")
            }
            if (
                binding["target"] != expected_target
                or binding["view_identity"] != expected_view
            ):
                result["status"] = "BLOCKED_INPUT"
                result["code"] = "snapshot_binding_mismatch"
                result["message"] = "AIVW snapshot identity does not match the saved event"
            else:
                snapshot_generation = binding["source_generation"]
                result["snapshot_source_generation"] = snapshot_generation
                result["validated_source_generation"] = snapshot_generation
    else:
        # A PASS without a saved-source snapshot is not publishable or
        # candidate-ready.  Return bounded blocked metadata instead of
        # fabricating a validation generation.
        result["status"] = "BLOCKED_INPUT"
        result["code"] = "snapshot_generation_unavailable"
        result["message"] = "AIVW run did not expose an authoritative snapshot generation"
    return result
