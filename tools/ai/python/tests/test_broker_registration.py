"""Complete relay registrations must authenticate before changing live ownership."""

import socket

import pytest

from sico.core.contracts import BoundContext
from sico.demo import DemoPeer
from sico.transport.broker import ContextBroker
from sico.transport.framing import VERSION, Connection


@pytest.mark.parametrize("replacement", [False, True])
def test_complete_registration_with_wrong_token_never_publishes_peer(replacement):
    context = BoundContext("authenticated", "generation", "bound", {"valid": True, "source": "simulated"})
    with ContextBroker(1) as broker:
        live = DemoPeer(broker, context) if replacement else None
        before = dict(broker.peers)
        bad = Connection(socket.create_connection(broker.address, timeout=1))
        try:
            bad.send({"protocol": VERSION, "kind": "hello", "token": "incorrect",
                      "instance_id": context.instance_id, "generation": context.generation,
                      "contexts": [context.record()]})
            with pytest.raises((EOFError, OSError)):
                bad.receive()
            assert broker.peers == before
            if live:
                assert broker.get_context(context)["valid"]
        finally:
            bad.close()
            if live:
                live.close()
