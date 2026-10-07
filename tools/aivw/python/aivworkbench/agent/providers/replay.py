"""Offline request/response replay provider."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from ..backend import BaseProvider, ProviderRequest, ProviderResponse, ProviderUnavailable
from ..protocol import (
    Action,
    AgentError,
    ErrorCode,
    ProtocolError,
)
from ..value_codec import ensure_json, freeze, reject_verdict_fields, thaw


class _DuplicateReplayKey(ValueError):
    pass


def _reject_replay_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


def _reject_replay_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise _DuplicateReplayKey("duplicate replay field: %s" % key)
        result[key] = item
    return result


@dataclass(frozen=True)
class ReplayRecord:
    request_sha256: str
    response: Mapping[str, Any]
    source_generation: str
    template_lock: str | None = None
    protocol_version: str = "aivw-agent-v1"
    sequence: int | None = None

    def __post_init__(self) -> None:
        # Direct dataclass construction is part of the public fixture API, so
        # it must enforce the same checks as ``from_mapping``.  Otherwise an
        # invalid record could sit dormant until a later turn (after the
        # replay cursor had already moved).
        if not isinstance(self.request_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.request_sha256):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay request hash is invalid")
        if not isinstance(self.response, Mapping):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response must be an object")
        if not isinstance(self.source_generation, str) or not self.source_generation:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay source generation is required")
        if self.protocol_version != "aivw-agent-v1":
            raise ProtocolError(ErrorCode.BUNDLE_PROTOCOL_MISMATCH, "replay protocol version is unsupported")
        if self.template_lock is not None and (
            not isinstance(self.template_lock, str)
            or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", self.template_lock)
        ):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay template lock is invalid")
        if self.sequence is not None and (
            not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0
        ):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay sequence is invalid")
        _validate_response_shape(self.response)
        try:
            ensure_json(self.response)
        except ProtocolError as exc:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response is not JSON-safe", exc.details) from exc
        object.__setattr__(self, "response", freeze(self.response))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReplayRecord":
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay record must be an object")
        allowed = {"request_sha256", "request_digest", "response", "source_generation", "template_lock", "protocol_version", "sequence"}
        # Validate key types before sorting; hostile in-memory mappings must
        # produce a stable protocol error rather than a heterogeneous-key
        # ``TypeError``.
        unknown = sorted(str(key) for key in value if not isinstance(key, str) or key not in allowed)
        if unknown:
            raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "replay record contains unknown fields", {"fields": unknown})
        digest_a = value.get("request_sha256")
        digest_b = value.get("request_digest")
        if digest_a is not None and digest_b is not None and digest_a != digest_b:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay request hash aliases disagree")
        digest = digest_a if digest_a is not None else digest_b
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay request hash is invalid")
        response = value.get("response")
        if not isinstance(response, Mapping):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response must be an object")
        source = value.get("source_generation")
        if not isinstance(source, str) or not source:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay source generation is required")
        protocol = value.get("protocol_version", "aivw-agent-v1")
        if protocol != "aivw-agent-v1":
            raise ProtocolError(ErrorCode.BUNDLE_PROTOCOL_MISMATCH, "replay protocol version is unsupported")
        template_lock = value.get("template_lock")
        if template_lock is not None and (not isinstance(template_lock, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", template_lock)):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay template lock is invalid")
        sequence = value.get("sequence")
        if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay sequence is invalid")
        return cls(digest, freeze(dict(response)), source, template_lock, protocol, sequence)


class ReplayProvider(BaseProvider):
    name = "replay"
    version = "1"

    def __init__(self, records: Iterable[ReplayRecord | Mapping[str, Any]] | str | Path, *, strict_sequence: bool = True) -> None:
        super().__init__()
        if not isinstance(strict_sequence, bool):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay strict_sequence must be boolean")
        if isinstance(records, (str, Path)):
            path = Path(records)
            try:
                raw = json.loads(
                    path.read_text(encoding="utf-8"),
                    parse_constant=_reject_replay_constant,
                    object_pairs_hook=_reject_replay_duplicates,
                )
            except (OSError, ValueError) as exc:
                raise ProtocolError(ErrorCode.BUNDLE_INVALID, "cannot read replay bundle", {"detail": str(exc)}) from exc
            if isinstance(raw, Mapping) and isinstance(raw.get("records"), list):
                unknown = sorted(str(key) for key in raw if key != "records")
                if unknown:
                    raise ProtocolError(
                        ErrorCode.UNKNOWN_FIELD,
                        "replay bundle contains unknown fields",
                        {"fields": unknown},
                    )
                records = raw["records"]
            elif isinstance(raw, list):
                records = raw
            else:
                records = [raw]
        try:
            iterator = iter(records)
        except TypeError as exc:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay records must be iterable") from exc
        normalized: list[ReplayRecord] = []
        for item in iterator:
            normalized.append(item if isinstance(item, ReplayRecord) else ReplayRecord.from_mapping(item))
        self.records = tuple(normalized)
        if not self.records:
            raise ValueError("replay provider requires at least one record")
        self.strict_sequence = strict_sequence
        self._index = 0
        if self.strict_sequence:
            for index, record in enumerate(self.records):
                if record.sequence is not None and record.sequence != index:
                    raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay record sequence is not contiguous", {"index": index, "sequence": record.sequence})

    def next_action(self, request: ProviderRequest) -> ProviderResponse:
        self._check_interrupt()
        if self._index >= len(self.records):
            raise ProviderUnavailable("REPLAY_EXHAUSTED", "replay bundle has no response for this turn")
        record = self.records[self._index]
        expected = request_digest(request)
        if record.request_sha256 != expected:
            raise ProviderUnavailable(ErrorCode.BUNDLE_HASH_MISMATCH.value, "replay request hash mismatch")
        if record.source_generation != request.source_generation:
            raise ProviderUnavailable(ErrorCode.BUNDLE_SOURCE_MISMATCH.value, "replay source generation mismatch")
        if record.template_lock != request.template_lock:
            raise ProviderUnavailable(ErrorCode.BUNDLE_TEMPLATE_MISMATCH.value, "replay template lock mismatch")
        if self.strict_sequence and record.sequence is not None and record.sequence != self._index:
            raise ProviderUnavailable("REPLAY_SEQUENCE_MISMATCH", "replay record sequence mismatch")
        response = record.response
        # A replay record may carry audited provider/model identity and usage
        # metadata.  Preserve those fields instead of silently replacing them
        # with the adapter defaults; otherwise a relocated bundle could pass a
        # qualification gate while losing the actual model provenance.
        response_provider = self.name
        response_model_id = "replay"
        usage: Mapping[str, Any] = {}
        metadata: Mapping[str, Any] = {}
        try:
            if "action" in response:
                action = Action.from_dict(response["action"])
                response_provider = response.get("provider", self.name)
                response_model_id = response.get("model_id", "replay")
                usage = response.get("usage", {})
                metadata = response.get("metadata", {})
            else:
                action = Action.from_dict(response)
            if (
                not isinstance(response_provider, str)
                or not response_provider
                or len(response_provider) > 128
                or not isinstance(response_model_id, str)
                or not response_model_id
                or len(response_model_id) > 256
                or not isinstance(usage, Mapping)
                or not isinstance(metadata, Mapping)
            ):
                raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response identity/metadata is invalid")
            # Re-run the same recursive checks used by ProviderResponse.  The
            # record was validated at load time, but this keeps direct in-memory
            # mutation of a hostile Mapping from crossing the provider boundary.
            reject_verdict_fields(usage, "replay.usage")
            reject_verdict_fields(metadata, "replay.metadata")
            ensure_json(usage, "replay.usage")
            ensure_json(metadata, "replay.metadata")
        except ProtocolError as exc:
            raise ProviderUnavailable(ErrorCode.INVALID_ACTION.value, exc.message) from exc
        # Advance the cursor only after the complete action has been parsed.
        # A malformed response must remain diagnosable and must not consume a
        # record that could otherwise be inspected or replaced offline.
        self._index += 1
        return ProviderResponse.from_action(
            action,
            provider=response_provider,
            model_id=response_model_id,
            usage=usage,
            raw_metadata=metadata,
        )

    @property
    def exhausted(self) -> bool:
        return self._index >= len(self.records)

    def reset(self) -> None:
        self._index = 0

    def export_state(self) -> Mapping[str, Any]:
        return {"index": self._index, "record_count": len(self.records)}

    def restore_state(self, value: Mapping[str, Any]) -> None:
        super().restore_state(value)
        if not isinstance(value, Mapping):
            raise ProviderUnavailable(ErrorCode.CHECKPOINT_INVALID.value, "replay checkpoint must be an object")
        unknown = [key for key in value if not isinstance(key, str) or key not in {"index", "record_count"}]
        if unknown:
            raise ProviderUnavailable(
                ErrorCode.CHECKPOINT_INVALID.value,
                "replay checkpoint has unknown fields: %s" % ", ".join(sorted(str(item) for item in unknown)),
            )
        index = value.get("index")
        record_count = value.get("record_count")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > len(self.records):
            raise ProviderUnavailable(ErrorCode.CHECKPOINT_INVALID.value, "replay checkpoint index is invalid")
        if record_count != len(self.records):
            raise ProviderUnavailable(ErrorCode.CHECKPOINT_INVALID.value, "replay checkpoint record set changed")
        self._index = index

    def export_records(self) -> list[dict[str, Any]]:
        """Return a canonical, redaction-free fixture representation.

        Replay records are already validated and contain no credentials by
        contract; callers can hash or archive this representation offline.
        """
        return [
            {
                "request_sha256": item.request_sha256,
                "response": thaw(item.response),
                "source_generation": item.source_generation,
                "template_lock": item.template_lock,
                "protocol_version": item.protocol_version,
                "sequence": item.sequence,
            }
            for item in self.records
        ]


def request_digest(request: ProviderRequest) -> str:
    payload = request.to_dict()
    # Metadata and budget are part of the request contract: changing a budget
    # must not accidentally reuse a response recorded under another limit.
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# Compatibility for historical offline fixtures.  Production consumers use
# the named domain operation above; remove this alias after stored fixtures
# and external qualification helpers have migrated to ``request_digest``.
_request_digest = request_digest


def _validate_response_shape(response: Mapping[str, Any]) -> None:
    """Validate the narrow response wrapper before a record can be replayed."""
    if not isinstance(response, Mapping):
        raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response must be an object")
    keys = set(response)
    if "action" in response:
        unknown = [key for key in response if not isinstance(key, str) or key not in {"action", "provider", "model_id", "usage", "metadata"}]
        if unknown:
            raise ProtocolError(
                ErrorCode.UNKNOWN_FIELD,
                "replay response contains unknown fields",
                {"fields": sorted(str(item) for item in unknown)},
            )
        if not isinstance(response.get("action"), Mapping):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response.action must be an object")
    else:
        # Bare action records are accepted for compact offline fixtures, but
        # they must at least carry the action contract fields.  Action.from_dict
        # performs the detailed validation when consumed.
        required = {"action_id", "kind", "parent_event_id", "expected_source_generation", "budget", "params"}
        if not required.issubset(keys):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response has no action wrapper or complete action")
    for name in ("provider", "model_id"):
        if name in response and (not isinstance(response[name], str) or not response[name]):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response.%s is invalid" % name)
    for name in ("usage", "metadata"):
        if name in response and not isinstance(response[name], Mapping):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "replay response.%s must be an object" % name)


__all__ = ["ReplayProvider", "ReplayRecord"]
