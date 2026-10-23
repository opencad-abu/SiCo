"""Tool-independent Ask admission; the service owns routing and durable receipts."""

import os

from ..core.contracts import BoundContext, identifier
from ..transport.host_payload_contract import capture_payload
from .host_gateway import HostServiceGateway


class FrontendAsk:
    def __init__(self, project, context, *, provider_config=None, environment=None):
        if not os.path.samefile(project, context.snapshot.get("cwd", "")):
            raise ValueError("Host project mismatch")
        self.context = BoundContext.from_record(context.record())
        self._gateway = HostServiceGateway(project, self.context, provider_config=provider_config,
                                           environment=environment)

    def submit(self, text, request_id, attachment=None, *, ask_sequence=None):
        message = dict(kind="submit", id=request_id, text=text, context=self.context.record())
        if ask_sequence is not None:
            message["ask_sequence"] = ask_sequence
        payload = dict(message=message, provider_config=None, environment={})
        if attachment is not None:
            payload["attachment_text"] = attachment
        capture_payload(payload)
        options = {"attachment_text": attachment} if attachment is not None else {}
        return self._gateway.submit(message, **options)

    def query(self, request_id):
        if identifier(request_id) != self.context.target_id:
            raise ValueError("Ask query must identify its captured request")
        return self._gateway.query(request_id)

    def close(self):
        self._gateway.close()
