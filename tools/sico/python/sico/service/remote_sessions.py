"""One immutable publication per remote session; views derive from that authority."""

import threading
from dataclasses import dataclass, replace

from ..core.contracts import BoundContext
from ..transport.framing import ProtocolError
from .frontend_session import SessionToken, SessionView
from .published import freeze
from .remote_validation import session_view
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress


@dataclass(frozen=True)
class SessionPublication:
    address: SessionAddress
    context: BoundContext
    label: str
    base: str
    model: str
    resources: bool
    display_name: str
    activity: str
    closing: bool

    def __post_init__(self):
        context = replace(self.context)
        object.__setattr__(context, "snapshot", freeze(context.snapshot))
        object.__setattr__(self, "context", context)

    @property
    def view(self):
        token = self.address.session
        return SessionView(token.session_id, token.runtime_id, self.context,
                           self.label, self.base, self.model, self.resources)


class RemoteSessions:
    def __init__(self, descriptor):
        self._descriptor = descriptor
        self._rows = {}
        self._lock = threading.Lock()

    def get(self, session_id):
        return self._rows.get(session_id)

    def tokens(self):
        return tuple(row.address.session for row in self._rows.values())

    def publish(self, row, *, catalog=False):
        exact_fields(row, {"address", "context", "label", "base", "model", "resources",
                           "display_name", "activity", "closing"})
        address = SessionAddress.from_record(row["address"])
        session_view(row)
        if ((address.project_id, address.service_id) !=
                (self._descriptor.project_id, self._descriptor.service_id)
                or not isinstance(row["context"], BoundContext)
                or address.session.runtime_id is None):
            raise ProtocolError("Session view belongs to another service")
        publication = SessionPublication(**dict(row, address=address))
        with self._lock:
            previous = self.get(address.session.session_id)
            if catalog and previous is not None and previous.address.session != address.session:
                return previous.address.session
            if previous is not None and previous.address == address and previous.closing:
                publication = replace(publication, closing=True)
            self._rows = {**self._rows, address.session.session_id: publication}
        return address.session

    def history(self, session_id, context):
        token = SessionToken(session_id, None)
        address = SessionAddress(self._descriptor.project_id, self._descriptor.service_id, token)
        row = SessionPublication(address, context, "历史只读", "", "", False,
                                 session_id, "idle", True)
        with self._lock:
            self._rows = {**self._rows, session_id: row}
        return token

    def mark_closing(self, token):
        with self._lock:
            row = self.get(token.session_id)
            if row is not None and row.address.session == token:
                self._rows = {**self._rows, token.session_id: replace(row, closing=True)}
