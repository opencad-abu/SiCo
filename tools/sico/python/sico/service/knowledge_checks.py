"""Project knowledge preview jobs scoped to authenticated frontend clients."""

import threading

from ..diagnostics import (
    DiagnosticLog,
    UNEXPECTED_EXCEPTION,
    sensitive_values,
)
from ..knowledge.contracts import KnowledgeError
from ..knowledge.engine import Knowledge
from ..storage.journal import private_dir
from ..storage.roots import agent_root, state_root
from .agent_settings import scope
from .service_protocol import exact_fields
from .service_values import identifier


def _diagnostic(project, environment):
    try:
        if not state_root(project).exists():
            raise ValueError("Project state is not active")
        parent = agent_root(project, create=True) / "knowledge"
        private_dir(parent)
        return DiagnosticLog(
            parent / "diagnostics.jsonl", component="knowledge",
            secrets_=sensitive_values(environment),
        )
    except (OSError, ValueError, RuntimeError):
        return DiagnosticLog.disabled(
            "knowledge", secrets_=sensitive_values(environment))


class KnowledgeChecks:
    def __init__(self, hub):
        self.hub, self.lock = hub, threading.Lock()
        self.active, self.cancelled = {}, set()

    def cancel(self, wire, params):
        exact_fields(params, {"id"})
        key = wire.identity.client_id, identifier(params["id"])
        with self.lock:
            if key in self.active:
                self.active[key].set()
            elif len(self.cancelled) < 64:
                self.cancelled.add(key)
        return {"cancel_requested": True}

    def run(self, wire, params):
        exact_fields(params, {"address", "id", "manifest", "action", "arguments"})
        address = scope(self.hub, params["address"])
        key = wire.identity.client_id, identifier(params["id"])
        stop = threading.Event()
        with self.lock:
            if key in self.cancelled:
                self.cancelled.remove(key)
                return {"status": "cancelled", "message": "知识源操作已取消"}
            if key in self.active or len(self.active) >= 2:
                raise ValueError("已有知识源检查正在执行，请稍后重试")
            self.active[key] = stop
        diagnostic = DiagnosticLog.disabled("knowledge")
        try:
            settings = (self.hub.owner.provider_for(address.session.session_id)
                        if address and address.session.runtime_id else self.hub.owner.resource_settings())
            diagnostic = _diagnostic(self.hub.owner.project, settings.environment)
            engine = Knowledge(
                self.hub.owner.project, params["manifest"], settings.environment,
                settings.options.get("api_key_env", "SICO_API_KEY"),
                diagnostic=diagnostic,
            )
            def cancelled():
                return stop.is_set() or wire.closed.is_set() or self.hub.closed.is_set()
            args, action = params["arguments"], params["action"]
            required = {"status": set(), "build": {"source_id"}, "search": {"query", "limit"},
                        "read": {"source_id", "evidence_id"}}
            if action not in required:
                raise ValueError("未知知识源操作")
            exact_fields(args, required[action])
            if action == "build":
                value = engine.build(args["source_id"], cancelled)
            else:
                value = getattr(engine, action)(**args, cancelled=cancelled)
            scope(self.hub, params["address"])
            return {"status": "cancelled", "message": "知识源操作已取消"} if cancelled() else {
                "status": "checked", "result": value}
        except KnowledgeError as exc:
            return {
                "status": exc.code, "message": str(exc),
                "error_code": exc.category,
                "diagnostic_id": exc.diagnostic_id or diagnostic.correlation_id,
            }
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
            diagnostic.emit(
                "knowledge.request.failed", category=UNEXPECTED_EXCEPTION,
                stage=str(params.get("action", ""))[:120],
                exception=type(exc).__name__,
            )
            return {
                "status": "failed",
                "message": "知识源检查未完成；原文及工程默认保留",
                "error_code": UNEXPECTED_EXCEPTION,
                "diagnostic_id": diagnostic.correlation_id,
            }
        finally:
            with self.lock:
                self.active.pop(key, None)
