"""Run the real window using only presentation values and a minimal fake port."""

from concurrent.futures import Future
from dataclasses import replace

from agent_boundary_guard import product_errors, window_errors
from agent_gui_wait import wait_until
from PyQt5.QtWidgets import QApplication
from sico.core.contracts import BoundContext
from sico.core.display_batch import DisplayBatch
from sico.service.frontend_session import FrontendSession, SessionToken, SessionView
from sico.service.preview_catalog import PreviewContent, PreviewIndex
from sico.service.replay_view import CursorPosition, ReplayView
from sico.service.workbench_publication import WorkbenchQueryPublication
from sico_ui.window import AssistantWindow


def completed(value):
    receipt = Future()
    receipt.set_result(value)
    return receipt


class Feed:
    def __init__(self, token, activation):
        self.identity = (token.session_id, token.runtime_id, "instance", "generation")
        self.activation = activation
        self.cursor = CursorPosition(0)
        self.closed = False

    def close(self):
        self.closed = True

    def validate_current(self):
        if self.closed:
            raise ValueError("Closed")

    def received(self, _future):
        self.validate_current()


class Source:
    live_processes = False

    def __init__(self, token, scope):
        self.session_id = token.session_id
        self.identity = (token.session_id, token.runtime_id, "instance", "generation")
        self.scope = scope

    def close(self):
        pass

    def empty_query(self):
        return WorkbenchQueryPublication(self, identity=self.identity)

    def query_pages(self, request):
        return completed((0, replace(self.empty_query(), query=request)))

    def processes(self, *_args):
        return completed({"rows": (), "total": 0, "running": 0, "offset": 0})

    def preview_artifact(self, name=""):
        if name:
            return completed(PreviewContent(
                notice="该预览产物暂不可读或校验失败；文件保留在预览目录。"))
        return completed(PreviewIndex(()))


class MinimalPort:
    initial_id = "first"
    desktop_busy = False

    def __init__(self, root):
        self.layout_path = root / "workspace.ini"
        self.launch_directory = root
        self.tokens = {key: SessionToken(key, key + "-runtime") for key in ("first", "second")}
        self.commands = []
        self.closed = False

    def handle(self, token):
        assert type(token) is SessionToken and self.owns(token)
        return token

    def owns(self, token):
        return token == self.tokens.get(token.session_id)

    def session(self, session_id):
        return self.tokens.get(session_id)

    def view(self, token):
        return SessionView(token.session_id, token.runtime_id,
                           BoundContext("instance", "generation", "target", {}),
                           "Test provider", "", "test-model", False)

    def connection(self, _token):
        return "instance", "generation"

    def opened_sessions(self):
        return tuple(self.tokens.values())

    def is_closing(self, _token):
        return self.closed

    def activity(self, _token):
        return "idle"

    def catalog_snapshot(self, _version=None):
        return {"version": 1, "error": "", "rows": (), "retained": {}, "availability": {}}

    def source(self, token, events=None, scope=None):
        return Source(token, scope)

    def prepare_replay(self, token, activation, _messages):
        return completed(ReplayView(Feed(token, activation), 0))

    def replay_matches(self, prepared, token, activation):
        return (prepared.stream.identity[:2] == (token.session_id, token.runtime_id)
                and prepared.stream.activation == activation)

    def event_batch(self, feed):
        return completed(DisplayBatch(feed.identity, feed.activation, 1, 0, 0, ()))

    def command(self, token, name, *args, **kwargs):
        self.commands.append((token, name, args, kwargs))
        return completed(True)

    def close_desktop(self):
        self.closed = True

    def wait_desktop(self, timeout=0):
        assert timeout == 0
        return self.closed


def test_window_runs_switches_and_closes_with_only_frontend_contract(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(application, "quit", lambda: None)
    port = MinimalPort(tmp_path)
    frontend = FrontendSession(port, port.session("first"))
    assert not product_errors(frontend)
    window = AssistantWindow(frontend)
    window.show()
    try:
        wait_until(lambda: not window.page.opening and window.binding.cursor is not None)
        first_feed = window.binding.delivery.stream
        window.page.activate_session("second")
        wait_until(lambda: not window.page.opening and window.page.session.session_id == "second")
        assert first_feed.closed and not window.binding.failed
        results = []
        window.actions.run_command("rename", "new label", success=results.append)
        wait_until(lambda: bool(results))
        assert port.commands[-1][:3] == (port.session("second"), "rename", ("new label",))
        # A record's preview path reaches the window slot; a non-widget parent would
        # abort the process from inside Qt's own slot, so the dialog must take the window.
        window.details.open_tool_reference(
            "/workarea/projs/proj4/xh/.sico/ai/circuit_previews/tb_inv_p3_g7.svg")
        dialogs = [widget for widget in window.children()
                   if widget.objectName() == "previewCatalog"]
        assert dialogs and dialogs[0].parent() is window
        dialogs[0].close()
        application.processEvents()
        assert not window_errors(window)
        window.request_quit()
        wait_until(lambda: port.closed and not window.isVisible())
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()
