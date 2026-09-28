"""Bounded observations of an explicitly requested session end."""

from dataclasses import dataclass

from ..transport.framing import ProtocolError
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress
from .service_values import name


@dataclass(frozen=True)
class SessionEndView:
    address: SessionAddress
    operation_id: str
    state: str

    def __post_init__(self):
        if type(self.address) is not SessionAddress or self.address.session.runtime_id is None:
            raise ProtocolError("Session end requires its captured runtime")
        name(self.operation_id)
        if self.state not in {"unknown", "closing", "ended", "needs_reconcile"}:
            raise ProtocolError("Invalid session end observation")

    def record(self):
        return dict(address=self.address.record(), operation_id=self.operation_id, state=self.state)

    @classmethod
    def from_record(cls, row, address, operation_id):
        exact_fields(row, {"address", "operation_id", "state"})
        value = cls(SessionAddress.from_record(row["address"]), row["operation_id"], row["state"])
        if value.address != address or value.operation_id != operation_id:
            raise ProtocolError("Session end belongs to another request")
        return value
