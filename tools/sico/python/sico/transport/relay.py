"""Virtuoso relay protocol and shutdown, with compatibility exports for its stdio reader."""

from __future__ import annotations

from sicoenv import read as environment_setting

import json
import math
import os
import queue
import select
import socket
import sys
import threading
import time
from pathlib import Path

from cadai.entry_context import wire_arguments

from ..core.contracts import BoundContext
from .circuit import METHOD as CIRCUIT_METHOD
from .circuit import wire_arguments as circuit_wire_arguments
from .framing import VERSION, Connection, ProtocolError, strict_json
from .methods import ASSISTANT_METHOD, READ_METHODS, SHARED_READ_METHODS
from .native_bindings import native_wire
from .project import PROJECT_READ_METHODS
from .project import wire_arguments as project_wire_arguments
from .relay_stdio import (
    SkillReplyWriteFailed as SkillReplyWriteFailed,
)

# Compatibility imports retain the historical module entry; migrate imports to relay_stdio.
from .relay_stdio import (
    SkillResponseTimeout as SkillResponseTimeout,
)
from .relay_stdio import (
    StdioLines as StdioLines,
)

DEFAULT_SKILL_RESPONSE_TIMEOUT = 1800.0
MIN_SKILL_RESPONSE_TIMEOUT = 1.0
MAX_SKILL_RESPONSE_TIMEOUT = 3600.0
_DIAGNOSTIC_LOCK = threading.RLock()


def circuit_diagnostic(reply):
    """Retain bounded failure evidence and synchronous SKILL output."""
    from cadai.skill_diagnostics import SkillDiagnostics

    raw = reply.get("diagnostic")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key in ("code", "category", "function", "stage", "request_id", "target_id",
                "function_entered", "operation_dispatched", "automatic_resume_allowed",
                "raw_error", "raw_error_truncated"):
        value = raw.get(key)
        if isinstance(value, str):
            limit = 2048 if key == "raw_error" else 160
            bounded = value[:limit]
            result[key] = bounded
            if bounded != value:
                result["diagnostic_truncated"] = True
        elif type(value) is bool:
            result[key] = value
    diagnostics = SkillDiagnostics()
    diagnostics.add(raw)
    result = diagnostics.attach(result)
    if "output" in result and result["output"] != raw.get("output"):
        result["diagnostic_truncated"] = True
    return result


def diagnostic_sink_from_environment(environment=None):
    """Return a bounded private JSONL diagnostic writer when configured.

    Diagnostics are deliberately opt-in so the relay keeps its existing wire
    protocol and does not write files into an arbitrary working directory.
    The caller owns the returned file object and must close it.
    """
    env = os.environ if environment is None else environment
    path = str(environment_setting(env, "SICO_AI_DIAGNOSTIC_LOG", "")).strip()
    if not path or not os.path.isabs(path):
        return None
    try:
        parent = os.path.dirname(path)
        if not parent or not os.path.isdir(parent):
            return None
        from ..storage.journal import open_private

        return os.fdopen(open_private(Path(path), os.O_CREAT | os.O_APPEND | os.O_WRONLY),
                         "a", encoding="utf-8", buffering=1)
    except (OSError, ValueError):
        return None


def emit_diagnostic(sink, event, **fields):
    """Write one bounded diagnostic event without affecting request handling."""
    if sink is None:
        return
    record = {"event": event, **fields}
    try:
        with _DIAGNOSTIC_LOCK:
            text = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(text.encode("utf-8")) > 8192:
                # Keep request identity and the beginning of the failure even when
                # Unicode/control characters expand beyond the line budget.
                record = {key: value[:160] if isinstance(value, str) else value
                          for key, value in record.items()
                          if key in {"event", "request_id", "method", "function", "code",
                                     "instance_id", "generation", "target_id", "ok"}}
                evidence = circuit_diagnostic({"diagnostic": fields.get("diagnostic")})
                if evidence:
                    record["diagnostic"] = {
                        key: value[:160] if isinstance(value, str) else value
                        for key, value in evidence.items()
                    }
                record["diagnostic_truncated"] = True
                text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                limit = 80
                while len(text.encode("utf-8")) > 8192:
                    for key, value in record.items():
                        if isinstance(value, str):
                            record[key] = value[:limit]
                        elif isinstance(value, dict):
                            record[key] = {k: v[:limit] if isinstance(v, str) else v
                                           for k, v in value.items()}
                    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    limit //= 2
            sink.write(text + "\n")
            sink.flush()
    except (OSError, TypeError, ValueError):
        return






def skill_response_timeout(environment=None, configured=None):
    """Resolve the bounded timeout for a dispatched SKILL operation.

    The dedicated setting takes precedence; the MCP setting is accepted so a
    desktop worker and its relay use the same budget when only the existing
    Copilot configuration is present.
    """
    env = os.environ if environment is None else environment
    raw = configured
    if raw is None:
        for name in ("SICO_SKILL_TIMEOUT", "SICO_MCP_TIMEOUT"):
            selected = environment_setting(env, name)
            if selected is not None:
                raw = selected
                break
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return DEFAULT_SKILL_RESPONSE_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError("Invalid SKILL response timeout") from None
    if (
        not math.isfinite(value)
        or not MIN_SKILL_RESPONSE_TIMEOUT <= value <= MAX_SKILL_RESPONSE_TIMEOUT
    ):
        raise ValueError("Invalid SKILL response timeout")
    return value


