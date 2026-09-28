"""Typed lifecycle requests/receipts bound to one authenticated connection exchange."""

import uuid
from dataclasses import dataclass

from ..transport.framing import ProtocolError
from .service_handshake import ConnectionIdentity
from .service_operations import OperationStatus
from .service_protocol import METHODS, envelope, request_message, result_message, validate_result
from .service_status import ServiceStatus
from .service_values import name


@dataclass(frozen=True)
class ServiceRequest:
    """Only implemented capabilities are callable; session DTOs do not enable methods."""

    method: str = "service.status"
    operation_id: str = None
    params: dict = None
    control: dict = None

    def __post_init__(self):
        if not isinstance(self.method, str) or self.method not in METHODS:
            raise ProtocolError("Unsupported service method")
        if self.operation_id is None:
            object.__setattr__(self, "operation_id", uuid.uuid4().hex)
        name(self.operation_id)
        if self.method in ("service.status", "service.stop"):
            if self.params not in (None, {}):
                raise ProtocolError("Lifecycle requests do not accept parameters")
        elif self.method == "service.operation":
            if self.params is not None:
                raise ProtocolError("Operation lookup uses operation_id as its parameter")
        elif self.method == "session.control":
            from .control_contract import control_params
            from .service_values import json_view

            control_params(self.params)
            object.__setattr__(self, "params", json_view(self.params))
        elif self.method.startswith("frontend."):
            from .frontend_codec import transfer_params

            object.__setattr__(self, "params", transfer_params(self.params))
        else:
            from .service_business_dto import business_params

            object.__setattr__(self, "params", business_params(self.method, self.params))
        if self.control is not None:
            from .service_protocol import control_claim
            from .service_values import json_view

            if self.method not in {"session.submit", "frontend.command"}:
                raise ProtocolError("Control claim is not valid for this method")
            claim = json_view(self.control)
            if not isinstance(claim, dict):
                raise ProtocolError("Control claim must be a mapping")
            control_claim(claim, claim.get("client_id"))
            object.__setattr__(self, "control", claim)

    def record(self, identity, request_id):
        params = (self.params or {}) if self.method != "service.operation" else {
            "operation_id": self.operation_id}
        return request_message(identity, self.method, request_id,
                               operation_id=self.operation_id, params=params, control=self.control)


@dataclass(frozen=True)
class ServiceReceipt:
    """A validated server observation; stop_requested is not durable business acceptance."""

    identity: ConnectionIdentity
    request_id: int
    operation_id: str
    method: str
    status: object

    def record(self):
        request = dict(envelope(self.identity), method=self.method, request_id=self.request_id,
                       operation_id=self.operation_id)
        return result_message(request, self.status.record())

    @classmethod
    def from_record(cls, row, request, identity):
        if any(request[key] != value for key, value in {
                "project_id": identity.project_id,
                "service_id": identity.service_id,
                "client_id": identity.client_id,
                "connection_id": identity.connection_id,
        }.items()):
            raise ProtocolError("Service receipt identity is not captured connection")
        result = validate_result(row, request)
        if request["method"].startswith("frontend."):
            from .frontend_codec import TransferResult

            status = TransferResult(result)
        elif request["method"] == "session.control":
            from .control_contract import ControlView, validate_control_result

            if result == {"error": "control_refused"}:
                from .control_contract import ControlRefused

                raise ControlRefused("控制权状态已变化，请刷新后明确操作")
            status = ControlView.from_record(result)
            validate_control_result(status, request["params"], identity.client_id)
        elif request["method"].startswith(("session.", "host.")):
            from .service_business_dto import business_result

            status = business_result(result, request)
        else:
            status = (OperationStatus.from_record(result)
                      if request["method"] == "service.operation"
                      else ServiceStatus.from_record(result, request["method"]))
        if (isinstance(status, OperationStatus)
                and status.operation_id != request["params"]["operation_id"]):
            raise ProtocolError("Operation result is not the captured lookup")
        return cls(identity, request["request_id"], request["operation_id"],
                   request["method"], status)
