"""Readiness uses the same negotiated connection as service administration."""

from .service_channel import service_channel

BOOTSTRAP_PROTOCOL = "sico_service_bootstrap.v1"


def probe_ready(descriptor, *, deadline, cancelled=None):
    with service_channel(descriptor, deadline=deadline, cancelled=cancelled, purpose="attach"):
        return descriptor
