"""Stable transport outcome categories for project service requests."""

from ..transport.framing import ProtocolError


def is_definite_refusal(error):
    """A damaged response cannot prove refusal, despite ValueError inheritance."""
    return isinstance(error, ValueError) and not isinstance(error, ProtocolError)


def is_unknown_outcome(error):
    """Frontend callers classify outcomes without importing transport details."""
    return isinstance(error, (ProtocolError, ResultUnknown))


class ServiceRequestError(RuntimeError):
    """Base class for an operation whose local outcome is classified."""


class RequestUnsent(ServiceRequestError, TimeoutError):
    """The request wrote no bytes, including failure before connection readiness."""


class ResultUnknown(ServiceRequestError):
    """The request was sent, but the service outcome was not observed."""

    def __init__(self, operation_id, method, message="Service operation result is unknown"):
        self.operation_id = operation_id
        self.method = method
        super().__init__(f"{message}: {method}/{operation_id}")


class StopResultUnknown(ResultUnknown):
    """Compatibility name for an unknown lifecycle stop outcome."""


class ServiceCapacityError(ServiceRequestError):
    """A bounded client or listener queue cannot accept more work."""
