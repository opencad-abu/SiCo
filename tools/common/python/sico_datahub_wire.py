"""Stdlib-only DataHub v1 envelope shim for Python 3.8+ site interpreters."""

import json
import math
import re
import socket

HUB = "sico.datahub.v1"
HELLO_CONTRACT = "sico.datahub.hello.v1"
HELLO_ACTION = "hello"
STATUSES = ("ok", "error", "unknown")
ERROR_CODES = (
    "invalid_arguments",
    "unsupported_contract",
    "version_mismatch",
    "capability_not_available",
    "source_not_captured",
    "stale_context",
    "stale_object",
    "ambiguous_target",
    "read_failed",
    "result_too_large",
    "timeout",
    "cancelled",
    "internal",
)
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 55000
MAX_DEPTH = 16
MAX_SEQUENCE = 200
MAX_TEXT = 4096
MAX_ID = 128
MAX_DEADLINE_MS = 300000
DEFAULT_DEADLINE_MS = 5000
READ_RETRY_CODES = frozenset({"stale_context", "read_failed", "cancelled"})
WRITE_RETRY_CODES = frozenset({"cancelled"})

_NAME = re.compile(r"^sico(?:\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$")
_META_FIELDS = {"trace_id", "source", "target", "deadline_ms"}
_PAGE_FIELDS = {"offset", "limit", "total", "next", "truncated"}
_ERROR_FIELDS = {"code", "message", "details"}
_ENVELOPE_FIELDS = {
    "hub", "contract", "version", "id", "action", "status",
    "payload", "page", "meta", "error",
}
_REQUIRED_ENVELOPE_FIELDS = _ENVELOPE_FIELDS - {"page"}


class HubError(ValueError):
    """A stable DataHub error code with optional structured details."""

    def __init__(self, code, message, details=None):
        if code not in ERROR_CODES:
            raise ValueError("Unknown DataHub error code: " + str(code))
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {} if details is None else dict(details)

    def to_dict(self):
        return {"code": self.code, "message": self.message, "details": dict(self.details)}


def _fail(message, code="invalid_arguments", details=None):
    raise HubError(code, message, details)


def check_text(value, field, *, limit=MAX_TEXT, empty=False):
    """Validate bounded text without control characters or lone surrogates."""
    if not isinstance(value, str):
        _fail(field + " must be text")
    if not value and not empty:
        _fail(field + " must not be empty")
    if len(value) > limit:
        _fail(field + " exceeds its text limit", "result_too_large")
    for char in value:
        code = ord(char)
        if code < 32 or code == 127 or 0xD800 <= code <= 0xDFFF:
            _fail(field + " contains a control character")
    return value


