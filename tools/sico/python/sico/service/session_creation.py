"""Retain bounded creation inputs after a runtime releases all execution resources."""

from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass

from ..core.contracts import BoundContext
from .frontend_session import SessionToken


@dataclass(frozen=True)
class CreationSource:
    dependencies: object
    context: BoundContext


class SessionCreation:
    LIMIT = 64

    def __init__(self, owner):
        self.owner = owner
        self._retired = OrderedDict()

    def retain(self, controller, dependencies):
        token = SessionToken(controller.session_id, controller.runtime_id)
        self._retired[token] = CreationSource(dependencies, controller.current)
        while len(self._retired) > self.LIMIT:
            self._retired.popitem(last=False)

    def source(self, token):
        source = self._retired.get(token)
        if source is None:
            raise ValueError("原会话创建来源已失效，请重新打开项目")
        return source

    def dependencies(self, session_id):
        return tuple(source.dependencies for token, source in reversed(self._retired.items())
                     if token.session_id == session_id)

    def create(self, token, session_id, context):
        source = self.source(token)
        if context != source.context:
            raise ValueError("原会话目标已变化，请重新选择项目入口")
        deps = source.dependencies
        with ExitStack() as pending:
            bridge = None
            peer = None
            if context.snapshot.get("source") == "simulated" and not deps.bridge_descriptor:
                from ..demo import DemoPeer
                from ..transport.broker import ContextBroker

                bridge = pending.enter_context(ContextBroker(3))
                peer = DemoPeer(bridge, context)
                pending.callback(peer.close)
            controller = self.owner.open(session_id, dict(deps.bridge_descriptor), context,
                provider_config=deps.provider_config, environment=deps.environment,
                bridge=bridge, new_only=True, background=deps.background)
            pending.pop_all()
            if peer is not None:
                self.owner.own_resource(controller, peer.close)
            return controller

    def clear(self):
        self._retired.clear()
