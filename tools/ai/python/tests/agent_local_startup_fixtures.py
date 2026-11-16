"""Tracked local bootstrap resources and a connect relay for startup boundary tests."""

import socket
import threading
from types import SimpleNamespace

import agent_local_harness as local_module
import agent_startup_harness as startup_module
import pytest
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.transport.framing import VERSION, Connection
from sico_ui.startup import StartupView


@pytest.fixture
def local_setup(tmp_path, monkeypatch):
    owner, calls, brokers, journals, loops, peers, views = (
        threading.get_ident(), [], [], [], [], [], [],
    )
    broker_type, journal_type = local_module.ContextBroker, startup_module.SessionJournal
    create_backend, peer_type = startup_module.create_backend, local_module.DemoPeer

    class Broker(broker_type):
        def __init__(self, *args, **kwargs):
            assert threading.get_ident() != owner
            super().__init__(*args, **kwargs)
            brokers.append(self)

        def close(self):
            assert threading.get_ident() != owner
            calls.append(("broker", self))
            super().close()

    class Journal(journal_type):
        def __init__(self, *args, **kwargs):
            assert threading.get_ident() != owner
            super().__init__(*args, **kwargs)
            journals.append(self)

        def close(self):
            assert threading.get_ident() != owner
            calls.append(("journal", self))
            super().close()

    class Peer(peer_type):
        def close(self):
            assert threading.get_ident() != owner
            calls.append(("peer", self))
            super().close()

    def backend(*args, **kwargs):
        assert threading.get_ident() != owner
        loop = create_backend(*args, **kwargs)
        original = getattr(loop, "close", lambda: None)

        def close():
            assert threading.get_ident() != owner
            assert loop.journal._fd >= 0
            calls.append(("backend", loop))
            original()

        loop.close = close
        loops.append(loop)
        return loop

    monkeypatch.setattr(local_module, "ContextBroker", Broker)
    monkeypatch.setattr(local_module, "DemoPeer", Peer)
    monkeypatch.setattr(startup_module, "SessionJournal", Journal)
    monkeypatch.setattr(startup_module, "create_backend", backend)

    def connect(broker, instance, generation, *, partial=b"", wrong=False):
        peer = Connection(socket.create_connection(broker.address, timeout=2))
        peers.append(peer)
        if partial:
            peer.socket.sendall(partial)
            return
        bound = BoundContext(instance, "wrong" if wrong else generation, "bound", {
            "valid": True, "cwd": str(tmp_path), "source": "test relay",
        })
        peer.send({"protocol": VERSION, "kind": "hello", "token": broker.token,
                   "instance_id": bound.instance_id, "generation": bound.generation,
                   "contexts": [bound.record()]})
        reply = peer.receive()
        if wrong:
            return reply

        def serve():
            try:
                while True:
                    request = peer.receive()
                    peer.send({"protocol": VERSION, "kind": "response", "id": request["id"],
                               "generation": generation, "ok": True, "result": bound.snapshot})
            except (EOFError, OSError):
                pass

        threading.Thread(target=serve, daemon=True).start()
        return bound

    def create(command="demo", *, attach=True, timeout=3, message="draft", abandon=False,
               provider_config=None, environment=None):
        args = SimpleNamespace(command=command, launch_dir=tmp_path, session="local",
                               provider_config=provider_config, timeout=timeout, message=message,
                               abandon_interrupted=abandon)
        if command == "connect" and attach:
            instructions = local_module.connection_instructions

            def register(journal, broker, instance, generation, **kwargs):
                text = instructions(journal, broker, instance, generation, **kwargs)
                connect(broker, instance, generation)
                return text

            monkeypatch.setattr(local_module, "connection_instructions", register)
        startup = local_module.LocalStartup(args, lambda *_a, **_k: None,
                                            environment=environment or {})
        view = StartupView(startup, None, None, quit_application=lambda: None)
        views.append((startup, view))
        return startup, view

    yield SimpleNamespace(create=create, owner=owner, calls=calls, brokers=brokers,
                          journals=journals, loops=loops, connect=connect, root=tmp_path)
    for startup, view in views:
        view.close()
        assert startup.wait(5)
        if view.window is not None:
            view.window.lifecycle.persistent = False
            view.window.close()
            view.window.deleteLater()
        view.progress.deleteLater()
        view.deleteLater()
    for peer in peers:
        peer.close()
    QApplication.instance().processEvents()
