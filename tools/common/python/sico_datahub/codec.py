"""Canonical JSON, line framing and artifact references for DataHub."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Union

import sico_datahub_wire as wire

from .envelope import Envelope
from .errors import HubError
from .limits import (
    MAX_ENVELOPE_BYTES,
    MAX_PAYLOAD_BYTES,
    MAX_TEXT,
    check_text,
)

_SHA256 = frozenset("0123456789abcdef")


def canonical_json(value) -> str:
    return wire.canonical_json(value)


def encode(envelope: Envelope) -> bytes:
    if not isinstance(envelope, Envelope):
        raise HubError("invalid_arguments", "Expected a DataHub envelope")
    return wire.encode_envelope(envelope.to_dict())


def decode(raw: Union[bytes, str]) -> Envelope:
    return Envelope.from_dict(wire.decode_envelope(raw))


def encode_line(envelope: Envelope) -> bytes:
    return encode(envelope) + b"\n"


def decode_line(raw: bytes) -> Envelope:
    return Envelope.from_dict(wire.decode_line(raw))


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str
    size: int

    def to_dict(self) -> Dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}

    @classmethod
    def from_dict(cls, value: dict) -> "ArtifactRef":
        if not isinstance(value, dict) or set(value) != {"path", "sha256", "size"}:
            raise HubError("invalid_arguments", "Invalid artifact reference fields")
        path = check_text(value["path"], "artifact path", limit=MAX_TEXT)
        if not os.path.isabs(path):
            raise HubError("invalid_arguments", "Artifact path must be absolute")
        digest = value["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or set(digest) - _SHA256
        ):
            raise HubError("invalid_arguments", "Invalid artifact digest")
        size = value["size"]
        if type(size) is not int or size < 0:
            raise HubError("invalid_arguments", "Invalid artifact size")
        return cls(path, digest, size)


def artifact_ref(path: Union[str, Path], *, chunk_size: int = 1024 * 1024) -> ArtifactRef:
    """Describe an existing file by its absolute readable path, digest and size."""
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise HubError("invalid_arguments", "Invalid artifact read size")
    target = Path(path).expanduser()
    if not target.is_absolute() or not target.is_file():
        raise HubError("invalid_arguments", "Artifact path must name an absolute file")
    digest = hashlib.sha256()
    size = 0
    try:
        with target.open("rb") as stream:
            for block in iter(lambda: stream.read(chunk_size), b""):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise HubError("read_failed", "Artifact could not be read") from exc
    return ArtifactRef(str(target), digest.hexdigest(), size)


def encode_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise HubError("invalid_arguments", "Base64 input must be bytes")
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: str, *, max_bytes: int = MAX_PAYLOAD_BYTES) -> bytes:
    if not isinstance(value, str) or not value.isascii():
        raise HubError("invalid_arguments", "Base64 input must be ASCII text")
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_ENVELOPE_BYTES:
        raise HubError("invalid_arguments", "Invalid base64 output limit")
    if len(value) > (max_bytes + 2) // 3 * 4:
        raise HubError("result_too_large", "Base64 value exceeds its byte limit")
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HubError("invalid_arguments", "Invalid base64 value") from exc
    if len(result) > max_bytes:
        raise HubError("result_too_large", "Decoded value exceeds its byte limit")
    return result


__all__ = [
    "ArtifactRef",
    "artifact_ref",
    "canonical_json",
    "decode",
    "decode_bytes",
    "decode_line",
    "encode",
    "encode_bytes",
    "encode_line",
]
