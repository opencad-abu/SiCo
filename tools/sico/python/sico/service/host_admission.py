"""Resolve one captured Ask to a durable destination, then use the sole inbox."""

import os
import uuid

from ..core.contracts import BoundContext
from ..storage.host_routes import HostRoutes
from ..storage.roots import agent_root
from ..transport.bridge_client import BridgeClient
from ..transport.framing import ProtocolError
from .frontend_session import SessionToken
from .host_contract import capture_payload, digest, host_identity
from .quick_routing import select_destination, validate_source
from .service_business_dto import InputResult
from .service_session_dto import SessionAddress


class HostAdmission:
    def __init__(self, owner, descriptor, inputs):
        self.owner, self.descriptor, self.inputs = owner, descriptor, inputs
        self.routes = HostRoutes(owner.project, descriptor.project_id)

    def execute(self, request):
        input_id, params = request["operation_id"], request["params"]
        if request["method"] == "host.input":
            return self.query(params["host"], input_id)
        bridge_descriptor = params["bridge"]
        host = host_identity(bridge_descriptor)
        # Fetch only from the captured authenticated instance, never a new discovery.
        with BridgeClient(bridge_descriptor) as bridge:
            payload = capture_payload(bridge.host_payload(params["payload_id"]))
            if digest(payload) != params["digest"]:
                raise ProtocolError("Host payload changed")
            message = payload["message"]
            context = BoundContext.from_record(message["context"])
            if (message["id"] != input_id or context.instance_id != host["instance_id"]
                    or context.generation != host["generation"]):
                raise ProtocolError("Host payload belongs to another operation")
            route = self.routes.read(host, input_id)
            if route is not None and route["payload_digest"] != digest(payload):
                return InputResult(None, input_id, "rejected", "operation_conflict")
            if route is not None and route["address"] is not None:
                return self.inputs.accept(SessionAddress.from_record(route["address"]),
                                          input_id, message["text"], message["context"])
            if route is not None and route["service_id"] != self.descriptor.service_id:
                return InputResult(None, input_id, "rejected", "stale_session")
            validate_source(context)
            if not os.path.samefile(self.owner.project, context.snapshot.get("cwd", "")):
                raise ValueError("Host target belongs to another project")
            if route is None:
                candidates = [item for key, item in self.owner.controllers.items()
                              if self._matches(key, bridge_descriptor, payload)]
                preferred = candidates[0] if candidates else None
                destination = select_destination(preferred, candidates, context)
                route = dict(contract="sico_host_route.v1", project_id=self.descriptor.project_id,
                             host=host, input_id=input_id, service_id=self.descriptor.service_id,
                             session_id=destination.session_id if destination else uuid.uuid4().hex,
                             payload_digest=digest(payload), address=None)
                self.routes.write(host, input_id, route)
            controller = self.owner.controllers.get(route["session_id"])
            if controller is None:
                path = agent_root(self.owner.project) / "sessions" / route["session_id"]
                if path.exists():
                    return InputResult(None, input_id, "rejected", "history_readonly")
                bridge.register_target(context)
                controller = self.owner.open(route["session_id"], bridge_descriptor, context,
                                             provider_config=payload["provider_config"],
                                             environment=payload["environment"], new_only=True)
            address = SessionAddress(self.descriptor.project_id, self.descriptor.service_id,
                                     SessionToken(controller.session_id, controller.runtime_id))
            route["address"] = address.record()
            self.routes.write(host, input_id, route)
            return self.inputs.accept(address, input_id, message["text"], message["context"])

    def _matches(self, session_id, bridge, payload):
        dependencies = self.owner.dependencies_for(session_id)
        return (dependencies.bridge_descriptor == bridge
                and dependencies.provider_config == payload["provider_config"]
                and dependencies.environment == payload["environment"])

    def query(self, host, input_id):
        route = self.routes.read(host, input_id)
        if route is None or route["address"] is None:
            return InputResult(None, input_id, "unknown")
        return self.inputs.query(SessionAddress.from_record(route["address"]), input_id)
