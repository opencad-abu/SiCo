"""Fail-closed HTTPS model provider interface.

The implementation uses the fixed Python installation's urllib facilities;
the provider policy does not require a separately installed HTTP client.
"""

from __future__ import annotations

import json
import math
import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..backend import BaseProvider, ProviderRequest, ProviderResponse, ProviderUnavailable
from ..protocol import Action, ErrorCode, ProtocolError
from ..value_codec import ensure_json
from ..context import redact_secrets


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_FORBIDDEN_ENVIRONMENT_NAMES = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "LD_PRELOAD",
        "LD_AUDIT",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    }
)


@dataclass(frozen=True)
class SecretReference:
    """A non-secret reference to a controller-owned environment variable."""

    name: str
    source: str = "environment"

    def __post_init__(self) -> None:
        if self.source != "environment":
            raise ValueError("HTTP model secret reference source is unsupported")
        if (
            not isinstance(self.name, str)
            or not _ENVIRONMENT_NAME.fullmatch(self.name)
            or self.name in _FORBIDDEN_ENVIRONMENT_NAMES
        ):
            raise ValueError("HTTP model secret reference name is invalid")

    @classmethod
    def from_value(cls, value: object) -> "SecretReference | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value)
        raise ValueError("HTTP model secret reference must be an environment name")

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "name": self.name}


@dataclass(frozen=True)
class HttpModelConfig:
    endpoint: str
    allowed_hosts: frozenset[str] = frozenset()
    timeout_seconds: float = 20.0
    max_response_bytes: int = 2 * 1024 * 1024
    api_key: str | None = None
    secret_reference: SecretReference | str | None = None
    # The configured identity is provenance, not an authorization token.  A
    # response may provide a more specific identity; when it does not, this
    # value is retained and finally falls back to the stable adapter id.
    model_id: str | None = None


