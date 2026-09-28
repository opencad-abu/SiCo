"""Server-side connection state: negotiate once, validate each request before dispatch."""

from .service_handshake import negotiate
from .service_management import management_reply
from .service_operations import OperationStore
from .service_protocol import heartbeat_ack, result_message, validate_heartbeat, validate_request
from .session_control import ControlConnection


class ServiceWire:
    def __init__(self, operations=None, dispatch=None):
        self._identity = None
        self._attached = False
        self._next_request = 1
        self._operations = operations if operations is not None else OperationStore()
        self._dispatch = dispatch
        self._frontend = None
        self._control_connection = None

    def close(self):
        if self._control_connection is not None:
            self._control_connection.closed.set()
        if self._frontend is not None:
            self._frontend.close()

    def reply(self, message, descriptor, lifecycle, allocation):
        if self._identity is None:
            reply, self._identity = negotiate(message, descriptor, lifecycle)
            if self._identity is not None:
                self._attached = message["purpose"] == "attach"
                self._control_connection = ControlConnection(self._identity)
            return reply
        if message.get("kind") == "heartbeat":
            validate_heartbeat(message, self._identity)
            if self._attached:
                lifecycle.attach()
            return heartbeat_ack(self._identity)
        validate_request(message, self._identity, self._next_request)
        self._next_request += 1
        if message["method"].startswith("frontend."):
            from ..transport.framing import ProtocolError
            from .frontend_wire import FrontendWire

            if lifecycle.stopping or self._dispatch is None:
                raise ProtocolError("Frontend service is unavailable")
            if self._frontend is None:
                self._frontend = FrontendWire(self._dispatch.frontend, self._identity,
                                               self._control_connection)
            return result_message(message, self._frontend.reply(
                message["method"], message["params"], message["operation_id"], message["control"]))
        if message["method"].startswith(("session.", "host.")):
            if lifecycle.stopping or self._dispatch is None:
                from .service_business import ServiceBusiness

                return result_message(message,
                    ServiceBusiness.failure(message, "rejected", "unavailable"))
            return self._dispatch.submit(message, self._control_connection)
        result = management_reply(message["method"], lifecycle, allocation,
                                  self._operations, message["params"], message["operation_id"])
        return result_message(message, result)
