"""A controlled broker using real binding transitions, with no external Virtuoso."""

import threading

from sico.core.contracts import BoundContext
from sico.transport.binding_events import BindingEvents
from sico.transport.bindings import BindingRegistry


class ResourceBroker:
    def __init__(self, controller):
        self.registry = BindingRegistry()
        self.events = BindingEvents(self.registry, lambda context: True, lambda *a, **k: False)
        self.session_id = controller.session_id
        self.register_session(self.session_id, controller.current)
        resource = BoundContext(
            controller.current.instance_id, controller.current.generation, "output",
            {"cellview": {"lib": "Work", "cell": "Amp", "view_name": "schematic"}},
        )
        self.events.reserve(self.session_id, resource, "resource")
        controller.bindings.attach_targets(self)
        self.calls = []

    def register_session(self, session_id, context):
        self.registry.add(session_id, context)

    def binding_snapshot(self, session_id, after=0):
        return self.events.snapshot(session_id, after)

    def release_binding_resource(self, session_id, reservation_id):
        self.calls.append((session_id, reservation_id))
        return self.events.change(session_id, "release_resource", reservation_id=reservation_id)

    def select_session_target(self, session_id, context, **kwargs):
        return self.events.change(session_id, "select", context=context, **kwargs)

    def release_session(self, session_id):
        self.registry.remove(session_id)


class ReleaseGate:
    """The watchdog fails a stalled call; passing tests must return before release."""

    def __init__(self, operation):
        self.operation = operation
        self.entered, self.release, self.returned = (
            threading.Event(), threading.Event(), threading.Event()
        )
        self.owner = threading.get_ident()
        self.expired = False

    def __call__(self, *args, **kwargs):
        assert threading.get_ident() != self.owner
        self.entered.set()
        try:
            if not self.release.wait(5):
                self.expired = True
                raise AssertionError("RPC gate expired before UI returned")
            return self.operation(*args, **kwargs)
        finally:
            self.returned.set()
