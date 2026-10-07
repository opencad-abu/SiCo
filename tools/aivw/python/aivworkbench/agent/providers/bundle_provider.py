"""Offline response provider for legacy bundle data."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..backend import BaseProvider, ProviderRequest, ProviderResponse, ProviderUnavailable
from ..protocol import ErrorCode, PROTOCOL_VERSION, ProtocolError, decode_jsonl
from ..value_codec import thaw
from .bundle_contract import _load_bundle_json
from .bundle_integrity import _has_symlink_component, import_bundle
from .replay import ReplayProvider, ReplayRecord

class BundleProvider(BaseProvider):
    """Consume legacy response data offline, without executing bundle code."""

    name = "bundle"
    version = "1"

    def __init__(
        self,
        root: str | Path,
        *,
        source_generation: str | None = None,
        template_lock: str | None = None,
        expected_protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        super().__init__()
        raw_root = Path(root).expanduser()
        if _has_symlink_component(raw_root):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "bundle root traverses a symlink")
        try:
            self.root = raw_root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "bundle root cannot be resolved") from exc
        self.validation = import_bundle(
            self.root,
            expected_source_generation=source_generation,
            expected_template_lock=template_lock,
            expected_protocol_version=expected_protocol_version,
        )
        self.source_generation = source_generation or self.validation.source_generation
        self.template_lock = template_lock or self.validation.template_lock
        response_path = next((self.root / name for name in ("response.json", "response.bundle.json", "response.jsonl") if (self.root / name).is_file()), None)
        if response_path is None:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "bundle has no response file")
        if response_path.suffix == ".jsonl":
            try:
                records = decode_jsonl(response_path.read_text(encoding="utf-8").splitlines())
            except (OSError, ProtocolError) as exc:
                raise ProtocolError(ErrorCode.BUNDLE_INVALID, "invalid response JSONL", {"detail": str(exc)}) from exc
            raw_records = [item.to_dict() if hasattr(item, "to_dict") else item for item in records]
        else:
            try:
                value = _load_bundle_json(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ProtocolError(ErrorCode.BUNDLE_INVALID, "invalid response JSON", {"detail": str(exc)}) from exc
            if isinstance(value, Mapping) and isinstance(value.get("records"), list):
                raw_records = value["records"]
            elif isinstance(value, list):
                raw_records = value
            else:
                raw_records = [value]
        self._records = tuple(self._normalize_record(item) for item in raw_records)
        if not self._records:
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "response bundle is empty")
        self._replay = ReplayProvider(self._records)

    def _normalize_record(self, value: object) -> ReplayRecord:
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.BUNDLE_INVALID, "response record must be an object")
        raw = dict(value)
        raw.setdefault("source_generation", self.source_generation or "unknown")
        if self.template_lock is not None:
            raw.setdefault("template_lock", self.template_lock)
        return ReplayRecord.from_mapping(raw)

    def next_action(self, request: ProviderRequest) -> ProviderResponse:
        self._check_interrupt()
        if self.source_generation and request.source_generation != self.source_generation:
            raise ProviderUnavailable(ErrorCode.BUNDLE_SOURCE_MISMATCH.value, "bundle source generation mismatch")
        if self.template_lock not in (None, request.template_lock):
            raise ProviderUnavailable(ErrorCode.BUNDLE_TEMPLATE_MISMATCH.value, "bundle template lock mismatch")
        replayed = self._replay.next_action(request)
        # Replay owns strict fixture parsing and cursor state, but callers
        # selected and qualified this outer Bundle provider.  Preserve the
        # immutable action while recording the actual audited boundary instead
        # of leaking the implementation detail as provider="replay".
        return ProviderResponse(
            action=replayed.action,
            error=replayed.error,
            provider=self.name,
            # The bundle transport is the audited provider boundary, while
            # the replayed response carries the model identity selected by
            # the original qualified run.  Keep both dimensions visible so
            # relocation cannot silently erase model provenance.
            model_id=replayed.model_id or "bundle",
            usage=replayed.usage,
            raw_metadata={
                # Inner metadata is useful provenance, but the outer
                # transport label is authoritative and cannot be spoofed by
                # a response fixture.
                **dict(thaw(replayed.raw_metadata)),
                "transport": "offline_bundle",
            },
        )

    @property
    def exhausted(self) -> bool:
        return self._replay.exhausted

    def export_state(self) -> Mapping[str, Any]:
        return {"replay": dict(self._replay.export_state())}

    def restore_state(self, value: Mapping[str, Any]) -> None:
        super().restore_state(value)
        if set(value) != {"replay"} or not isinstance(value.get("replay"), Mapping):
            raise ProviderUnavailable(ErrorCode.CHECKPOINT_INVALID.value, "bundle provider checkpoint is invalid")
        self._replay.restore_state(value["replay"])


__all__ = ["BundleProvider"]
