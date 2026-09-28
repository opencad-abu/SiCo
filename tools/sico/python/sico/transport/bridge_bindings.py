"""Private host binding publication and rejection evidence."""

import logging

from ..core.contracts import BoundContext, CircuitCallError


def publish_binding(self, session_id, context, **metadata):
    """Private host publication. Desktop RPC cannot attest workflow provenance."""
    with self._changed:
        self.targets.add(context)
        self.broker.register_target(context)
        return self.broker.propose_binding(session_id, context, **metadata)

def native_binding_event(self, message):
    """Private pipe only. A rejected event must never swallow an operation reply."""
    from uuid import uuid4

    with self._changed, self.broker._changed:
        try:
            with self.targets.lock:
                context = (
                    BoundContext.from_record(message["context"])
                    if message.get("kind") == "binding.native_created"
                    else None
                )
                if context:
                    self.targets.validate(context)
                result = self.broker.native_bindings.event(message)
                if context:
                    self.targets.add(context)
            self._sync_binding_membership()
            return result
        except (ValueError, KeyError, TypeError, OSError, CircuitCallError) as error:
            request = self.broker.native_bindings.requests.get(message.get("request_id"), {})
            identity = request.get("identity", {})
            payload = dict(
                event_id=uuid4().hex,
                session_id=identity.get("session_id", ""),
                request_id=message.get("request_id", ""),
                native_event=message,
                code="binding_native_event_rejected",
                category="binding",
                message=str(error),
                automatic_resume_allowed=False,
            )
            try:
                self.broker.binding_events._publish("binding.native_event_rejected", payload)
            except (OSError, ValueError):
                logging.getLogger(__name__).exception(
                    "Native binding evidence persistence failed"
                )
            record = self.broker._sessions.get(identity.get("session_id"))
            if record:
                record.holds["native_event_" + message.get("request_id", "unknown")] = str(
                    error
                )
            logging.getLogger(__name__).warning("Native binding event rejected: %s", error)
            return None