def assistant_wire_arguments(arguments):
    """Encode one bounded source-bound Assistant request for the SKILL relay."""
    if not isinstance(arguments, dict) or set(arguments) != {"method", "code"}:
        raise ProtocolError("assistant_call requires method and code")
    method, code = arguments["method"], arguments["code"]
    if method not in {"eval_skill", "eval_skill_native", "load_skill_file"}:
        raise ProtocolError("unsupported Assistant method")
    if not isinstance(code, str) or not code or len(code.encode("utf-8")) > 65536:
        raise ProtocolError("Assistant source exceeds the 64 KiB request limit")
    return [method, code.encode("utf-8").hex()]




def relay(
    host: str,
    port: int,
    token: str,
    source=None,
    sink=None,
    *,
    reader=None,
    registration=None,
    stopped=None,
    targets=None,
    skill_timeout=None,
    diagnostic=None,
    router=None,
) -> None:
    source = source or sys.stdin.buffer
    sink = sink or sys.stdout
    reader = reader or StdioLines(source)
    registration = registration or strict_json(reader.next())
    stopped = stopped or threading.Event()
    skill_timeout = skill_response_timeout(configured=skill_timeout)
    diagnostic = diagnostic if diagnostic is not None else diagnostic_sink_from_environment()
    context = BoundContext.from_record(registration["context"])
    hello = {
        "protocol": VERSION,
        "kind": "hello",
        "token": token,
        "instance_id": context.instance_id,
        "generation": context.generation,
        "contexts": [context.record()],
    }
    delay = 0.1
    pending_skill_id = None
    while not reader.closed.is_set() and not stopped.is_set():
        connection = None
        try:
            connection = Connection(socket.create_connection((host, port), timeout=3))
            if targets:
                hello["contexts"] = targets.records()
            connection.send(hello)
            welcome = connection.receive()
            if (
                welcome.get("protocol") != VERSION
                or welcome.get("kind") != "welcome"
                or welcome.get("generation") != context.generation
            ):
                raise ProtocolError("Invalid broker handshake")
            delay = 0.1
            while not reader.closed.is_set() and not stopped.is_set():
                if router:
                    pending_skill_id = router.skill_pending()
                # A timed-out SKILL operation may still complete later. Drain
                # only its matching response before allowing another command
                # to reach the shared CIW stream. Other lines are discarded so
                # a late reply can never be mistaken for a newer request.
                if pending_skill_id is not None:
                    try:
                        late = reader.lines.get_nowait()
                    except queue.Empty:
                        late = None
                    if late is not None:
                        try:
                            late_response = strict_json(late)
                        except ProtocolError:
                            late_response = None
                        if (
                            isinstance(late_response, dict)
                            and late_response.get("id") == pending_skill_id
                        ):
                            if router and not router.skill_reply(late_response):
                                continue
                            emit_diagnostic(
                                diagnostic,
                                "skill.late_reply",
                                request_id=pending_skill_id,
                                matched=True,
                            )
                            connection.send({"protocol": VERSION, "kind": "skill_late_reply",
                                "generation": context.generation, "id": pending_skill_id,
                                "ok": late_response.get("ok") is True, "reply": late_response})
                            pending_skill_id = None
                        elif late_response is not None:
                            emit_diagnostic(
                                diagnostic,
                                "skill.late_reply",
                                request_id=late_response.get("id"),
                                matched=False,
                            )
                if not select.select([connection.socket], [], [], 0.2)[0]:
                    continue
                request = connection.receive()
                target = targets.get(request.get("target_id")) if targets else context
                if (
                    request.get("protocol") != VERSION
                    or request.get("kind") != "request"
                    or request.get("method")
                    not in READ_METHODS | {CIRCUIT_METHOD, ASSISTANT_METHOD}
                    or request.get("instance_id") != context.instance_id
                    or request.get("generation") != context.generation
                    or target is None
                    or request.get("target_id") != target.target_id
                ):
                    raise ProtocolError("Unsupported or stale context request")
                # Only registered methods and validated data reach SKILL; never source.
                method = request["method"]
                if method != "get_context" and method not in target.snapshot.get(
                    "capabilities", []
                ):
                    raise ProtocolError("Read method was not advertised by this target")
                if (
                    method
                    not in SHARED_READ_METHODS
                    | PROJECT_READ_METHODS
                    | {CIRCUIT_METHOD, ASSISTANT_METHOD}
                    and request.get("params", {}) != {}
                ):
                    raise ProtocolError("Read methods do not accept arguments")
                request_id = request.get("id", "")
                if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
                    raise ProtocolError("Invalid request id")
                suffix = "" if method == "get_context" else " " + method
                if target.target_id != "bound":
                    suffix = " " + method + " " + target.target_id
                if method in SHARED_READ_METHODS:
                    suffix = (
                        " "
                        + method
                        + " "
                        + target.target_id
                        + " "
                        + " ".join(wire_arguments(method, request.get("params", {})))
                    )
                elif method in PROJECT_READ_METHODS:
                    suffix = (
                        " "
                        + method
                        + " "
                        + target.target_id
                        + " "
                        + " ".join(project_wire_arguments(method, request.get("params", {})))
                    )
                elif method == CIRCUIT_METHOD:
                    suffix = (
                        " "
                        + method
                        + " "
                        + target.target_id
                        + " "
                        + " ".join(circuit_wire_arguments(request.get("params", {})))
                    )
                elif method == ASSISTANT_METHOD:
                    suffix = (
                        " " + method + " " + target.target_id + " "
                        + " ".join(assistant_wire_arguments(request.get("params", {})))
                    )
                if pending_skill_id is not None:
                    connection.send(
                        {
                            "protocol": VERSION,
                            "kind": "response",
                            "generation": context.generation,
                            "id": request_id,
                            "ok": False,
                            "code": "skill_response_pending",
                            "error": (
                                "A previous SKILL request is still completing; its execution "
                                "status is unknown and no new request was dispatched"
                            ),
                        }
                    )
                    continue
                if request.get("native_identity"):
                    suffix += " @binding " + native_wire(request["native_identity"])
                if len((request_id + suffix).encode("utf-8")) > 199998:
                    raise ValueError("Native identity and call exceed frame limit")
                if router:
                    router.skill_started(request_id)
                pending_skill_id = request_id
                sink.write(request_id + suffix + "\n")
                sink.flush()
                emit_diagnostic(
                    diagnostic,
                    "skill.dispatch",
                    request_id=request_id,
                    method=method,
                    function=request.get("params", {}).get("function"),
                    instance_id=context.instance_id,
                    generation=context.generation,
                    target_id=target.target_id,
                )
                try:
                    reply_deadline = time.monotonic() + skill_timeout
                    while True:
                        remaining = max(0, reply_deadline - time.monotonic())
                        try:
                            raw = reader.next(timeout=min(.2, remaining) if router else remaining)
                        except SkillResponseTimeout:
                            if router and router.skill_write_failed(request_id):
                                raise SkillReplyWriteFailed('SKILL IPC reply write failed') from None
                            if time.monotonic() >= reply_deadline:
                                raise
                            continue
                        response = strict_json(raw)
                        if response.get("id") == request_id:
                            break
                        emit_diagnostic(diagnostic, "skill.late_reply",
                                        request_id=response.get("id"), matched=False)
                except SkillResponseTimeout as exc:
                    # The request was already written to SKILL. Report an
                    # unknown outcome with the same id and keep the relay
                    # alive while the reader quarantines the stream.
                    if router:
                        router.mark_unknown(request_id)
                    connection.send(
                        {
                            "protocol": VERSION,
                            "kind": "response",
                            "generation": context.generation,
                            "id": request_id,
                            "ok": False,
                            "code": "skill_reply_write_failed" if isinstance(exc, SkillReplyWriteFailed) else "skill_timeout",
                            "error": (
                                "SKILL reply write failed; result is unconfirmed and will not be retried"
                                if isinstance(exc, SkillReplyWriteFailed) else
                                "SKILL request was dispatched but no response arrived before the "
                                "configured deadline; execution status is unknown and will not "
                                "be retried"
                            ),
                        }
                    )
                    pending_skill_id = request_id
                    emit_diagnostic(
                        diagnostic,
                        "skill.timeout_unknown",
                        code="skill_reply_write_failed" if isinstance(exc, SkillReplyWriteFailed) else "skill_timeout",
                        request_id=request_id,
                        method=method,
                        instance_id=context.instance_id,
                        generation=context.generation,
                    )
                    continue
                if router:
                    if not router.skill_reply(response):
                        raise ProtocolError("SKILL reply identity was rejected by the instance router")
                pending_skill_id = None
                emit_diagnostic(
                    diagnostic,
                    "skill.reply",
                    request_id=request_id,
                    method=method,
                    function=request.get("params", {}).get("function"),
                    target_id=target.target_id,
                    diagnostic=circuit_diagnostic(response),
                    ok=response.get("ok") is True,
                    code=response.get("code"),
                    instance_id=context.instance_id,
                    generation=context.generation,
                )
                response.update(protocol=VERSION, kind="response", generation=context.generation)
                connection.send(response)
        except (OSError, EOFError):
            if router and router.skill_pending():
                router.mark_unknown(router.skill_pending())
            # Reconnect identity only. Never replay a previously dispatched SKILL command.
            reader.closed.wait(delay)
            delay = min(3, delay * 2)
        finally:
            if connection:
                connection.close()
    if diagnostic is not None:
        try:
            diagnostic.close()
        except OSError:
            pass
