"""Explicit recovery orchestration; inspection never opens a writer or execution backend."""

import threading
from contextlib import ExitStack

from ..core.contracts import identifier
from ..demo import DemoPeer
from ..transport.bridge_client import BridgeClient
from ..transport.broker import ContextBroker
from ..transport.framing import ProtocolError
from .cleanup_task import DEFAULT_CLEANUP_SECONDS, CleanupTask
from .frontend_attach import session_record
from .recovery_contract import recovery_request
from .recovery_facts import read_facts
from ..storage.roots import agent_root
from ..storage.record_removal import removed


class SessionRecovery:
    def __init__(self, owner, descriptor):
        self.owner, self.descriptor = owner, descriptor
        self._unresolved = frozenset()
        self._scanning = True
        self._scan_lock = threading.Lock()
        self._retired = set()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._scan, name="copilot-recovery", daemon=True)
        self._thread.start()
        self._cleanup = CleanupTask(self._join, "copilot-recovery-close")

    def _scan(self):
        unresolved = set()
        try:
            root = agent_root(self.owner.project) / "sessions"
            if not root.exists():
                self._unresolved = frozenset()
                return
            for path in root.glob("*"):
                if self._closed.is_set():
                    return
                try:
                    identifier(path.name)
                    if removed(root.parent, path.name):
                        continue
                    if read_facts(self.owner.project, path.name).unresolved:
                        unresolved.add(path.name)
                except Exception:
                    unresolved.add(path.name)  # Damage cannot establish absence of pending work.
            with self._scan_lock:
                self._unresolved = frozenset(unresolved - self._retired)
        except Exception:
            self._unresolved = frozenset({"<unverified>"})
        finally:
            self._scanning = False

    @property
    def pending(self):
        # Read-only projection; listener never scans or waits for journal I/O.
        return int(self._scanning) + len(self._unresolved - self.owner.controllers.keys())

    def retired(self, session_id):
        with self._scan_lock:
            self._retired.add(session_id)
            self._unresolved = self._unresolved - {session_id}

    def inspect(self, session_id, operation_id=None):
        identifier(session_id)
        if operation_id is not None:
            identifier(operation_id)
        with self.owner._mutation:
            controller = self.owner.controllers.get(session_id)
            return self._view(session_id, self._facts(session_id, operation_id),
                              controller, operation_id)

    def _facts(self, session_id, operation_id=None):
        controller = self.owner.controllers.get(session_id)
        if controller is None:
            facts = read_facts(self.owner.project, session_id, operation_id)
        else:
            # Lock ordering matches controller commands. Bytes visible before fsync
            # are not committed evidence and cannot confirm a recovery operation.
            with controller._lock, controller.journal._events_lock:
                facts = read_facts(self.owner.project, session_id, operation_id)
        if not facts.archived and facts.snapshot and facts.snapshot["project_id"] != self.descriptor.project_id:
            raise ProtocolError("Recovery configuration belongs to another project")
        return facts

    def _view(self, session_id, facts, controller, operation_id=None):
        config = None if facts.archived else (facts.snapshot or {}).get("provider_config")
        credential = config.get("api_key_env", "SICO_API_KEY") if config else None
        state = ("archived_readonly" if facts.archived else "live" if controller else "ended" if facts.deleted or
                 (facts.ends and not facts.resources_released) else
                 "legacy_readonly" if facts.snapshot is None else
                 "waiting_credentials" if credential else "ready")
        operation = None if facts.archived else next((row for row in facts.operations
                          if row["operation_id"] == operation_id), None)
        return dict(project_id=self.descriptor.project_id, service_id=self.descriptor.service_id,
                    session_id=session_id, version=facts.version, state=state,
                    credential=credential, context=facts.context,
                    task_id=facts.task.get("id", ""), task_status=facts.task.get("status", ""),
                    inputs=facts.inputs, session=None if controller is None else
                    session_record(controller, self.descriptor), operation=operation)

    def continue_session(self, connection, operation_id, key, preferred=None, selected=None):
        """Open one conversation from a single validated durable snapshot."""
        identifier(key)
        identifier(operation_id)
        with self.owner._mutation:
            facts = self._facts(key)
            if facts.snapshot is None or facts.deleted:
                raise ValueError("会话已删除或缺少配置，保持历史只读")
            controller = self._open(connection, operation_id, key, facts, continue_chat=True,
                                    preferred=preferred, selected=selected)
            with controller._lock:
                controller.recovery.continue_chat(facts)
            return controller

    def restore(self, connection, operation_id, params):
        recovery_request(params)
        identifier(operation_id)
        key = params["session_id"]
        with self.owner._mutation:
            if self._closed.is_set():
                raise ValueError("服务恢复入口已关闭")
            facts = self._facts(key, operation_id)
            if facts.archived:
                raise ValueError("迁移历史保持只读；请新建会话继续工作")
            existing = self.owner.controllers.get(key)
            previous = next((row for row in facts.operations
                             if row["operation_id"] == operation_id), None)
            if previous is not None:
                if previous["version"] != params["version"]:
                    raise ValueError("恢复操作 ID 与原请求不一致")
                return self._view(key, facts, existing, operation_id)
            if existing is not None:
                raise ValueError("会话已恢复，请只读关联后明确获取控制权")
            if facts.version != params["version"]:
                raise ValueError("恢复证据已变化，请刷新后明确操作")
            if facts.snapshot is None or facts.deleted:
                raise ValueError("会话已删除或缺少配置，保持历史只读")
            if facts.ends and not facts.resources_released:
                raise ValueError("原会话结束操作尚未确认释放资源，请先核对")
            snapshot = facts.snapshot
            config = snapshot["provider_config"]
            credential = config.get("api_key_env", "SICO_API_KEY") if config else None
            supplied = params["credentials"]
            if set(supplied) - ({credential} if credential else set()):
                raise ProtocolError("Only the captured credential reference is accepted")
            if credential and not supplied.get(credential):
                return self._view(key, facts, None)
            controller = self._open(connection, operation_id, key, facts, supplied)
            return self._view(key, self._facts(key, operation_id), controller, operation_id)

    def _open(self, connection, operation_id, key, facts, credentials=None, *, continue_chat=False,
              preferred=None, selected=None):
        if self._closed.is_set():
            raise ValueError("服务恢复入口已关闭")
        snapshot = facts.snapshot
        environment = dict(snapshot["environment"], **(credentials or {}))
        if continue_chat:
            from .recovery_environment import recovery_environment

            environment = recovery_environment(self.owner, key, snapshot)
        with ExitStack() as rollback, ExitStack() as peers:
            context = facts.context
            if continue_chat:
                from .continuation_host import current_host

                selected = selected or current_host(self.owner, preferred)
            if selected is not None:
                bridge, context = selected
                rollback.enter_context(bridge)
            else:
                bridge = (self._chat_bridge(facts, rollback, peers) if continue_chat
                          else self._bridge(facts, rollback, peers))
            if connection.closed.is_set():
                raise ValueError("恢复等待已取消")
            controller = self.owner.open(key, getattr(bridge, "descriptor", {}), context,
                provider_config=snapshot["provider_config"], environment=environment, bridge=bridge,
                background=snapshot.get("background"),
                recovery=dict(operation_id=operation_id, service_id=self.descriptor.service_id,
                              version=facts.version),
                previous_context=facts.context if context != facts.context else None)
            rollback.pop_all()  # The owner owns the bridge after publication.
            self.owner.own_resource(controller, peers.pop_all().close)
        return controller

    def _chat_bridge(self, facts, rollback, peers):
        from ..transport.unavailable_bridge import UnavailableBridge

        if facts.ends and not facts.resources_released:
            return UnavailableBridge()
        try:
            return self._bridge(facts, rollback, peers)
        except (OSError, ValueError, RuntimeError):
            # No current host is available. Chat remains usable until one reconnects.
            rollback.close()
            peers.close()
            return UnavailableBridge()

    def _bridge(self, facts, rollback, peers):
        context = facts.context
        if context.snapshot.get("source") == "simulated" and not facts.snapshot["bridge_identity"]:
            broker = rollback.enter_context(ContextBroker(3))
            peer = DemoPeer(broker, context)
            peers.callback(peer.close)
            return broker
        identity = facts.bridge_identity or facts.snapshot["bridge_identity"]
        if set(identity) != {"bridge_id", "router_id"}:
            raise ValueError("历史缺少可核验的宿主身份；保持只读，不能自动重绑")
        try:
            bridge = rollback.enter_context(BridgeClient.discover(
                self.owner.project, context, timeout=.2))
        except (ConnectionError, TimeoutError) as exc:
            raise ValueError("原 Virtuoso 连接已断开，暂不能恢复设计工具；对话历史已保留") from exc
        if any(bridge.descriptor.get(key) != value for key, value in identity.items()):
            raise ValueError("捕获的 bridge/router 已变化；不能自动重绑")
        if bridge.context(context.instance_id, context.target_id) != context:
            raise ValueError("捕获目标已变化；不能自动重绑")
        return bridge

    def close(self, timeout=DEFAULT_CLEANUP_SECONDS):
        self._closed.set()
        return self._cleanup.start().wait(timeout)

    def _join(self):
        self._thread.join()
