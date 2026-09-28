"""Authentication and generation replacement within the broker registration lock."""

import hmac

from .framing import VERSION, ProtocolError
from .relay import emit_diagnostic


def authenticate(connection, expected_token, diagnostic, operation_timeout, expected_identity, peer_factory):
    hello = connection.receive()
    token = hello.pop("token", "")
    if (
        hello.get("protocol") != VERSION
        or hello.get("kind") != "hello"
        or not isinstance(token, str)
        or not hmac.compare_digest(token, expected_token)
    ):
        raise ProtocolError("Authentication failed")
    # Validate all target identities before mutating the live
    # generation registry.  A malformed reconnect must not evict a
    # healthy router that is already serving this instance.
    peer = peer_factory(connection, hello, diagnostic, router=None,
                operation_timeout=operation_timeout + 5,
                preserve_router=expected_identity is not None)
    return peer


def publish_peer(peer, old, router, created, *, peers, retired_peers, registered_generations, diagnostic, bridge_id):
    if old:
        if old.generation == peer.generation:
            old.demote()
            retired_peers.add(old)
        else:
            old.close(close_router=False)
            for retired in tuple(retired_peers):
                if retired.instance_id == peer.instance_id:
                    retired.close(close_router=False)
                    retired_peers.discard(retired)
    peers[peer.instance_id] = peer
    previous_generation = registered_generations.get(peer.instance_id)
    registered_generations[peer.instance_id] = peer.generation
    emit_diagnostic(
        diagnostic,
        ("bridge.instance_transport_updated"
         if previous_generation == peer.generation
         else "bridge.instance_registered"),
        bridge_id=bridge_id,
        instance_id=peer.instance_id,
        generation=peer.generation,
        router_id=router.router_id,
        router_created=created,
    )
