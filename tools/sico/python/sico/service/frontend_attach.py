"""Server-side creation/attachment; session dependencies stay in the project service."""

import math
import os
import threading
import time
import uuid
from contextlib import ExitStack
from types import SimpleNamespace

from ..core.contracts import BoundContext
from ..demo import DemoPeer
from ..transport.bridge_client import BridgeClient
from ..transport.broker import ContextBroker
from .connection import connection_instructions
from .frontend_session import SessionToken
from .quick_routing import QuickInputRouting, design_keys
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress
from .service_values import name
from ..storage.roots import agent_root


def session_record(controller, descriptor):
    view = controller.frontend_view
    return dict(address=SessionAddress(descriptor.project_id, descriptor.service_id,
                    SessionToken(view.session_id, view.runtime_id)).record(),
                context=view.context, label=view.label, base=view.base, model=view.model,
                resources=view.resources, display_name=controller.display_name,
                activity=controller.activity(),
                closing=controller.closing or controller._shutdown.is_set())


class FrontendAttach:
    def __init__(self, owner, descriptor):
        self.owner, self.descriptor = owner, descriptor
        self._creation = threading.Lock()

    def execute(self, wire, operation, params):
        created = False
        if operation == "platform_open":
            from .platform_connection import open_platform

            controller, created = open_platform(self, wire, params)
        elif operation == "attach":
            exact_fields(params, {"session_id"})
            controller = self.owner.controllers.get(name(params["session_id"]))
            if controller is None or controller.closing or controller._shutdown.is_set():
                raise ValueError("会话未在当前服务运行；历史只读，恢复需明确操作")
        elif operation == "open":
            exact_fields(params, {"session_id", "bridge", "context", "provider_config",
                                  "environment"})
            name(params["session_id"])
            if not isinstance(params["context"], BoundContext):
                raise ValueError("需要捕获的设计目标")
            self._dependencies(params)
            self._new(params["session_id"])
            if not os.path.samefile(self.owner.project, params["context"].snapshot.get("cwd", "")):
                raise ValueError("设计目标属于另一项目")
            with BridgeClient(params["bridge"]) as broker:
                bound = params["context"]
                if broker.context(bound.instance_id, bound.target_id) != bound:
                    raise ValueError("设计目标与捕获来源不一致")
            with self.owner._mutation:
                controller = self._existing_target(params)
                if controller is None:
                    if wire.closed.is_set():
                        raise ValueError("连接等待已结束")
                    controller = self.owner.open(params["session_id"], params["bridge"], bound,
                        provider_config=params["provider_config"],
                        environment=params["environment"],
                        new_only=True)
                    created = True
        elif operation in {"demo", "connect"}:
            exact_fields(params, {"session_id", "provider_config", "environment", "timeout",
                                  "context_file"})
            self._dependencies(params)
            if (type(params["timeout"]) not in (int, float)
                    or not math.isfinite(params["timeout"]) or not 0 < params["timeout"] <= 300
                    or (params["context_file"] is not None
                        and not isinstance(params["context_file"], str))):
                raise ValueError("Invalid local connection parameters")
            with self._creation:
                created = params["session_id"] not in self.owner.controllers
                controller = self._local(wire, operation, params)
        else:
            raise ValueError("Unknown frontend attach operation")
        return dict(session=session_record(controller, self.descriptor), created=created)

    def _existing_target(self, params):
        """Reopening an owned design observes its captured session configuration."""
        context = params["context"]
        for session_id, controller in self.owner.controllers.items():
            deps = self.owner.dependencies_for(session_id)
            if deps.bridge_descriptor != params["bridge"]:
                continue
            if (controller.current == context or QuickInputRouting._affinity(
                    controller, context, design_keys(context)) == 2):
                if controller.closing or controller._shutdown.is_set():
                    raise ValueError("原会话正在结束，请核对后重新打开")
                return controller
        return None

    @staticmethod
    def _dependencies(params):
        from ..transport.framing import ProtocolError

        if (params["provider_config"] is not None
                and not isinstance(params["provider_config"], dict)):
            raise ProtocolError("Invalid frontend provider configuration")
        env = params["environment"]
        if not isinstance(env, dict) or len(env) > 2048 or any(
                not isinstance(v, str) or not k or "=" in k or "\0" in k or "\0" in v
                for k, v in env.items()):
            raise ProtocolError("Invalid frontend environment")

    def _new(self, session_id):
        name(session_id)
        if (session_id not in self.owner.controllers
                and (agent_root(self.owner.project) / "sessions" / session_id).exists()):
            raise ValueError("历史会话只读；恢复执行需明确操作")

    def _local(self, wire, mode, params):
        if wire.closed.is_set():
            raise ValueError("连接等待已结束")
        self._new(params["session_id"])
        if params["session_id"] in self.owner.controllers:
            existing = self.owner.controllers[params["session_id"]]
            deps = self.owner.dependencies_for(params["session_id"])
            if (deps.environment != params["environment"]
                    or deps.provider_config != params["provider_config"]
                    or deps.bridge_descriptor
                    or (mode == "demo") !=
                       (existing.current.instance_id == "demo-instance")):
                raise ValueError("会话已使用不同依赖运行，请使用只读关联入口")
            return existing
        with ExitStack() as pending:
            broker = pending.enter_context(ContextBroker(min(params["timeout"], 30)))
            if mode == "demo":
                context = BoundContext("demo-instance", uuid.uuid4().hex, "bound", dict(
                    source="simulated", valid=True, cwd=str(self.owner.project),
                    cellview=dict(lib="demo", cell="amp", view="schematic")))
                peer = DemoPeer(broker, context, cancelled=wire.closed.is_set)
                pending.callback(peer.close)
            else:
                from ..storage.journal import private_dir

                instance, generation = uuid.uuid4().hex, uuid.uuid4().hex
                broker.expected_identity = instance, generation
                directory = agent_root(self.owner.project) / ("connect-" + generation)
                private_dir(directory)
                def cleanup():
                    (directory / "connection.json").unlink(missing_ok=True)
                    directory.rmdir()
                pending.callback(cleanup)
                wire.progress = connection_instructions(SimpleNamespace(directory=directory),
                    broker, instance, generation, context_file=params["context_file"],
                    environment=params["environment"])
                deadline = time.monotonic() + params["timeout"]
                while True:
                    if wire.closed.is_set() or time.monotonic() >= deadline:
                        raise ValueError("连接等待已结束")
                    try:
                        context = broker.context(instance, timeout=0.05)
                        break
                    except TimeoutError:
                        pass
                if (context.instance_id, context.generation) != (instance, generation):
                    raise ValueError("Relay source identity mismatch")
            if wire.closed.is_set():
                raise ValueError("连接等待已结束")
            # Retirement must not pass publication before local resources transfer.
            with self.owner._mutation:
                controller = self.owner.open(params["session_id"], {}, context,
                    provider_config=params["provider_config"], environment=params["environment"],
                    bridge=broker, new_only=True)
                if mode == "demo":
                    self.owner.own_resource(controller, peer.close)
                else:
                    self.owner.own_resource(controller, cleanup)
                pending.pop_all()  # The session owner now owns all local resources.
            return controller

    def close(self):
        pass  # Published demo peers/connect files belong to their session owner.
