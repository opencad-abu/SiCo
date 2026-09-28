"""A real loopback relay with explicitly simulated Virtuoso data, for offline development."""

from __future__ import annotations

import threading
import uuid

from .core.contracts import BoundContext
from .transport.framing import VERSION, Connection


class DemoPeer:
    def __init__(self, broker, context=None, *, cancelled=None):
        import socket

        if context and context.snapshot.get("source") != "simulated":
            raise ValueError("Cannot resume a real target as a simulation")
        self.context = context or BoundContext(
            "demo-instance",
            uuid.uuid4().hex,
            "bound",
            {
                "source": "simulated",
                "valid": True,
                "version": "simulated IC231 context",
                "cellview": {"lib": "demo", "cell": "amp", "view": "schematic"},
            },
        )
        self.connection = Connection(socket.create_connection(broker.address, timeout=10))
        try:
            self.connection.send(
                {
                    "protocol": VERSION,
                    "kind": "hello",
                    "token": broker.token,
                    "instance_id": self.context.instance_id,
                    "generation": self.context.generation,
                    "contexts": [self.context.record()],
                }
            )
            self.connection.receive(cancelled=cancelled)
        except BaseException:
            self.connection.close()
            raise
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            while True:
                request = self.connection.receive()
                self.connection.send(
                    {
                        "protocol": VERSION,
                        "kind": "response",
                        "generation": self.context.generation,
                        "id": request["id"],
                        "ok": True,
                        "result": self.context.snapshot,
                    }
                )
        except (EOFError, OSError):
            return

    def close(self):
        self.connection.close()
        self.thread.join(timeout=2)