class HttpModelProvider(BaseProvider):
    name = "http_model"
    version = "1"

    def __init__(
        self,
        config: HttpModelConfig | str,
        *,
        allowed_hosts: set[str] | frozenset[str] = frozenset(),
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 2 * 1024 * 1024,
        api_key: str | None = None,
        secret_reference: SecretReference | str | None = None,
        model_id: str | None = None,
        transport: Any | None = None,
    ) -> None:
        super().__init__()
        if isinstance(config, str):
            config = HttpModelConfig(
                config,
                frozenset(allowed_hosts),
                timeout_seconds,
                max_response_bytes,
                api_key,
                secret_reference,
                model_id,
            )
        if not isinstance(config, HttpModelConfig):
            raise TypeError("HTTP model config must be HttpModelConfig or text endpoint")
        if not isinstance(config.endpoint, str):
            raise ValueError("HTTP model endpoint must be text")
        if not isinstance(config.timeout_seconds, (int, float)) or isinstance(config.timeout_seconds, bool) or not math.isfinite(float(config.timeout_seconds)) or config.timeout_seconds <= 0:
            raise ValueError("HTTP model timeout must be a positive finite number")
        if not isinstance(config.max_response_bytes, int) or isinstance(config.max_response_bytes, bool) or config.max_response_bytes <= 0:
            raise ValueError("HTTP model response cap must be a positive integer")
        if config.api_key is not None and (
            not isinstance(config.api_key, str)
            or not config.api_key
            or any(char in config.api_key for char in "\r\n")
            or len(config.api_key) > 4096
        ):
            raise ValueError("HTTP model API key is invalid")
        normalized_secret_reference = SecretReference.from_value(config.secret_reference)
        if config.api_key is not None and normalized_secret_reference is not None:
            raise ValueError("HTTP model API key and secret reference are mutually exclusive")
        normalized_model_id = _normalize_model_id(config.model_id)
        if not isinstance(config.allowed_hosts, (set, frozenset, tuple, list)):
            raise ValueError("HTTP model allowed_hosts must be a set of host names")
        normalized_hosts = frozenset(_normalize_host_entry(item) for item in config.allowed_hosts)
        if any(not item for item in normalized_hosts):
            raise ValueError("HTTP model allowed host is invalid")
        config = HttpModelConfig(
            config.endpoint,
            normalized_hosts,
            float(config.timeout_seconds),
            config.max_response_bytes,
            config.api_key,
            normalized_secret_reference,
            normalized_model_id,
        )
        self.config = config
        self.model_id = normalized_model_id
        # ``transport`` is an explicit dependency-injection seam for offline
        # qualification tests.  Production callers leave it unset, which
        # selects the no-proxy/no-redirect urllib opener below; no ambient
        # network client is ever discovered implicitly.
        if transport is not None and not (callable(transport) or callable(getattr(transport, "open", None))):
            raise ValueError("HTTP model transport must be callable or provide open()")
        self._transport = transport
        self._endpoint_error: str | None = None
        if not config.endpoint:
            # An unconfigured provider is a valid object, but it must report a
            # stable unavailable error when selected at runtime.  This keeps
            # provider selection/configuration separate from agent startup.
            self._endpoint_error = "no model endpoint configured"
            return
        if any(char.isspace() or ord(char) < 0x20 for char in config.endpoint):
            raise ValueError("HTTP model endpoint contains whitespace or control characters")
        try:
            parsed = urlparse(config.endpoint)
            hostname = parsed.hostname
            username = parsed.username
            password = parsed.password
            fragment = parsed.fragment
            parsed_port = parsed.port
        except ValueError as exc:
            raise ValueError("HTTP model endpoint is malformed") from exc
        if (
            parsed.scheme.lower() != "https"
            or not hostname
            or username
            or password
            or fragment
        ):
            raise ValueError("HTTP model endpoint must be HTTPS with a host")
        # An empty allowlist is deny-all.  The provider is an explicit network
        # capability and must never become usable because a caller omitted a
        # host restriction.
        if not config.allowed_hosts:
            raise ValueError("HTTP model endpoint requires a non-empty host allowlist")
        endpoint_port = parsed_port if parsed_port is not None else 443
        if not 1 <= endpoint_port <= 65535:
            raise ValueError("HTTP model endpoint port is invalid")
        if not _host_allowed(hostname, endpoint_port, config.allowed_hosts):
            raise ValueError("HTTP model endpoint host is not allowlisted")

    def next_action(self, request: ProviderRequest) -> ProviderResponse:
        self._check_interrupt()
        # The production default remains unavailable until an endpoint is
        # explicitly configured and qualified.  This avoids accidental network
        # calls from Rule/Replay-only installations.
        if self._endpoint_error is not None:
            raise ProviderUnavailable(ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value, self._endpoint_error)
        try:
            redacted_request = redact_secrets(request.to_dict())
            ensure_json(redacted_request)
            body = json.dumps(redacted_request, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except Exception as exc:
            # A malformed injected request must not escape as a raw Python
            # exception or trigger an unbounded retry.  It is a provider
            # boundary failure, with the original text redacted by
            # ProviderUnavailable.
            raise ProviderUnavailable(
                ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
                "model request could not be prepared: %s" % exc,
            ) from exc
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            api_key = self._resolve_api_key()
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable(
                ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
                "configured model secret reference is invalid",
            ) from exc
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        req = Request(self.config.endpoint, data=body, headers=headers, method="POST")
        try:
            response = self._open(req)
            if hasattr(response, "__enter__"):
                with response as stream:
                    _require_success_status(stream, fallback=response)
                    raw = stream.read(self.config.max_response_bytes + 1)
            else:
                _require_success_status(response)
                raw = response.read(self.config.max_response_bytes + 1)
        except ProviderUnavailable:
            raise
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise ProviderUnavailable(ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value, "model endpoint unavailable: %s" % exc) from exc
        except Exception as exc:
            # Test transports and embedded adapters can raise arbitrary
            # RuntimeError/ValueError instances.  Normalize all ordinary
            # exceptions to the stable provider-unavailable contract.
            raise ProviderUnavailable(ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value, "model endpoint unavailable") from exc
        if not isinstance(raw, (bytes, bytearray)):
            raise ProviderUnavailable(ErrorCode.INVALID_ACTION.value, "model endpoint returned a non-byte response")
        raw = bytes(raw)
        if len(raw) > self.config.max_response_bytes:
            raise ProviderUnavailable(ErrorCode.BUNDLE_INVALID.value, "model response exceeds size cap")
        try:
            value = json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_object_names,
            )
            if not isinstance(value, Mapping):
                action = None
            elif "action" in value:
                allowed = {"action", "provider", "model_id", "usage", "metadata"}
                unknown = [key for key in value if not isinstance(key, str) or key not in allowed]
                if unknown:
                    raise ProtocolError(
                        ErrorCode.UNKNOWN_FIELD,
                        "HTTP response wrapper contains unknown fields",
                        {"fields": sorted(str(item) for item in unknown)},
                    )
                action = Action.from_dict(value["action"])
                response_provider = value.get("provider", self.name)
                response_model_id = value.get("model_id", self.model_id)
                usage = value.get("usage", {})
                metadata = value.get("metadata", {})
                if not isinstance(response_provider, str) or not response_provider or len(response_provider) > 128:
                    raise ProtocolError(ErrorCode.INVALID_ACTION, "HTTP response provider identity is invalid")
                response_model_id = _normalize_model_id(response_model_id)
                if not isinstance(usage, Mapping) or not isinstance(metadata, Mapping):
                    raise ProtocolError(ErrorCode.INVALID_ACTION, "HTTP response usage/metadata must be objects")
            else:
                action = Action.from_dict(value)
                response_provider = self.name
                response_model_id = self.model_id
                usage = {}
                metadata = {}
        except (UnicodeError, ValueError, ProtocolError) as exc:
            raise ProviderUnavailable(ErrorCode.INVALID_ACTION.value, "model response is not a valid action") from exc
        if action is None:
            raise ProviderUnavailable(ErrorCode.INVALID_ACTION.value, "model response contains no action")
        return ProviderResponse.from_action(
            action,
            provider=response_provider,
            model_id=response_model_id or "http",
            usage=usage,
            raw_metadata=metadata,
        )

    def _open(self, request: Request) -> Any:
        transport = self._transport
        if transport is None:
            return _NoRedirectOpener().open(request, timeout=self.config.timeout_seconds)
        if callable(getattr(transport, "open", None)):
            return transport.open(request, timeout=self.config.timeout_seconds)
        return transport(request, self.config.timeout_seconds)

    def _resolve_api_key(self) -> str | None:
        """Resolve a controller-owned secret without exposing it to metadata."""

        if self.config.api_key is not None:
            return self.config.api_key
        reference = SecretReference.from_value(self.config.secret_reference)
        if reference is None:
            return None
        value = os.environ.get(reference.name)
        if value is None or not value or any(char in value for char in "\r\n") or len(value) > 4096:
            raise ProviderUnavailable(
                ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
                "configured model secret reference is unavailable",
            )
        return value

    @property
    def secret_reference(self) -> dict[str, str] | None:
        """Expose only the reference metadata, never the resolved secret."""

        reference = SecretReference.from_value(self.config.secret_reference)
        return None if reference is None else reference.to_dict()


