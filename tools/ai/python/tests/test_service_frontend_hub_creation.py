"""Frontend session creation distinguishes retryable and committed failures."""

from types import SimpleNamespace

import pytest

try:
    from sico.api.frontend_session import SessionToken
    from sico.api.service_session_dto import SessionAddress
except ImportError:  # Legacy development layout before the api split.
    from sico.service.frontend_session import SessionToken
    from sico.service.service_session_dto import SessionAddress
from sico.service.frontend_attach import create_from_source

DESCRIPTOR = SimpleNamespace(project_id="a" * 32, service_id="b" * 32)
ADDRESS = SessionAddress(
    DESCRIPTOR.project_id,
    DESCRIPTOR.service_id,
    SessionToken("source", "c" * 32),
)
WIRE = SimpleNamespace(connection=object(), control=object())


def record(value, _descriptor):
    return {
        "address": SessionAddress(
            DESCRIPTOR.project_id,
            DESCRIPTOR.service_id,
            SessionToken(value.session_id, value.runtime_id),
        ).record(),
    }


class Owner:
    def __init__(self, create):
        self.controllers = {}
        self.create = create
        self.calls = 0
        self.creation = SimpleNamespace(create=self._create)

    def create_from(self, _source_id, session_id, _context):
        self.calls += 1
        return self.create(self, session_id)

    def _create(self, _address, session_id, _context):
        self.calls += 1
        return self.create(self, session_id)


class Control:
    def run(self, _address, _connection, _proof, operation):
        return operation()

    def continue_session(self, _connection, address):
        return SimpleNamespace(record=lambda: {
            "address": address.record(), "action": "acquire",
        })


def controller(owner, session_id):
    value = SimpleNamespace(session_id=session_id, runtime_id="d" * 32)
    owner.controllers[session_id] = value
    return value


def test_prepublication_failure_retries_with_the_same_source():
    def create(owner, session_id):
        if owner.calls == 1:
            raise RuntimeError("bridge was temporarily unavailable")
        return controller(owner, session_id)

    owner = Owner(create)

    result = create_from_source(
        owner, DESCRIPTOR, Control(), WIRE, ADDRESS, object(), record=record
    )

    assert owner.calls == 2
    assert result["session"]["address"]["session_id"] in owner.controllers
    assert result["control"]["action"] == "acquire"


def test_committed_runtime_is_finalized_without_creating_a_duplicate():
    def create(owner, session_id):
        controller(owner, session_id)
        raise RuntimeError("control acquisition response was interrupted")

    owner = Owner(create)

    result = create_from_source(
        owner, DESCRIPTOR, Control(), WIRE, ADDRESS, object(), record=record
    )

    assert owner.calls == 1
    assert len(owner.controllers) == 1
    assert result["session"]["address"]["session_id"] in owner.controllers


def test_two_prepublication_failures_are_definite_rejections():
    def create(_owner, _session_id):
        raise RuntimeError("bridge is unavailable")

    owner = Owner(create)

    def record(*_args):
        pytest.fail("no session was published")

    with pytest.raises(ValueError, match="未创建运行实例"):
        create_from_source(
            owner, DESCRIPTOR, Control(), WIRE, ADDRESS, object(), record=record
        )
    assert owner.calls == 2
    assert not owner.controllers
