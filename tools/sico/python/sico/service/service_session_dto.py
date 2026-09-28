"""Project/service-scoped session addresses and opaque replay cursor values."""

from dataclasses import dataclass

from ..transport.framing import ProtocolError
from .frontend_session import SessionToken
from .service_protocol import exact_fields, require_id
from .service_values import integer, name


@dataclass(frozen=True)
class SessionAddress:
    project_id: str
    service_id: str
    session: SessionToken

    def __post_init__(self):
        require_id(self.project_id)
        require_id(self.service_id)
        if type(self.session) is not SessionToken:
            raise ProtocolError("A session identity token is required")
        name(self.session.session_id)
        if self.session.runtime_id is not None:
            require_id(self.session.runtime_id)

    def record(self):
        return dict(project_id=self.project_id, service_id=self.service_id,
                    session_id=self.session.session_id, runtime_id=self.session.runtime_id)

    @classmethod
    def from_record(cls, row):
        exact_fields(row, {"project_id", "service_id", "session_id", "runtime_id"})
        return cls(row["project_id"], row["service_id"],
                   SessionToken(row["session_id"], row["runtime_id"]))


@dataclass(frozen=True)
class ReplayCursor:
    """Position in a server-owned subscription; not an EventCursor or stream handle.

    A null runtime is permitted only for read-only closed history. Subscription IDs
    never grant authority; AS-04 must resolve them against the owning connection.
    """

    address: SessionAddress
    connection_id: str
    subscription_id: str
    activation: int
    instance_id: str
    generation: str
    sequence: int
    committed: int
    state_version: int
    readonly: bool = False

    def __post_init__(self):
        if type(self.address) is not SessionAddress:
            raise ProtocolError("A scoped session address is required")
        require_id(self.connection_id)
        require_id(self.subscription_id)
        name(self.instance_id)
        name(self.generation)
        for value in (self.activation, self.sequence, self.committed):
            integer(value)
        integer(self.state_version, -1)
        if (type(self.readonly) is not bool or self.sequence > self.committed
                or (not self.readonly and self.address.session.runtime_id is None)):
            raise ProtocolError("Invalid replay watermark or history mode")

    @property
    def identity(self):
        return (self.address.session.session_id, self.address.session.runtime_id,
                self.instance_id, self.generation)

    def record(self):
        return dict(address=self.address.record(), connection_id=self.connection_id,
                    subscription_id=self.subscription_id, activation=self.activation,
                    instance_id=self.instance_id, generation=self.generation,
                    sequence=self.sequence, committed=self.committed,
                    state_version=self.state_version, readonly=self.readonly)

    @classmethod
    def from_record(cls, row):
        exact_fields(row, {"address", "connection_id", "subscription_id", "activation",
                           "instance_id", "generation", "sequence", "committed",
                           "state_version", "readonly"})
        return cls(**dict(row, address=SessionAddress.from_record(row["address"])))

    def require_binding(self, address, activation, connection_id):
        if (self.address != address or self.activation != activation
                or self.connection_id != connection_id):
            raise ProtocolError("Replay cursor belongs to a retired page or connection")

    def require_successor(self, previous):
        """Check a position update without performing any journal/source validation."""
        self.require_binding(previous.address, previous.activation, previous.connection_id)
        if (self.subscription_id != previous.subscription_id or self.identity != previous.identity
                or self.readonly != previous.readonly or self.sequence < previous.sequence
                or self.committed < previous.committed
                or self.state_version < previous.state_version
                or (self.readonly and self.committed != previous.committed)):
            raise ProtocolError("Replay cursor regressed or changed subscription")
