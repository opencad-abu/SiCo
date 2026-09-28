"""Publish catalog observations without retiring a later attached runtime."""

import time

from ..transport.framing import ProtocolError
from .published import freeze
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress


class RemoteCatalog:
    def __init__(self, api):
        self.api = api
        self._view = freeze(dict(version=0, rows=(), retained={}, availability={}, error="",
                                 complete=False))
        self._receipt, self._queried = None, 0

    def snapshot(self, version=None):
        if (not self.api.detached and time.monotonic() - self._queried > 0.5
                and (self._receipt is None or self._receipt.done())):
            self.refresh()
        return None if version == self._view["version"] else self._view

    def refresh(self):
        """Return a fresh service inventory; do not attach to or take control of sessions."""
        self._queried = time.monotonic()
        captured = self.api.opened_sessions()
        self._receipt = self.api._queries.request(
            "catalog", dict(version=self._view["version"]),
            lambda row: self._publish(row, captured))
        return self._receipt

    def _publish(self, row, captured):
        exact_fields(row, {"catalog", "sessions"})
        catalog = row["catalog"]
        if catalog is not None and (not isinstance(catalog, dict)
                                    or type(catalog.get("complete")) is not bool):
            raise ProtocolError("Invalid catalog scan completion")
        for session in row["sessions"]:
            self.api._sessions.publish(session, catalog=True)
        live = {value["address"]["session_id"] for value in row["sessions"]}
        for token in captured:
            if (token.runtime_id is not None and token.session_id not in live
                    and self.api.owns(token)):
                self.api._sessions.mark_closing(token)
        if row["catalog"] is not None:
            self._view = row["catalog"]
        return tuple(SessionAddress.from_record(session["address"]).session
                     for session in row["sessions"])
