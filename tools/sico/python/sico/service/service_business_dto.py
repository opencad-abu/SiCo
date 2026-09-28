"""Small session admission and durable input receipts, without runtime handles."""

from dataclasses import dataclass

from ..core.contracts import BoundContext
from ..storage.inbox_records import INPUT_STATES
from ..transport.framing import ProtocolError
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress
from .service_values import json_view, name

CODES = {"", "unavailable", "capacity", "input_rejected", "operation_conflict",
         "stale_session", "history_readonly", "unconfirmed", "control_required"}


def business_params(method, params):
    params = json_view(params)
    if method.startswith("host."):
        from .host_contract import validate_query, validate_reference

        (validate_reference if method == "host.submit" else validate_query)(params)
        return params
    if method == "session.open":
        exact_fields(params, {"session_id", "context", "bridge", "provider_config", "environment"})
        name(params["session_id"])
        for key in ("provider_config", "environment"):
            if params[key] is not None and not isinstance(params[key], dict):
                raise ProtocolError("Invalid session dependency mapping")
        if params["environment"] is not None and any(
                not isinstance(v, str) or "\0" in k or "=" in k or "\0" in v
                for k, v in params["environment"].items()):
            raise ProtocolError("Invalid session environment")
        if not isinstance(params["bridge"], dict):
            raise ProtocolError("Invalid bridge descriptor")
    else:
        exact_fields(params, {"address", "text", "context"} if method == "session.submit"
                     else {"address"})
        address = SessionAddress.from_record(params["address"])
        if address.session.runtime_id is None:
            raise ProtocolError("Input operations require their captured runtime")
    if method in {"session.open", "session.submit"}:
        exact_fields(params["context"], {"contract", "instance_id", "generation",
                                         "target_id", "snapshot"})
        try:
            BoundContext.from_record(params["context"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProtocolError("Invalid captured target") from exc
    if method == "session.submit" and (not isinstance(params["text"], str)
                                      or not params["text"].strip() or "\0" in params["text"]):
        raise ProtocolError("Invalid service input text")
    return params


@dataclass(frozen=True)
class SessionResult:
    outcome: str
    code: str
    address: object = None

    def __post_init__(self):
        if (not isinstance(self.outcome, str) or not isinstance(self.code, str)
                or self.outcome not in {"opened", "rejected", "unknown"} or self.code not in CODES
                or (self.outcome == "opened") != (self.address is not None)
                or (self.outcome == "opened") != (self.code == "")):
            raise ProtocolError("Invalid session admission result")
        if self.address is not None:
            if type(self.address) is not SessionAddress or self.address.session.runtime_id is None:
                raise ProtocolError("Invalid live session result")

    def record(self):
        return dict(outcome=self.outcome, code=self.code,
                    address=None if self.address is None else self.address.record())

    @classmethod
    def from_record(cls, row):
        exact_fields(row, {"outcome", "code", "address"})
        return cls(row["outcome"], row["code"], None if row["address"] is None
                   else SessionAddress.from_record(row["address"]))


@dataclass(frozen=True)
class InputResult:
    address: SessionAddress
    input_id: str
    outcome: str
    code: str = ""
    execution: str = None
    task_id: str = None
    recovery_required: bool = False

    def __post_init__(self):
        name(self.input_id)
        if self.address is not None and (type(self.address) is not SessionAddress
                                        or self.address.session.runtime_id is None):
            raise ProtocolError("Invalid input result address")
        if (not isinstance(self.outcome, str) or not isinstance(self.code, str)
                or self.outcome not in {"accepted", "unknown", "rejected"} or self.code not in CODES
                or type(self.recovery_required) is not bool):
            raise ProtocolError("Invalid durable input result")
        if self.outcome == "accepted":
            if (self.address is None or not isinstance(self.execution, str)
                    or self.execution not in INPUT_STATES or self.code):
                raise ProtocolError("Invalid accepted execution state")
        elif self.execution is not None or self.task_id is not None or self.recovery_required:
            raise ProtocolError("Unconfirmed input cannot claim execution")
        if self.outcome == "rejected" and not self.code:
            raise ProtocolError("Missing input rejection reason")
        if self.task_id is not None:
            name(self.task_id)

    def record(self):
        return dict(address=None if self.address is None else self.address.record(),
                    input_id=self.input_id, outcome=self.outcome,
                    code=self.code, execution=self.execution, task_id=self.task_id,
                    recovery_required=self.recovery_required)

    @classmethod
    def from_record(cls, row):
        exact_fields(row, {"address", "input_id", "outcome", "code", "execution",
                           "task_id", "recovery_required"})
        return cls(**dict(row, address=None if row["address"] is None
                         else SessionAddress.from_record(row["address"])))


def business_result(row, request):
    if row == {"error": "protocol_error"}:
        raise ProtocolError("Service rejected damaged protocol or persistent evidence")
    if request["method"] == "session.open":
        result = SessionResult.from_record(row)
        if result.address is not None and (
                result.address.session.session_id != request["params"]["session_id"]
                or result.address.project_id != request["project_id"]
                or result.address.service_id != request["service_id"]):
            raise ProtocolError("Opened session is not the captured request")
    else:
        result = InputResult.from_record(row)
        if result.input_id != request["operation_id"]:
            raise ProtocolError("Input receipt is not the captured request")
        if request["method"].startswith("host."):
            if result.address is not None and result.address.project_id != request["project_id"]:
                raise ProtocolError("Host receipt belongs to another project")
        elif result.address is None or result.address.record() != request["params"]["address"]:
            raise ProtocolError("Input receipt is not the captured runtime")
    return result
