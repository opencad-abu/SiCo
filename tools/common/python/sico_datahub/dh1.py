"""Bounded DH1 request frames shared with Tcl site adapters."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Optional, Union

import sico_datahub_wire as wire

from .envelope import Envelope, Meta
from .errors import HubError

_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_VERSION = re.compile(r"^[1-9][0-9]*$")


def _token(value, field):
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise HubError("invalid_arguments", "Invalid DH1 " + field)
    return value


def _payload_json(payload):
    if not isinstance(payload, dict):
        raise HubError("invalid_arguments", "DH1 payload must be an object")
    wire.check_value(payload)
    encoded = wire.canonical_json(payload).encode("utf-8")
    if len(encoded) > wire.MAX_PAYLOAD_BYTES:
        raise HubError("result_too_large", "DH1 payload exceeds its byte limit")
    return encoded


@dataclass(frozen=True)
class Dh1Request:
    contract: str
    version: int
    id: str
    action: str
    payload: dict

    def __post_init__(self):
        _token(self.contract, "contract")
        if type(self.version) is not int or self.version < 1:
            raise HubError("invalid_arguments", "Invalid DH1 version")
        _token(self.id, "id")
        _token(self.action, "action")
        _payload_json(self.payload)

    def payload_json(self) -> str:
        return _payload_json(self.payload).decode("utf-8")

    def encode(self) -> bytes:
        payload = base64.b64encode(_payload_json(self.payload))
        frame = (
            f"DH1 {self.contract} {self.version} {self.id} {self.action} "
        ).encode("ascii") + payload
        if len(frame) > wire.MAX_ENVELOPE_BYTES:
            raise HubError("result_too_large", "DH1 frame exceeds its byte limit")
        return frame

    def encode_line(self) -> bytes:
        return self.encode() + b"\n"

    def to_envelope(self, meta: Optional[Meta] = None) -> Envelope:
        return Envelope(
            contract=self.contract,
            version=self.version,
            action=self.action,
            id=self.id,
            payload=dict(self.payload),
            meta=meta or Meta(source="tcl", target="python"),
        )

    @classmethod
    def from_envelope(cls, envelope: Envelope) -> "Dh1Request":
        if envelope.status != "ok" or envelope.page is not None or envelope.error is not None:
            raise HubError("invalid_arguments", "DH1 carries request envelopes only")
        return cls(
            contract=envelope.contract,
            version=envelope.version,
            id=envelope.id,
            action=envelope.action,
            payload=dict(envelope.payload),
        )

    @classmethod
    def decode(cls, raw: Union[bytes, str]) -> "Dh1Request":
        if isinstance(raw, str):
            try:
                line = raw.encode("utf-8")
            except UnicodeError as exc:
                raise HubError("invalid_arguments", "DH1 line is not UTF-8") from exc
        elif isinstance(raw, bytes):
            line = raw
        else:
            raise HubError("invalid_arguments", "DH1 line must be bytes or text")
        if len(line) > wire.MAX_ENVELOPE_BYTES:
            raise HubError("result_too_large", "DH1 line exceeds its byte limit")
        if line.endswith(b"\n"):
            line = line[:-1]
            if line.endswith(b"\r"):
                line = line[:-1]
        if b"\n" in line or b"\r" in line:
            raise HubError("invalid_arguments", "DH1 line contains multiple records")
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HubError("invalid_arguments", "DH1 line is not ASCII framing") from exc
        parts = text.split(" ")
        if len(parts) != 6 or parts[0] != "DH1":
            raise HubError("invalid_arguments", "Invalid DH1 frame")
        contract, version_text, request_id, action, payload_text = parts[1:]
        if not _VERSION.fullmatch(version_text):
            raise HubError("invalid_arguments", "Invalid DH1 version")
        _token(contract, "contract")
        _token(request_id, "id")
        _token(action, "action")
        try:
            payload_raw = base64.b64decode(payload_text.encode("ascii"), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HubError("invalid_arguments", "Invalid DH1 payload encoding") from exc
        if len(payload_raw) > wire.MAX_PAYLOAD_BYTES:
            raise HubError("result_too_large", "DH1 payload exceeds its byte limit")
        try:
            payload = wire.loads(payload_raw)
        except HubError:
            raise
        except UnicodeError as exc:
            raise HubError("invalid_arguments", "DH1 payload is not UTF-8") from exc
        return cls(contract, int(version_text), request_id, action, payload)


__all__ = ["Dh1Request"]
