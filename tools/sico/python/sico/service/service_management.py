"""Small authenticated status/stop contract for the project service lifecycle."""

from dataclasses import asdict

from ..transport.framing import ProtocolError
from .service_channel import RequestUnsent, service_channel
from .service_errors import ResultUnknown, StopResultUnknown
from .service_operations import OperationStatus, OperationStore


def management_reply(method, lifecycle, allocation, operations=None, params=None,
                     operation_id=None):
    if method not in ("service.status", "service.stop", "service.operation"):
        raise ProtocolError("Unsupported service lifecycle method")
    operations = operations or OperationStore()
    params = {} if params is None else params
    if method == "service.operation":
        operation_id = params["operation_id"]
        row = operations.get(operation_id)
        return (OperationStatus(operation_id, "completed", row.method, row.result).record()
                if row is not None else OperationStatus(operation_id, "unknown", "unknown",
                                                        None).record())
    if operation_id is not None:
        existing = operations.get(operation_id)
        if existing is not None:
            if existing.method != method or existing.params != params:
                raise ProtocolError("Operation identity was reused with different parameters")
            return existing.result
    status = lifecycle.stop() if method == "service.stop" else lifecycle.status()
    outcome = "observed"
    if method == "service.stop":
        outcome = "stop_requested" if lifecycle.stopping else "blocked"
    result = dict(outcome=outcome, **status, allocation=asdict(allocation))
    if operation_id is not None:
        return operations.record(operation_id, method, params, result).result
    return result


def exchange_service(descriptor, request, *, deadline, cancelled=None, client_id=None):
    """Backend transport entry shared by async clients and discovery-based administration."""
    with service_channel(descriptor, deadline=deadline, cancelled=cancelled,
                         client_id=client_id) as channel:
        try:
            return channel.exchange(request, deadline=deadline, cancelled=cancelled)
        except RequestUnsent:
            raise
        except ResultUnknown as exc:
            if request.method == "service.stop":
                raise StopResultUnknown(
                    exc.operation_id, request.method,
                    "Stop may have been accepted; query the captured service") from exc
            raise
