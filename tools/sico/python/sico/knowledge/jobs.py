"""Supervised source workers with bounded output, deadlines and private failure evidence."""

import json
import os
import select
import sys
import time

from .. import diagnostics
from ..diagnostics import DiagnosticLog
from ..installation import python_path
from ..interpreter import cad_python
from ..local_process import LocalProcess
from ..transport.framing import strict_json
from .contracts import KnowledgeError, MAX_OUTPUT, checkpoint

_CATEGORIES = {
    "auth_required": diagnostics.AUTH_FAILED,
    "timeout": diagnostics.TIMEOUT,
    "cancelled": diagnostics.CANCELLED,
    "cleanup_unconfirmed": diagnostics.CLEANUP_UNCONFIRMED,
}


def _category(code):
    return _CATEGORIES.get(code, diagnostics.OPERATION_FAILED)


def _failed(diagnostic, stage, code, message, *, category=None, exception=None,
            worker_code=None, **fields):
    category = category or _category(code)
    diagnostic.emit(
        "knowledge.job.failed", category=category, stage=stage,
        exception=exception, worker_code=worker_code or code, **fields,
    )
    error = KnowledgeError(
        code, message, diagnostic_id=diagnostic.correlation_id, category=category)
    error.diagnostic_recorded = True
    return error


def run(project, source, action, params, environment, cancelled, deadline,
        diagnostic=None):
    diagnostic = diagnostic or DiagnosticLog.disabled("knowledge")
    stage = str(action)[:120]
    owner = process = None
    try:
        checkpoint(cancelled, deadline)
        safe = {k: v for k, v in environment.items() if k in {
            "PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "TMPDIR"}}
        safe.update(PYTHONPATH=python_path(), PYTHONNOUSERSITE="1",
                    PYTHONDONTWRITEBYTECODE="1")
        refs = {source.get("credential_ref"), "HTTP_PROXY", "HTTPS_PROXY",
                "http_proxy", "https_proxy"}
        request = {
            "project": str(project), "source": source, "action": action,
            "params": params,
            "environment": {k: v for k, v in environment.items() if k in refs},
            "seconds": max(0.1, deadline - time.monotonic()),
            "diagnostic_id": diagnostic.correlation_id,
        }
        python = cad_python(environment)
        command = [
            python, "-s", "-c",
            "from sico.knowledge.worker import main; raise SystemExit(main())",
        ]
        owner = LocalProcess(
            command, safe, str(project), diagnostic=diagnostic,
            component="knowledge", cleanup_timeout=3,
        )
        process = owner.child
        try:
            process.stdin.write(
                json.dumps(request, ensure_ascii=False).encode() + b"\n")
            process.stdin.flush()
        except OSError as exc:
            raise _failed(
                diagnostic, stage, "unavailable",
                "知识源操作未完成，请核对资料和连接",
                exception=type(exc).__name__,
            ) from None

        output = bytearray()
        poll = select.poll()
        poll.register(process.stdout, select.POLLIN | select.POLLHUP)
        while True:
            checkpoint(cancelled, deadline)
            if not poll.poll(100):
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                break
            output.extend(data)
            if len(output) > MAX_OUTPUT + 2048:
                raise _failed(
                    diagnostic, stage, "output_limit",
                    "知识源输出超过显示上限",
                )
            if b"\n" in output:
                break
        observed = owner.failure(.2) if not output else {}
        if not output and observed.get("exit_code") not in (None, 0):
            raise _failed(
                diagnostic, stage, "unavailable", "知识源工作进程未能启动",
                category=observed.get("code") or diagnostics.PROCESS_EXIT,
                exception="ProcessExit", exit_code=observed.get("exit_code"),
                stderr=observed.get("stderr"),
            )
        try:
            value = strict_json(output)
        except (ValueError, UnicodeError) as exc:
            raise _failed(
                diagnostic, stage, "unavailable", "知识源输出格式无效",
                category=diagnostics.PROTOCOL_FRAME_INVALID,
                exception=type(exc).__name__, frame_bytes=len(output),
            ) from None
        if not value.get("ok"):
            code = value.get("code")
            if not isinstance(code, str) or not code:
                code = "unavailable"
            message = value.get("message")
            if not isinstance(message, str) or not message:
                message = "知识源操作未完成，请核对资料和连接"
            raise _failed(
                diagnostic, stage, code, message,
                exception=value.get("exception"), worker_code=code,
            )
        return value["value"]
    except KnowledgeError as exc:
        if exc.diagnostic_id is None:
            exc.diagnostic_id = diagnostic.correlation_id
        if not getattr(exc, "diagnostic_recorded", False):
            diagnostic.emit(
                "knowledge.job.failed", category=exc.category, stage=stage,
                exception=type(exc).__name__, worker_code=exc.code,
            )
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise _failed(
            diagnostic, stage, "unavailable",
            "知识源操作未完成，请核对资料和连接",
            category=diagnostics.UNEXPECTED_EXCEPTION,
            exception=type(exc).__name__,
        ) from None
    finally:
        primary = sys.exc_info()[0] is not None
        cleanup_error = None
        if owner is not None:
            try:
                owner.close()
            except (OSError, RuntimeError) as exc:
                cleanup_error = exc
            finally:
                if process is not None and process.stdout is not None:
                    try:
                        process.stdout.close()
                    except (OSError, ValueError):
                        pass
        if cleanup_error is not None:
            raise _failed(
                diagnostic, stage, diagnostics.CLEANUP_UNCONFIRMED,
                "知识源进程清理未确认",
                category=diagnostics.CLEANUP_UNCONFIRMED,
                exception="ProcessCleanup",
                exit_code=getattr(cleanup_error, "exit_code", None),
            ) from None
        actual_exit = owner.exit_code() if owner is not None else None
        if actual_exit not in (None, 0):
            observed = owner.failure(.1) if owner is not None else {}
            category = (observed.get("code") if observed else None) or (
                diagnostics.PROCESS_EXIT)
            if not primary:
                raise _failed(
                    diagnostic, stage, "unavailable",
                    "知识源工作进程异常退出",
                    category=category, exception="ProcessExit",
                    exit_code=actual_exit,
                ) from None
            diagnostic.emit(
                "knowledge.worker.exited", category=category, stage=stage,
                exception="ProcessExit", exit_code=actual_exit,
                stderr=observed.get("stderr") if observed else None,
            )
