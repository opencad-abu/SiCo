"""Shared, value-redacted validation for model endpoints and output budgets."""

from __future__ import annotations

from urllib.parse import urlsplit


def provider_endpoint(base_url, resource):
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Provider API URL must be a nonempty string")
    base = base_url.strip().rstrip("/")
    try:
        url = urlsplit(base)
        port = url.port
    except ValueError:
        raise ValueError("Invalid provider API URL") from None
    local = url.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        (url.scheme != "https" and not (url.scheme == "http" and local))
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or port == 0
        or any(c.isspace() or ord(c) < 32 for c in base)
    ):
        raise ValueError("Provider API URL must use HTTPS (HTTP allowed for loopback tests)")
    suffix = "/" + resource
    if url.path.endswith(suffix):
        return base
    if any(
        url.path.endswith("/" + other) for other in ("messages", "chat/completions", "responses")
    ):
        raise ValueError("Provider API endpoint does not match the selected protocol")
    return base + (suffix if url.path.endswith("/v1") else "/v1" + suffix)


def validate_settings(model, max_tokens, timeout):
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Provider model must be a nonempty string")
    if (
        type(max_tokens) is not int
        or not 1 <= max_tokens <= 128_000
        or type(timeout) not in (int, float)
        or not 0 < timeout <= 120
    ):
        raise ValueError("Invalid provider output or timeout budget")


def validate_key(api_key):
    if not isinstance(api_key, str) or not api_key.strip() or any(c in api_key for c in "\r\n"):
        raise ValueError("A valid API credential is required")
