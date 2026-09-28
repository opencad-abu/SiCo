"""Bounded SSE transport; remote error bodies and credentials are never logged."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

from ..core.contracts import Cancelled, ProviderError
from ..transport.framing import strict_json


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def sse_events(endpoint, headers, payload, timeout, cancelled, *, done_marker=False):
    if cancelled():
        raise Cancelled()
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        headers={"content-type": "application/json", "accept": "text/event-stream", **headers},
        method="POST",
    )
    # System proxy and CA settings are honored. Keys never follow HTTP redirects.
    opener = urllib.request.build_opener(NoRedirect())
    emitted = False
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.headers.get_content_type() != "text/event-stream":
                raise ProviderError("Model endpoint did not return text/event-stream")
            data, size, total = [], 0, 0
            while True:
                if cancelled():
                    raise Cancelled()
                raw = response.readline(262_145)
                if not raw:
                    if data:
                        raise ProviderError("Truncated model SSE event")
                    return
                total += len(raw)
                if len(raw) > 262_144 or total > 4 * 1024 * 1024:
                    raise ProviderError("Model stream exceeds byte limit")
                if raw in (b"\n", b"\r\n"):
                    if data:
                        raw_event = b"\n".join(data)
                        if done_marker and raw_event == b"[DONE]":
                            yield None
                            return
                        event = strict_json(raw_event)
                        emitted = True
                        yield event
                    data, size = [], 0
                elif raw.startswith(b"data:"):
                    chunk = raw[5:].lstrip(b" ").rstrip(b"\r\n")
                    size += len(chunk)
                    if size > 262_144:
                        raise ProviderError("Model SSE event exceeds byte limit")
                    data.append(chunk)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ProviderError(
            f"Model endpoint returned HTTP {code}",
            retryable=code in {408, 429, 500, 502, 503, 504, 529},
            code=f"http_{code}",
        ) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        if cancelled():
            raise Cancelled() from None
        raise ProviderError("Model connection interrupted", retryable=not emitted) from None
    except ValueError:
        raise ProviderError("Invalid model SSE payload") from None
