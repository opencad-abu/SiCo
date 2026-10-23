"""Bounded private failure evidence shared by supervised process owners.

The public error remains short and free of upstream bodies.  This module owns
the correlation identity, stderr capture, redaction and fixed-size JSONL log
used to retain the private cause after the process is gone.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path

SCHEMA = "sico.diagnostic.v1"
MAX_RECORD_BYTES = 16 * 1024
MAX_STDERR_BYTES = 8 * 1024
DEFAULT_MAX_BYTES = 512 * 1024
DEFAULT_BACKUPS = 2

IMPORT_FAILED = "import_failed"
AUTH_FAILED = "auth_failed"
PROCESS_EXIT = "process_exit"
PROCESS_STOPPED = "process_stopped"
PROCESS_SPAWN_FAILED = "process_spawn_failed"
PROTOCOL_FRAME_INVALID = "protocol_frame_invalid"
PROTOCOL_FRAME_TOO_LARGE = "protocol_frame_too_large"
PROTOCOL_FRAME_TRUNCATED = "protocol_frame_truncated"
TRANSPORT_READ_FAILED = "transport_read_failed"
TRANSPORT_WRITE_FAILED = "transport_write_failed"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
CLEANUP_UNCONFIRMED = "cleanup_unconfirmed"
UPSTREAM_REJECTED = "upstream_rejected"
UPSTREAM_PROTOCOL_ERROR = "upstream_protocol_error"
OPERATION_FAILED = "operation_failed"
UNEXPECTED_EXCEPTION = "unexpected_exception"

_FIELDS = frozenset({
    "category", "cleanup", "component", "correlation_id", "event", "exception",
    "exit_code", "frame_bytes", "pid", "rpc_code", "sent_bytes", "stage", "status",
    "stderr", "worker_code",
})
_SENSITIVE_NAME = re.compile(
    r"(?i)(?:API[_-]?KEY|ACCESS[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|"
    r"CREDENTIAL|PRIVATE[_-]?KEY|AUTH|PROXY)"
)
_IMPORT_ERROR = re.compile(
    r"(?i)(?:modulenotfounderror|importerror|cannot import name|"
    r"no module named|failed to import)"
)
_AUTH_ERROR = re.compile(
    r"(?i)(?:\bunauthori[sz]ed\b|\bforbidden\b|\bauthentication\b|"
    r"invalid api key|api key.{0,32}(?:invalid|missing|expired)|"
    r"token.{0,32}(?:invalid|missing|expired|required)|\b401\b|\b403\b)"
)
_REDACTIONS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"),
     r"\1[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|"
        r"password|credential)\s*[:=]\s*)['\"]?[^\s,'\"]+"),
     r"\1[REDACTED]"),
    (re.compile(r"(://[^/\s:@]+:)[^/\s@]+@"), r"\1[REDACTED]@"),
)
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def new_correlation_id(component="sico"):
    name = re.sub(r"[^a-z0-9]+", "-", str(component).lower()).strip("-") or "sico"
    return name[:24] + "-" + secrets.token_hex(8)


def clip_bytes(value, limit):
    text = str(value)
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    marker = "...<truncated>"
    room = max(0, limit - len(marker))
    return raw[:room].decode("utf-8", errors="ignore") + marker


def sensitive_values(environment):
    """Collect exact credential values that must never reach private evidence."""
    result = []
    for name, value in (environment or {}).items():
        if (isinstance(value, str) and len(value) >= 4
                and _SENSITIVE_NAME.search(str(name))):
            result.append(value)
    return tuple(sorted(set(result), key=len, reverse=True))


def redact(text, secrets_=()):
    value = str(text)
    for secret in secrets_:
        if isinstance(secret, str) and len(secret) >= 4:
            value = value.replace(secret, "[REDACTED]")
    for pattern, replacement in _REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def classify_text(text):
    """Classify a process failure from already-redacted stderr-like text."""
    if _IMPORT_ERROR.search(text or ""):
        return IMPORT_FAILED
    if _AUTH_ERROR.search(text or ""):
        return AUTH_FAILED
    return PROCESS_EXIT


def classify_rpc_error(code, message):
    """Return a stable category without retaining the upstream message."""
    if isinstance(code, int) and code in (401, 403):
        return AUTH_FAILED
    if isinstance(message, str) and classify_text(message) == AUTH_FAILED:
        return AUTH_FAILED
    if code in (-32600, -32602, -32700):
        return UPSTREAM_PROTOCOL_ERROR
    return UPSTREAM_REJECTED


def error_suffix(code, correlation_id):
    fields = []
    if code:
        fields.append(str(code))
    if correlation_id:
        fields.append("diagnostic " + str(correlation_id))
    return " [" + "; ".join(fields) + "]" if fields else ""


def _path_lock(path):
    key = os.fspath(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock


def _private_parent(path):
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError:
        return False
    return (path.is_absolute() and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0)


def _open_append(path):
    if path.is_symlink():
        raise ValueError("Diagnostic log cannot be a symlink")
    descriptor = os.open(
        path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW
        | os.O_CLOEXEC | os.O_NONBLOCK, 0o600,
    )
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        os.close(descriptor)
        raise ValueError("Diagnostic log must be a private owned regular file")
    return descriptor


class DiagnosticLog:
    """One bounded private JSONL stream with a stable correlation identity."""

    def __init__(self, path, *, component, correlation_id=None, secrets_=(),
                 max_bytes=DEFAULT_MAX_BYTES, backups=DEFAULT_BACKUPS):
        self.path = Path(path).absolute() if path is not None else None
        self.component = str(component)[:80]
        self.correlation_id = correlation_id or new_correlation_id(component)
        self.secrets = tuple(secrets_)
        self.max_bytes = max(1024, int(max_bytes))
        self.backups = max(1, min(9, int(backups)))
        if self.path is not None and not _private_parent(self.path):
            self.path = None

    @classmethod
    def disabled(cls, component, *, correlation_id=None, secrets_=()):
        return cls(None, component=component, correlation_id=correlation_id,
                   secrets_=secrets_)

    @property
    def enabled(self):
        return self.path is not None

    def child(self):
        return DiagnosticLog(
            self.path, component=self.component, secrets_=self.secrets,
            max_bytes=self.max_bytes, backups=self.backups,
        )

    def emit(self, event, **fields):
        if self.path is None:
            return None
        record = {
            "schema": SCHEMA,
            "timestamp": round(time.time(), 3),
            "component": self.component,
            "correlation_id": self.correlation_id,
            "event": clip_bytes(event, 120),
        }
        for name in _FIELDS:
            value = fields.get(name)
            if value is None:
                continue
            if name == "stderr":
                value = clip_bytes(redact(value, self.secrets), MAX_STDERR_BYTES)
            elif isinstance(value, str):
                value = clip_bytes(value, 512)
            record[name] = value
        record["correlation_id"] = self.correlation_id
        raw = (json.dumps(record, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")) + "\n").encode("utf-8")
        record_limit = min(MAX_RECORD_BYTES, self.max_bytes - 1)
        if len(raw) > record_limit:
            record["stderr"] = clip_bytes(record.get("stderr", ""),
                                          MAX_STDERR_BYTES // 2)
            record["truncated"] = True
            raw = (json.dumps(record, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > record_limit:
            record = {
                "schema": SCHEMA, "timestamp": record["timestamp"],
                "component": self.component,
                "correlation_id": self.correlation_id,
                "event": clip_bytes(event, 64), "truncated": True,
            }
            raw = (json.dumps(record, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with _path_lock(self.path):
                self._rotate_if_needed(len(raw))
                descriptor = _open_append(self.path)
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        if not written:
                            raise OSError("Diagnostic append made no progress")
                        view = view[written:]
                finally:
                    os.close(descriptor)
        except (OSError, ValueError, TypeError):
            return None
        return self.correlation_id

    def _rotate_if_needed(self, incoming):
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming <= self.max_bytes or self.backups < 1:
            return
        parent, name = self.path.parent, self.path.name
        for index in range(self.backups, 0, -1):
            source = self.path if index == 1 else parent / (name + "." + str(index - 1))
            target = parent / (name + "." + str(index))
            if source.exists() or source.is_symlink():
                os.replace(source, target)
