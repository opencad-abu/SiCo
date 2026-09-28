"""Async recovery observations and explicit paused-runtime creation for one service."""

from .recovery_contract import recovery_view
from .service_session_dto import SessionAddress


class RemoteRecovery:
    def __init__(self, api):
        self.api = api

    def _publish(self, row, session_id, operation_id):
        recovery_view(row, self.api.descriptor, session_id, operation_id)
        # Inspection does not replace a runtime; only explicit open_session attaches it.
        return row

    def inspect(self, session_id, operation_id=None):
        return self.api._queries.request("recovery", dict(session_id=session_id,
            operation_id=operation_id), lambda row: self._publish(row, session_id, operation_id))

    def continue_session(self, session_id):
        from .service_protocol import exact_fields

        epoch = self.api._commands.epoch

        def publish(row):
            exact_fields(row, {"session", "control"})
            address = SessionAddress.from_record(row["session"]["address"])
            if (address.session.session_id != session_id or epoch != self.api._commands.epoch):
                raise ValueError("会话连接已变化，请重试")
            token = self.api._sessions.publish(row["session"])
            self.api.control.continued(token, row["control"])
            return token

        return self.api._commands.request("continue_session", dict(session_id=session_id,
            source_session_id=self.api.initial_id), publish)

    def open_history(self, session_id):
        from .frontend_session import FrontendSession

        def publish(row):
            self._publish(row, session_id, None)
            if row["session"] is not None:
                token = self.api._sessions.publish(row["session"])
            else:
                token = self.api._sessions.history(session_id, row["context"])
            if not self.api.initial_id:
                self.api.initial_id = session_id
            return FrontendSession(self.api, token)
        return self.api._queries.request("recovery", dict(session_id=session_id, operation_id=None),
                                        publish)

    def restore(self, view, operation_id, credentials=None):
        session_id, version = view["session_id"], view["version"]
        return self.api._commands.request("recover_session", dict(session_id=session_id,
            version=version, credentials=dict(credentials or {})),
            lambda row: self._publish(row, session_id, operation_id), operation_id=operation_id)

    def input_result(self, address, operation_id):
        from .service_messages import ServiceRequest

        address = SessionAddress.from_record(address)
        return self.api._admin.request(ServiceRequest("session.input", operation_id,
                                                      dict(address=address.record())))

    def end_result(self, address, operation_id):
        from .session_end_contract import SessionEndView

        address = SessionAddress.from_record(address)
        return self.api._commands.request("end_status", dict(address=address.record(),
            operation_id=operation_id), lambda row: SessionEndView.from_record(row, address,
                                                                              operation_id))
