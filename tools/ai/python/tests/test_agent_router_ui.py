"""User-visible F2 receipt and explicit reconnect controls."""

import json
import time

from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtWidgets import QApplication, QPlainTextEdit
from test_agent_bridge import bound

from sico.adapters.virtuoso import context_tools
from sico.service.python_backend import PythonBackend
from sico.demo import DemoPeer
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico.transport.bridge_client import BridgeClient
from sico.transport.instance_bridge import InstanceBridge


def test_receipt_is_read_only_and_reconnect_requires_explicit_choice(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    with InstanceBridge(tmp_path, bound()) as bridge, SessionJournal(tmp_path, "desktop") as journal:
        peer = DemoPeer(bridge.broker, bound())
        with BridgeClient.discover(tmp_path, bound()) as client:
            client.register_session("desktop", bound())
            loop = PythonBackend(ContextDemoProvider(), context_tools(client, journal), journal, bound())
            notices = []
            window = AssistantWindow(loop, persistent=True,
                                      notice=lambda kind, **payload: notices.append((kind, payload)))
            window.controller.bindings.attach_targets(client)
            window.resize(920, 660)
            window.show()
            window.controller.initialize_context()
            try:
                deadline = time.monotonic() + 5
                while window.controller.busy:
                    assert time.monotonic() < deadline
                    app.processEvents()
                    time.sleep(.01)
                # The receipt scope needs the first session snapshot from the
                # binding; the action stays disabled until it arrives.
                while window.presentation.state is None:
                    assert time.monotonic() < deadline
                    app.processEvents()
                    time.sleep(.01)
                window.router_actions.query_router_receipt()
                deadline = time.monotonic() + 5
                while window.findChild(QPlainTextEdit, "routerReceipt") is None:
                    assert time.monotonic() < deadline
                    app.processEvents()
                    time.sleep(.01)
                editor = window.findChild(QPlainTextEdit, "routerReceipt")
                assert editor.isReadOnly()
                receipt = json.loads(editor.toPlainText())
                assert receipt["state"] == "completed"
                assert not receipt["automatic_resume_allowed"]
                editor.parent().grab().save(str(tmp_path / "router-receipt.png"))
                monkeypatch.setattr("sico_ui.window_actions.confirm",
                                    lambda *args, **kwargs: False)
                window.actions.reconnect_instance()
                assert not any(kind == "reconnect" for kind, _ in notices)
                monkeypatch.setattr("sico_ui.window_actions.confirm",
                                    lambda *args, **kwargs: True)
                window.actions.reconnect_instance()
                assert notices[-1] == ("reconnect", {"id": "bound"})
                window.grab().save(str(tmp_path / "router-window.png"))
            finally:
                window.request_quit()
                window.sessions.wait()
                window.close()
                peer.close()
