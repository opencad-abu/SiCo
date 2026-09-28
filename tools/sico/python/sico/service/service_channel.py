"""Backend-only sequential connection exchange; never retries an operation."""

from contextlib import ExitStack, contextmanager

from ..transport.framing import ProtocolError, SendError, SendTimeout
from .control_contract import ControlRefused
from .service_errors import RequestUnsent, ResultUnknown
from .service_handshake import ServiceStopping, accept_ready, hello_message
from .service_messages import ServiceReceipt, ServiceRequest
from .service_peer import check_wait, service_connection
from .service_protocol import CAPABILITIES, heartbeat_message, validate_heartbeat_ack


class ServiceChannel:
    def __init__(self, connection, identity):
        self.identity = identity
        self._connection = connection
        self._next_request = 1
        self._failed = False

    def exchange(self, request, *, deadline, cancelled=None):
        if type(request) is not ServiceRequest:
            raise TypeError("A ServiceRequest is required")
        if self._failed:
            raise ProtocolError("Service connection is unusable after failed exchange")
        try:
            check_wait(deadline, cancelled)
        except TimeoutError as exc:
            raise RequestUnsent("Service request expired before send") from exc
        row = request.record(self.identity, self._next_request)
        try:
            try:
                self._connection.send(row, deadline=deadline, cancelled=cancelled)
            except (SendTimeout, SendError) as exc:
                if exc.sent == 0:
                    raise RequestUnsent("Service request failed before send") from exc
                raise ResultUnknown(request.operation_id, request.method) from exc
            reply = self._connection.receive(deadline=deadline, cancelled=cancelled)
            result = ServiceReceipt.from_record(reply, row, self.identity)
        except ControlRefused:
            self._next_request += 1
            raise
        except RequestUnsent:
            self._fail()
            raise
        except (OSError, EOFError, TimeoutError) as exc:
            self._fail()
            raise ResultUnknown(request.operation_id, request.method) from exc
        except BaseException:
            self._fail()
            raise
        self._next_request += 1
        return result

    def heartbeat(self, *, deadline, cancelled=None):
        """Probe this authenticated connection without consuming request sequence."""
        if self._failed:
            raise ProtocolError("Service connection is unusable after failed exchange")
        try:
            self._connection.send(heartbeat_message(self.identity), deadline=deadline,
                                  cancelled=cancelled)
            reply = self._connection.receive(deadline=deadline, cancelled=cancelled)
            validate_heartbeat_ack(reply, self.identity)
            return True
        except BaseException:
            self._fail()
            raise

    def _fail(self):
        self._failed = True
        self._connection.close()


@contextmanager
def service_channel(descriptor, *, deadline, cancelled=None, purpose="inspect", client_id=None,
                    capabilities=CAPABILITIES, required=CAPABILITIES):
    hello = hello_message(descriptor, purpose=purpose, client_id=client_id,
                          capabilities=capabilities, required=required)
    with ExitStack() as stack:
        try:
            check_wait(deadline, cancelled)
            connection = stack.enter_context(service_connection(descriptor, deadline=deadline))
            check_wait(deadline, cancelled)
            connection.send(hello, deadline=deadline, cancelled=cancelled)
            reply = connection.receive(deadline=deadline, cancelled=cancelled)
            identity = accept_ready(reply, hello)
        except ServiceStopping:
            raise
        except (OSError, EOFError) as exc:
            raise RequestUnsent("Service connection failed before request") from exc
        yield ServiceChannel(connection, identity)