def _normalize_model_id(value: object) -> str | None:
    """Validate a bounded, non-secret model identity string."""

    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("HTTP model id is invalid")
    if any(char.isspace() or ord(char) < 0x20 for char in value):
        raise ValueError("HTTP model id is invalid")
    return value


def _normalize_host_entry(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("HTTP model allowed host must be text")
    value = value.strip().lower().rstrip(".")
    if (
        not value
        or any(char.isspace() or ord(char) < 0x20 for char in value)
        or any(char in value for char in "/?#@")
        or "*" in value
    ):
        raise ValueError("HTTP model allowed host is invalid")
    # Keep an optional numeric port in the canonical entry.  Host-only
    # entries intentionally mean the default HTTPS port, preventing a caller
    # from bypassing an allowlist by selecting an arbitrary port.
    if value.startswith("["):
        close = value.find("]")
        if close <= 1:
            raise ValueError("HTTP model IPv6 host is invalid")
        try:
            host = str(ipaddress.IPv6Address(value[1:close])).lower()
        except ValueError as exc:
            raise ValueError("HTTP model IPv6 host is invalid") from exc
        suffix = value[close + 1 :]
        if suffix:
            if not suffix.startswith(":") or not suffix[1:].isdigit():
                raise ValueError("HTTP model host port is invalid")
            port = int(suffix[1:])
            if not 1 <= port <= 65535:
                raise ValueError("HTTP model host port is invalid")
        if suffix:
            suffix = ":%d" % int(suffix[1:])
        return "[%s]%s" % (host, suffix)
    if value.count(":") > 1:
        raise ValueError("HTTP model IPv6 host must be bracketed")
    if ":" in value:
        host, port = value.rsplit(":", 1)
        if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("HTTP model host port is invalid")
        return "%s:%d" % (host, int(port))
    return value


def _host_allowed(hostname: str, port: int, allowed: frozenset[str]) -> bool:
    host = hostname.lower().rstrip(".")
    try:
        host = str(ipaddress.IPv6Address(host)).lower()
    except ValueError:
        pass
    for entry in allowed:
        if entry.startswith("["):
            close = entry.find("]")
            entry_host = entry[1:close].lower()
            suffix = entry[close + 1 :]
            entry_port = int(suffix[1:]) if suffix else 443
        elif ":" in entry:
            entry_host, raw_port = entry.rsplit(":", 1)
            entry_port = int(raw_port)
        else:
            entry_host, entry_port = entry, 443
        if host == entry_host.rstrip(".") and port == entry_port:
            return True
    return False


class _DuplicateObjectName(ValueError):
    pass


def _reject_duplicate_object_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectName("duplicate response field: %s" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


def _response_status(response: Any) -> int | None:
    """Read a response status from common urllib/transport interfaces."""

    for name in ("status", "status_code"):
        try:
            value = getattr(response, name)
        except Exception:
            value = None
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            return value
    try:
        getter = getattr(response, "getcode", None)
    except Exception:
        getter = None
    if callable(getter):
        try:
            value = getter()
        except Exception:
            return None
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value
    return None


def _require_success_status(response: Any, *, fallback: Any | None = None) -> None:
    """Accept only an explicit successful HTTP status from the transport."""

    status = _response_status(response)
    if status is None and fallback is not None:
        status = _response_status(fallback)
    if status is None or not 200 <= status < 300:
        # Do not include response body or transport-provided text in the
        # qualification error.  A custom transport is untrusted input, and
        # a non-2xx body must never be mistaken for an Action.
        raise ProviderUnavailable(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
            "model endpoint returned a non-success HTTP status",
        )


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so the configured host allowlist remains binding."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise HTTPError(req.full_url, code, "redirects are disabled", headers, None)


class _NoRedirectOpener:
    def __init__(self) -> None:
        # Never inherit ambient HTTP(S)_PROXY/NO_PROXY settings.  Network
        # access is an explicit provider capability and must remain bound to
        # the configured host allowlist.
        self._opener = build_opener(ProxyHandler({}), _NoRedirectHandler())

    def open(self, request, timeout):  # type: ignore[no-untyped-def]
        return self._opener.open(request, timeout=timeout)


__all__ = ["HttpModelConfig", "HttpModelProvider", "SecretReference"]
