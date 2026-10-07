"""Managed Unix-socket provider boundary.

The wire format is JSONL, but credentials and socket ownership remain outside
the agent.  The default implementation is intentionally conservative and
reports unavailable when no explicitly managed socket is supplied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import math
from typing import Any, Callable, Mapping, Tuple

from ..backend import BaseProvider, ProviderRequest, ProviderResponse, ProviderUnavailable
from ..context import redact_secrets
from ..protocol import Action, ErrorCode, ProtocolError, decode_json
from ..value_codec import ensure_json


class UnixSocketProvider(BaseProvider):
    name = "unix_socket"
    version = "1"

    def __init__(self, socket_path: str | Path | None = None, *, managed_roots: tuple[str | Path, ...] = (), timeout_seconds: float = 20.0, max_response_bytes: int = 2 * 1024 * 1024, model_id: str | None = None, channel_factory: Callable[[], socket.socket] | None = None) -> None:
        super().__init__()
        self.socket_path = None if socket_path is None else Path(socket_path).expanduser()
        if not isinstance(managed_roots, (tuple, list)):
            raise ValueError("managed_roots must be a sequence of paths")
        self.managed_roots = tuple(Path(item).expanduser() for item in managed_roots)
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
            raise ValueError("socket timeout must be positive")
        if not isinstance(max_response_bytes, int) or isinstance(max_response_bytes, bool) or max_response_bytes <= 0:
            raise ValueError("socket response cap must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.model_id = _normalize_model_id(model_id)
        if channel_factory is not None and not callable(channel_factory):
            raise ValueError("channel_factory must be callable")
        self._channel_factory = channel_factory

    def next_action(self, request: ProviderRequest) -> ProviderResponse:
        self._check_interrupt()
        if self.socket_path is None or not self._socket_is_managed():
            raise ProviderUnavailable(ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value, "managed model socket is unavailable")
        try:
            redacted_request = redact_secrets(request.to_dict())
            ensure_json(redacted_request)
            payload = (json.dumps(redacted_request, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        except Exception as exc:
            raise ProviderUnavailable(
                ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
                "model socket request could not be prepared: %s" % exc,
            ) from exc
        try:
            make_channel = self._channel_factory or (lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            with make_channel() as channel:
                channel.settimeout(self.timeout_seconds)
                channel.connect(str(self.socket_path))
                channel.sendall(payload)
                # The gateway protocol is exactly one request and one
                # response.  Half-close the request side so a peer that reads
                # until EOF can begin producing its JSONL response without a
                # deadlock.  The read side remains open until the peer closes.
                channel.shutdown(socket.SHUT_WR)
                raw, trailing = _read_single_frame(channel, self.max_response_bytes)
                if trailing.strip():
                    raise ProviderUnavailable(
                        ErrorCode.INVALID_ACTION.value,
                        "model socket returned more than one JSONL frame",
                    )
        except ProviderUnavailable:
            raise
        except (OSError, TimeoutError) as exc:
            raise ProviderUnavailable(ErrorCode.UNKNOWN_PROVIDER_STATE.value, "model socket state is unknown: %s" % exc) from exc
        except Exception as exc:
            # An embedded gateway may raise a non-socket exception.  Keep the
            # conservative unknown-state outcome: after sendall(), mutation
            # or provider progress cannot be proven absent.
            raise ProviderUnavailable(ErrorCode.UNKNOWN_PROVIDER_STATE.value, "model socket state is unknown") from exc
        try:
            value = decode_json(raw)
            response_provider = self.name
            response_model_id = self.model_id
            usage: Mapping[str, Any] = {}
            metadata: Mapping[str, Any] = {}
            # ``decode_json`` may return a typed Action for a bare action
            # object.  Only inspect wrapper keys when the decoder returned a
            # plain mapping; otherwise preserve the already-validated object.
            if isinstance(value, Action):
                action = value
            elif isinstance(value, dict) and "action" in value:
                wrapper = value
                allowed = {"action", "provider", "model_id", "usage", "metadata"}
                unknown = [key for key in wrapper if not isinstance(key, str) or key not in allowed]
                if unknown:
                    raise ProtocolError(
                        ErrorCode.UNKNOWN_FIELD,
                        "socket response wrapper contains unknown fields",
                        {"fields": sorted(str(item) for item in unknown)},
                    )
                action = Action.from_dict(wrapper["action"])
                response_provider = wrapper.get("provider", self.name)
                response_model_id = _normalize_model_id(wrapper.get("model_id", self.model_id))
                usage = wrapper.get("usage", {})
                metadata = wrapper.get("metadata", {})
                if not isinstance(response_provider, str) or not response_provider or len(response_provider) > 128:
                    raise ProtocolError(ErrorCode.INVALID_ACTION, "socket response provider identity is invalid")
                if not isinstance(usage, Mapping) or not isinstance(metadata, Mapping):
                    raise ProtocolError(ErrorCode.INVALID_ACTION, "socket response usage/metadata must be objects")
            else:
                action = Action.from_dict(value)
                response_provider = self.name
                response_model_id = self.model_id
                usage = {}
                metadata = {}
        except (ProtocolError, UnicodeError, ValueError) as exc:
            raise ProviderUnavailable(ErrorCode.INVALID_ACTION.value, "socket response is not a valid action") from exc
        return ProviderResponse.from_action(
            action,
            provider=response_provider,
            model_id=response_model_id or "unix-socket",
            usage=usage,
            raw_metadata=metadata,
        )

    def _socket_is_managed(self) -> bool:
        path = self.socket_path
        if path is None or not path.is_absolute() or _has_symlink_component(path) or not path.exists():
            return False
        try:
            socket_info = path.lstat()
            if (
                not stat.S_ISSOCK(socket_info.st_mode)
                or socket_info.st_uid != os.getuid()
                or stat.S_IMODE(socket_info.st_mode) != 0o600
            ):
                return False
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        for raw_root in self.managed_roots:
            root = raw_root
            if (
                not root.is_absolute()
                or _has_symlink_component(root)
                or not root.is_dir()
            ):
                continue
            try:
                root_info = root.lstat()
                if (
                    not stat.S_ISDIR(root_info.st_mode)
                    or root_info.st_uid != os.getuid()
                    or stat.S_IMODE(root_info.st_mode) != 0o700
                ):
                    continue
                root_resolved = root.resolve(strict=True)
                resolved.relative_to(root_resolved)
                return True
            except (OSError, RuntimeError, ValueError):
                continue
        return False

    @property
    def socket_is_managed(self) -> bool:
        """Return the current managed-root, ownership, and mode check."""

        return self._socket_is_managed()


def _has_symlink_component(path: Path) -> bool:
    if not path.is_absolute():
        current = Path.cwd()
        parts = path.parts
    else:
        current = Path(path.anchor)
        parts = path.parts[1:]
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _normalize_model_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("Unix model id is invalid")
    if any(char.isspace() or ord(char) < 0x20 for char in value):
        raise ValueError("Unix model id is invalid")
    return value


def _read_single_frame(channel: socket.socket, cap: int) -> Tuple[bytes, bytes]:
    """Read one complete JSONL frame and inspect all trailing bytes.

    The provider protocol is one request and exactly one response.  Reading
    until EOF makes a second frame observable instead of silently discarding
    it; the same hard cap applies to the first frame and any trailing data.
    """

    data = bytearray()
    newline_at = -1
    while newline_at < 0:
        if len(data) >= cap:
            # A frame may end exactly at the cap.  Probe one byte so an EOF
            # can be accepted while any additional byte is still rejected.
            chunk = channel.recv(1)
            if chunk:
                raise ProviderUnavailable(ErrorCode.BUNDLE_INVALID.value, "socket response exceeds size cap")
            break
        chunk = channel.recv(min(65536, cap - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > cap:
            raise ProviderUnavailable(ErrorCode.BUNDLE_INVALID.value, "socket response exceeds size cap")
        newline_at = data.find(b"\n")
    if newline_at < 0:
        if not data:
            # A clean peer close before any response is a transport failure,
            # not a malformed action.  The caller cannot prove whether the
            # gateway accepted or acted on the request, so preserve the
            # unknown-provider-state classification used for disconnects.
            raise ProviderUnavailable(
                ErrorCode.UNKNOWN_PROVIDER_STATE.value,
                "model socket disconnected before returning a response",
            )
        raise ProviderUnavailable(ErrorCode.INVALID_ACTION.value, "socket response is missing a JSONL frame terminator")
    first = bytes(data[:newline_at])
    trailing = bytes(data[newline_at + 1 :])
    # Continue to EOF so a second frame sent after the first recv is also
    # detected.  A peer that never closes is classified as an unknown socket
    # state by the caller's timeout handling.  The managed gateway contract
    # therefore requires the server to half-close/close after one response.
    while True:
        if len(data) >= cap:
            # As above, permit an exact-cap response only when the peer closes
            # immediately after the first frame.
            chunk = channel.recv(1)
            if chunk:
                raise ProviderUnavailable(ErrorCode.BUNDLE_INVALID.value, "socket response exceeds size cap")
            break
        chunk = channel.recv(min(65536, cap - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > cap:
            raise ProviderUnavailable(ErrorCode.BUNDLE_INVALID.value, "socket response exceeds size cap")
    return first, bytes(data[newline_at + 1 :])


__all__ = ["UnixSocketProvider"]
