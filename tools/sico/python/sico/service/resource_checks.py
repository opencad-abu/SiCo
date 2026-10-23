"""Own isolated resource checks and cancellation by authenticated frontend identity."""

import threading
from types import SimpleNamespace

from cadai.env_names import value as env_value
from cadai.launch import resolve_agent_executable

from .agent_settings import scope
from .service_protocol import exact_fields
from .service_values import identifier


class ResourceChecks:
    def __init__(self, hub):
        self.hub = hub
        self.lock = threading.Lock()
        self.active, self.cancelled = {}, set()

    def cancel(self, wire, params):
        exact_fields(params, {"id"})
        identity = identifier(params["id"])
        key = wire.identity.client_id, identity
        with self.lock:
            if key in self.active:
                self.active[key].set()
            elif len(self.cancelled) < 64:
                self.cancelled.add(key)
        return {"cancel_requested": True}

    def run(self, wire, params):
        exact_fields(params, {"address", "id", "manifest", "call", "skill"})
        identity = identifier(params["id"])
        address = scope(self.hub, params["address"])
        key, stop = (wire.identity.client_id, identity), threading.Event()
        with self.lock:
            if key in self.cancelled:
                self.cancelled.remove(key)
                return {"status": "cancelled", "message": "资源检查已取消"}
            if key in self.active or len(self.active) >= 2:
                raise ValueError("已有资源检查正在执行，请稍后重试")
            self.active[key] = stop
        try:
            from ..codex.resource_probe import CheckError, check

            if address and address.session.runtime_id:
                settings = self.hub.owner.provider_for(address.session.session_id)
            else:
                settings = self.hub.owner.resource_settings()
            environment = dict(settings.environment)
            executable = settings.options.get("codex_executable", env_value(environment, "CODEX_CLI"))
            if executable:
                environment["SICO_CODEX_CLI"] = executable
            executable = resolve_agent_executable("codex", environment=environment)
            settings = SimpleNamespace(options=settings.options, environment=environment)
            def cancelled():
                return stop.is_set() or wire.closed.is_set() or self.hub.closed.is_set()
            value = check(self.hub.owner.project, params["manifest"], settings, executable,
                          cancelled, params["call"], params["skill"])
            scope(self.hub, params["address"])
            return {"status": "cancelled", "message": "资源检查已取消"} if cancelled() else {
                "status": "checked", **value}
        except CheckError as exc:
            return {"status": "failed", "message": str(exc)}
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            return {"status": "cancelled" if stop.is_set() else "failed",
                    "message": "检查未完成；请核对资源、环境引用和服务状态。若已提交工具调用，"
                               "其效果未确认，请勿自动重试。默认与当前会话未改动"}
        finally:
            with self.lock:
                self.active.pop(key, None)