def _check_value(value, depth):
    if depth > MAX_DEPTH:
        _fail("Value exceeds its nesting limit", "result_too_large")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("Non-finite numbers are not allowed")
        return
    if isinstance(value, str):
        check_text(value, "payload text", empty=True)
        return
    if isinstance(value, list):
        if len(value) > MAX_SEQUENCE:
            _fail("Sequence exceeds its item limit", "result_too_large")
        for item in value:
            _check_value(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            check_text(key, "payload field", empty=False)
            _check_value(item, depth + 1)
        return
    _fail("Payload contains an unsupported value type")


def check_value(value):
    _check_value(value, 0)


def canonical_json(value):
    """Return the canonical UTF-8 JSON representation."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        text.encode("utf-8")
        return text
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HubError("invalid_arguments", "Value cannot be encoded as JSON") from exc


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        _fail("Non-finite numbers are not allowed")
    return result


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            _fail("Duplicate JSON key")
        result[key] = value
    return result


def loads(raw):
    """Decode one strict UTF-8 JSON object."""
    if isinstance(raw, bytes):
        if len(raw) > MAX_ENVELOPE_BYTES:
            _fail("Envelope exceeds its byte limit", "result_too_large")
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HubError("invalid_arguments", "Invalid UTF-8 payload") from exc
    elif isinstance(raw, str):
        source = raw
    else:
        _fail("JSON input must be bytes or text")
    try:
        value = json.loads(
            source,
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: _fail("Non-finite numbers are not allowed"),
            parse_float=_finite_float,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, HubError):
            raise
        raise HubError("invalid_arguments", "Invalid JSON payload") from exc
    if not isinstance(value, dict):
        _fail("JSON message must be an object")
    _check_value(value, 0)
    return value


def _check_meta(meta):
    if not isinstance(meta, dict) or set(meta) != _META_FIELDS:
        _fail("Invalid envelope meta fields")
    check_text(meta["trace_id"], "meta.trace_id", limit=MAX_ID, empty=True)
    check_text(meta["source"], "meta.source", limit=64)
    check_text(meta["target"], "meta.target", limit=64)
    deadline = meta["deadline_ms"]
    if type(deadline) is not int or not 0 < deadline <= MAX_DEADLINE_MS:
        _fail("Invalid envelope deadline")


def _check_page(page):
    if not isinstance(page, dict) or set(page) != _PAGE_FIELDS:
        _fail("Invalid envelope page fields")
    for key in ("offset", "limit", "total"):
        if type(page[key]) is not int or page[key] < 0:
            _fail("Invalid envelope page " + key)
    if not 1 <= page["limit"] <= MAX_SEQUENCE:
        _fail("Invalid envelope page limit")
    if type(page["truncated"]) is not bool:
        _fail("Invalid envelope page truncation")
    next_offset = page["next"]
    if next_offset is None:
        if page["truncated"]:
            _fail("Truncated page requires its next offset")
    elif (
        type(next_offset) is not int
        or next_offset <= page["offset"]
        or next_offset > page["total"]
        or not page["truncated"]
    ):
        _fail("Invalid envelope page next offset")


def _check_error(error):
    if not isinstance(error, dict) or set(error) != _ERROR_FIELDS:
        _fail("Invalid envelope error fields")
    if error["code"] not in ERROR_CODES:
        _fail("Unknown envelope error code")
    check_text(error["message"], "error.message")
    if not isinstance(error["details"], dict):
        _fail("Envelope error details must be an object")
    _check_value(error["details"], 1)


def validate_envelope(row):
    """Validate and return one envelope without applying a contract registry."""
    if not isinstance(row, dict):
        _fail("Envelope must be an object")
    if set(row) - _ENVELOPE_FIELDS or not _REQUIRED_ENVELOPE_FIELDS <= set(row):
        _fail("Invalid envelope fields")
    if row["hub"] != HUB:
        _fail("Unsupported DataHub envelope")
    contract = check_text(row["contract"], "contract", limit=MAX_ID)
    if not _NAME.fullmatch(contract):
        _fail("Invalid contract name")
    version = row["version"]
    if type(version) is not int or version < 1:
        _fail("Invalid contract version")
    check_text(row["id"], "id", limit=MAX_ID)
    check_text(row["action"], "action", limit=MAX_ID)
    if row["status"] not in STATUSES:
        _fail("Invalid envelope status")
    if not isinstance(row["payload"], dict):
        _fail("Envelope payload must be an object")
    _check_value(row["payload"], 1)
    payload_size = len(canonical_json(row["payload"]).encode("utf-8"))
    if payload_size > MAX_PAYLOAD_BYTES:
        _fail("Payload exceeds its byte limit", "result_too_large")
    if row.get("page") is not None:
        _check_page(row["page"])
    _check_meta(row["meta"])
    error = row["error"]
    if row["status"] == "ok":
        if error is not None:
            _fail("Successful envelopes cannot carry an error")
    elif row["status"] == "error":
        if error is None:
            _fail("Failed envelopes require an error")
        _check_error(error)
    elif error is not None:
        _check_error(error)
    size = len(canonical_json(row).encode("utf-8"))
    if size > MAX_ENVELOPE_BYTES:
        _fail("Envelope exceeds its byte limit", "result_too_large")
    return row


def make_envelope(
    contract,
    action,
    request_id,
    payload=None,
    *,
    status="ok",
    page=None,
    error=None,
    meta=None,
    version=1,
):
    """Build and validate an envelope using explicit defaults."""
    envelope_meta = {
        "trace_id": "",
        "source": "python",
        "target": "python",
        "deadline_ms": DEFAULT_DEADLINE_MS,
    }
    if meta is not None:
        if not isinstance(meta, dict):
            _fail("Envelope meta must be an object")
        envelope_meta.update(meta)
    row = {
        "hub": HUB,
        "contract": contract,
        "version": version,
        "id": request_id,
        "action": action,
        "status": status,
        "payload": {} if payload is None else payload,
        "meta": envelope_meta,
        "error": error,
    }
    if page is not None:
        row["page"] = page
    return validate_envelope(row)


def error_envelope(contract, action, request_id, code, message, *, details=None,
                   meta=None, version=1):
    return make_envelope(
        contract,
        action,
        request_id,
        {},
        status="error",
        error={"code": code, "message": message, "details": details or {}},
        meta=meta,
        version=version,
    )


def may_retry(status, code, *, write=False):
    """Return whether an automatic retry is permitted by the stable policy."""
    if status != "error":
        return False
    allowed = WRITE_RETRY_CODES if write else READ_RETRY_CODES
    return code in allowed


def encode_envelope(row):
    validate_envelope(row)
    return canonical_json(row).encode("utf-8")


def decode_envelope(raw):
    return validate_envelope(loads(raw))


def encode_line(row):
    return encode_envelope(row) + b"\n"


def decode_line(raw):
    if not isinstance(raw, bytes) or not raw.endswith(b"\n"):
        _fail("Line frame must end with one newline")
    body = raw[:-1]
    if b"\n" in body:
        _fail("Line frame contains more than one message")
    return decode_envelope(body)


class EndpointClient:
    """Minimal AF_UNIX endpoint client for site Python; no package imports."""

    def __init__(self, path, token, session_ref, timeout=5.0):
        check_text(path, "endpoint path")
        check_text(token, "endpoint token", limit=256)
        check_text(session_ref, "endpoint session", limit=128)
        self.path = path
        self.session_ref = session_ref
        self.timeout = timeout
        self.capabilities = ()
        self._token = token
        self._socket = None
        self._stream = None

    def connect(self):
        if self._socket is not None:
            return self
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.path)
        except OSError as exc:
            sock.close()
            raise HubError("capability_not_available", "Endpoint is unavailable") from exc
        self._socket = sock
        self._stream = sock.makefile("rwb", buffering=0)
        hello = make_envelope(
            HELLO_CONTRACT,
            HELLO_ACTION,
            "site-hello",
            {"token": self._token, "session_ref": self.session_ref},
            meta={"trace_id": "", "source": "python-site",
                  "target": "python", "deadline_ms": 5000},
        )
        reply = self._exchange(hello)
        if reply["status"] != "ok":
            error = reply["error"] or {}
            self.close()
            raise HubError(
                error.get("code", "capability_not_available"),
                error.get("message", "Endpoint session rejected"),
            )
        self.capabilities = tuple(reply["payload"].get("capabilities", ()))
        return self

    def _exchange(self, envelope):
        try:
            self._stream.write(encode_line(envelope))
            line = self._stream.readline(MAX_ENVELOPE_BYTES + 2)
        except socket.timeout as exc:
            raise HubError("timeout", "Endpoint exchange timed out") from exc
        except OSError as exc:
            raise HubError("read_failed", "Endpoint exchange failed") from exc
        if not line:
            raise HubError("read_failed", "Endpoint closed without a response")
        response = decode_line(line)
        if (
            response["contract"] != envelope["contract"]
            or response["version"] != envelope["version"]
            or response["action"] != envelope["action"]
            or response["id"] != envelope["id"]
        ):
            raise HubError("invalid_arguments", "Endpoint response identity mismatch")
        return response

    def exchange(self, contract, version, action, request_id, payload,
                 meta=None):
        if self._stream is None:
            raise HubError("capability_not_available", "Endpoint client is not connected")
        request = make_envelope(
            contract, action, request_id, payload, version=version, meta=meta
        )
        return self._exchange(request)

    def close(self):
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_args):
        self.close()


__all__ = [
    "DEFAULT_DEADLINE_MS",
    "ERROR_CODES",
    "EndpointClient",
    "HUB",
    "HELLO_ACTION",
    "HELLO_CONTRACT",
    "HubError",
    "MAX_DEADLINE_MS",
    "MAX_DEPTH",
    "MAX_ENVELOPE_BYTES",
    "MAX_ID",
    "MAX_PAYLOAD_BYTES",
    "MAX_SEQUENCE",
    "MAX_TEXT",
    "READ_RETRY_CODES",
    "STATUSES",
    "WRITE_RETRY_CODES",
    "canonical_json",
    "check_text",
    "check_value",
    "decode_envelope",
    "decode_line",
    "encode_envelope",
    "encode_line",
    "error_envelope",
    "loads",
    "make_envelope",
    "may_retry",
    "validate_envelope",
]
