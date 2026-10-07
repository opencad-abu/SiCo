"""Bound and paginate immutable provider context snapshots."""

from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
from typing import Any, Iterable, Mapping
from .protocol import ErrorCode, ProtocolError
from .redaction import redact as redact_secrets
from .context_values import canonical_json, freeze_json, thaw_json
from .context_page import ContextPage


MIN_CONTEXT_BYTES = 2


PAGE_TRANSPORT_OVERHEAD_BYTES = 512


@dataclass(frozen=True)
class BoundedContext:
    """An immutable, redacted context snapshot with hard byte/item caps."""

    value: Mapping[str, Any]
    max_bytes: int = 64 * 1024
    max_items: int = 256
    _encoded: str = field(init=False, repr=False)
    truncated: bool = field(init=False)

    def __post_init__(self) -> None:
        # ``{}`` is the smallest valid JSON object and is also the smallest
        # useful provider context.  Reject an impossible byte cap explicitly
        # instead of returning a value that violates the advertised limit.
        if (
            not isinstance(self.max_bytes, int)
            or isinstance(self.max_bytes, bool)
            or not isinstance(self.max_items, int)
            or isinstance(self.max_items, bool)
            or self.max_bytes < MIN_CONTEXT_BYTES
            or self.max_items <= 0
        ):
            raise ProtocolError(ErrorCode.CONTEXT_LIMIT_EXCEEDED, "context limits must be positive")
        if not isinstance(self.value, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context root must be an object")
        try:
            redacted = redact_secrets(self.value)
        except ProtocolError:
            raise
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context could not be normalized", {"detail": str(exc)}) from exc
        if not isinstance(redacted, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context root must be an object")
        try:
            limited, truncated = _limit_value(redacted, self.max_bytes, self.max_items)
        except ProtocolError:
            raise
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context could not be bounded", {"detail": str(exc)}) from exc
        encoded = canonical_json(limited)
        if len(encoded.encode("utf-8")) > self.max_bytes:
            # A single scalar may still be too large after structural limits.
            marker = {"_truncated": True, "_reason": "hard_cap"}
            limited = marker if len(canonical_json(marker).encode("utf-8")) <= self.max_bytes else {}
            encoded = canonical_json(limited)
            truncated = True
        frozen = freeze_json(limited)
        if not isinstance(frozen, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context root must be an object")
        # Recompute from the immutable copy so ``bytes_used`` and ``digest``
        # describe the exact object exposed to callers.
        encoded = canonical_json(frozen)
        if len(encoded.encode("utf-8")) > self.max_bytes:
            raise ProtocolError(ErrorCode.CONTEXT_LIMIT_EXCEEDED, "context exceeds hard byte cap")
        object.__setattr__(self, "value", frozen)
        object.__setattr__(self, "_encoded", encoded)
        object.__setattr__(self, "truncated", bool(truncated))

    @property
    def bytes_used(self) -> int:
        return len(self._encoded.encode("utf-8"))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._encoded.encode("utf-8")).hexdigest()

    @property
    def page_transport_cap(self) -> int:
        """Maximum encoded size of one page, including page metadata."""
        return self.max_bytes + PAGE_TRANSPORT_OVERHEAD_BYTES

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": thaw_json(self.value),
            "bytes_used": self.bytes_used,
            "max_bytes": self.max_bytes,
            "max_items": self.max_items,
            "truncated": self.truncated,
            "sha256": self.digest,
        }

    def pages(self, *, page_size: int = 32) -> tuple[ContextPage, ...]:
        if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size <= 0:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "page_size must be positive")
        items: list[Mapping[str, Any]] = []
        if isinstance(self.value, Mapping):
            # Context keys are normally strings, but a defensive cast keeps a
            # hostile in-memory Mapping from leaking a heterogeneous-key
            # TypeError through pagination.
            for key in sorted(self.value, key=lambda item: str(item)):
                items.append({str(key): self.value[key]})
        total = max(1, (len(items) + page_size - 1) // page_size)
        pages: list[ContextPage] = []
        page_cap = self.page_transport_cap
        for index in range(total):
            chunk_items = items[index * page_size : (index + 1) * page_size]
            # A page is independently transportable.  Do not let a large
            # single field bypass the context hard cap merely because the
            # enclosing context was truncated earlier.  The page payload is
            # represented as an array to keep ordering deterministic.
            bounded: list[Mapping[str, Any]] = []
            truncated = self.truncated
            for item in chunk_items:
                candidate = bounded + [item]
                if len(canonical_json(candidate).encode("utf-8")) <= self.max_bytes:
                    bounded.append(item)
                    continue
                truncated = True
                break
            # The truncation marker is useful only when it fits inside the
            # advertised context payload cap.  For very small caps (for
            # example max_bytes=2 or 3), even the marker would violate the
            # contract; an empty page is the only valid representation.
            marker: Mapping[str, Any] = {"_truncated": True}
            marker_size = len(canonical_json(marker).encode("utf-8"))
            if truncated and marker_size <= self.max_bytes:
                bounded.append(marker)
            encoded_size = len(canonical_json(bounded).encode("utf-8"))
            # A partially accumulated list can still be over the cap when a
            # caller supplied a page size of one and the item itself is
            # larger than max_bytes.  Drop payload items until the encoded
            # array is within the hard limit; the truncated bit preserves the
            # fact that information was discarded.
            while encoded_size > self.max_bytes and bounded:
                bounded.pop()
                truncated = True
                encoded_size = len(canonical_json(bounded).encode("utf-8"))
            chunk = tuple(bounded)
            encoded_size = len(canonical_json(chunk).encode("utf-8"))
            page = ContextPage(index, total, chunk, encoded_size, truncated)
            # ``ContextPage.to_dict`` adds metadata; enforce the separate
            # transport cap on the complete encoded object as a final guard.
            if page.transport_bytes > page_cap:
                # A page with metadata cannot fit below this minimum; retain
                # an empty, explicitly truncated page rather than violating
                # the advertised cap.
                empty_marker = (marker,) if marker_size <= self.max_bytes else ()
                empty_size = len(canonical_json(empty_marker).encode("utf-8"))
                page = ContextPage(index, total, empty_marker, empty_size, True)
                # A pathological page count could make index/total metadata
                # exceed the transport allowance even for an empty payload.
                # Keep the page representation valid and bounded; callers
                # still receive the deterministic index/total fields.
                if page.transport_bytes > page_cap and empty_marker:
                    page = ContextPage(index, total, (), 2, True)
            pages.append(page)
        return tuple(pages)

    def page(self, index: int, *, page_size: int = 32) -> ContextPage:
        pages = self.pages(page_size=page_size)
        if index < 0 or index >= len(pages):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context page index is out of range", {"index": index})
        return pages[index]


def build_context(
    fragments: Iterable[Mapping[str, Any] | object],
    *,
    max_bytes: int = 64 * 1024,
    max_items: int = 256,
) -> BoundedContext:
    merged: dict[str, Any] = {}
    for index, fragment in enumerate(fragments):
        if isinstance(fragment, Mapping):
            merged.update(dict(fragment))
        else:
            merged["fragment_%d" % index] = fragment
    return BoundedContext(merged, max_bytes=max_bytes, max_items=max_items)


def _limit_value(value: Any, max_bytes: int, max_items: int) -> tuple[Any, bool]:
    counter = [0]

    def walk(item: Any, path: str) -> tuple[Any, bool]:
        counter[0] += 1
        if counter[0] > max_items:
            return "[TRUNCATED]", True
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            truncated = False
            for key in sorted(item, key=lambda raw_key: str(raw_key)):
                candidate, was_truncated = walk(item[key], path + "." + str(key))
                result[str(key)] = candidate
                truncated = truncated or was_truncated
                if len(canonical_json(result).encode("utf-8")) > max_bytes:
                    result[str(key)] = "[TRUNCATED]"
                    truncated = True
                    break
            return result, truncated
        if isinstance(item, (list, tuple)):
            result_list: list[Any] = []
            truncated = False
            for index, child in enumerate(item):
                candidate, was_truncated = walk(child, path + "[%d]" % index)
                result_list.append(candidate)
                truncated = truncated or was_truncated
                if len(canonical_json(result_list).encode("utf-8")) > max_bytes:
                    result_list[-1] = "[TRUNCATED]"
                    truncated = True
                    break
            return result_list, truncated
        if isinstance(item, str) and len(item.encode("utf-8")) > max_bytes:
            return item.encode("utf-8")[: max(0, max_bytes // 2)].decode("utf-8", errors="ignore") + "...[TRUNCATED]", True
        return item, False

    return walk(value, "value")

