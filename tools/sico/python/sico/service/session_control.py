"""Authoritative per-runtime leases; mutation and takeover share one session gate."""

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from .control_contract import ControlRefused, ControlView, control_params
from .service_protocol import MAX_REQUEST_ID, ControlClaim, control_claim


@dataclass
class ControlConnection:
    identity: object
    closed: object = field(default_factory=threading.Event)


@dataclass
class _Lease:
    address: object
    gate: object = field(default_factory=threading.RLock)
    generation: int = 0
    holder: object = None
    claim: object = None


class SessionControl:
    def __init__(self, owner, descriptor):
        self.owner, self.descriptor = owner, descriptor
        self._entries = {}
        self._lock = threading.Lock()

    def _current(self, address):
        controller = self.owner.controllers.get(address.session.session_id)
        if (address.project_id != self.descriptor.project_id
                or address.service_id != self.descriptor.service_id or controller is None
                or controller.runtime_id != address.session.runtime_id
                or controller.closing or controller._shutdown.is_set()):
            raise ControlRefused("会话运行实例已失效，控制权未改变")

    def _entry(self, address):
        self._current(address)
        with self._lock:
            live = self.owner.controllers
            for key in tuple(self._entries):
                if key not in live:
                    del self._entries[key]
            entry = self._entries.get(address.session.session_id)
            if entry is None or entry.address != address:
                entry = _Lease(address)
                self._entries[address.session.session_id] = entry
            return entry

    @contextmanager
    def guard(self, address):
        entry = self._entry(address)
        with entry.gate:
            self._current(address)
            yield entry

    @staticmethod
    def _view(entry, connection, *, grant=False):
        holder = entry.holder
        if holder is None:
            state = "available"
        elif holder.identity.client_id == connection.identity.client_id:
            state = "recoverable" if holder.closed.is_set() else "owned"
        else:
            state = "detached" if holder.closed.is_set() else "occupied"
        return ControlView(entry.address, entry.generation, state,
                           entry.claim if grant else None)

    def require(self, entry, connection, proof):
        if proof is not None and connection is not None:
            claim = control_claim(proof, connection.identity.client_id)
        else:
            claim = None
        if (connection is None or connection.closed.is_set() or entry.holder is not connection
                or claim is None or claim != entry.claim):
            raise ControlRefused("当前为只读观察，控制权已失效；请明确获取、恢复或接管控制权")
        self._current(entry.address)

    def run(self, address, connection, proof, operation):
        with self.guard(address) as entry:
            self.require(entry, connection, proof)
            return operation()

    def execute(self, connection, params):
        address = control_params(params)
        if connection.closed.is_set():
            raise ControlRefused("原连接已断开，控制权未改变")
        with self.guard(address) as entry:
            action = params["action"]
            if action == "status":
                return self._view(entry, connection)
            if entry.generation != params["generation"]:
                raise ControlRefused("控制权状态已变化，请刷新后明确操作")
            previous = self._holder(entry)
            if action == "release":
                self.require(entry, connection, params["previous"])
                self._advance(entry)
                entry.holder, entry.claim = None, None
                self._record(address, action, connection, previous, entry)
                return self._view(entry, connection)
            if action == "acquire" and entry.holder is not None:
                raise ControlRefused("会话已有控制归属；请明确恢复或接管")
            if action == "resume":
                proof = control_claim(params["previous"], connection.identity.client_id)
                if (entry.holder is None or proof != entry.claim
                        or not entry.holder.closed.is_set()
                        or entry.holder.identity.client_id != connection.identity.client_id):
                    raise ControlRefused("原控制权不能恢复；请刷新后明确操作")
            if connection.closed.is_set():
                raise ControlRefused("连接已断开，控制权未改变")
            self._advance(entry)
            entry.holder = connection
            token = address.session
            entry.claim = ControlClaim(connection.identity.client_id, token.session_id,
                                       token.runtime_id, entry.generation, uuid.uuid4().hex)
            self._record(address, action, connection, previous, entry)
            return self._view(entry, connection, grant=True)

    def continue_session(self, connection, address):
        """Acquire free/disconnected control as part of the user's Continue action."""
        with self.guard(address) as entry:
            if connection.closed.is_set():
                raise ControlRefused("会话连接已断开")
            if entry.holder is connection:
                return self._view(entry, connection, grant=True)
            if entry.holder is not None and not entry.holder.closed.is_set():
                raise ControlRefused("其他窗口正在使用此会话")
            return self.execute(connection, dict(address=address.record(),
                action="acquire" if entry.holder is None else "takeover",
                generation=entry.generation, previous=None))

    @staticmethod
    def _holder(entry):
        """The control facts before an action: who held it, and whether that holder was gone."""

        if entry.holder is None:
            return {"client_id": "", "disconnected": False}
        return {"client_id": entry.holder.identity.client_id,
                "disconnected": entry.holder.closed.is_set()}

    def _record(self, address, action, connection, previous, entry):
        """Control authority is evidence: record the move in the session journal."""

        controller = self.owner.controllers.get(address.session.session_id)
        if controller is None:
            return
        controller.journal.append_session("session.control", dict(
            action=action, client_id=connection.identity.client_id,
            session_id=address.session.session_id, runtime_id=address.session.runtime_id,
            generation=entry.generation, previous_client_id=previous["client_id"],
            previous_disconnected=previous["disconnected"]), controller.current)

    @staticmethod
    def _advance(entry):
        if entry.generation >= MAX_REQUEST_ID:
            raise ControlRefused("控制代次已用尽，请结束会话后重新创建")
        entry.generation += 1
