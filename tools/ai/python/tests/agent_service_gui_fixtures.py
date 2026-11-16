"""Service-backed Qt windows with real sockets and deterministic cleanup."""

from contextlib import contextmanager
from types import SimpleNamespace

from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from PyQt5.QtWidgets import QApplication

from sico.service.remote_frontend import RemoteFrontend
from sico_ui.window import AssistantWindow


class ServiceTestWindow(AssistantWindow):
    __test__ = False

    def finish_close(self):
        # The pytest session owns QApplication; this fixture owns only its window.
        self.display.set_busy(False)
        self.close()


def startup_args(project, mode="demo", session="live"):
    return SimpleNamespace(command=mode, session=session, launch_dir=project,
                           provider_config=None, timeout=3, context_file=None, message="")


@contextmanager
def remote_window(project, monkeypatch, *, provider=None):
    with business_service(project, monkeypatch, provider=provider) as service:
        address = open_session(service)
        api = RemoteFrontend(service.descriptor, project, address.session.session_id)
        window = None
        try:
            frontend = api.open_initial("attach", dict(session_id="live")).result(5)
            api.control.request(frontend.session, "acquire").result(5)
            window = ServiceTestWindow(frontend)
            wait_until(lambda: window.presentation.state is not None)
            yield service, window
        finally:
            if window:
                window.request_quit()
                window.deleteLater()
            api.close_desktop()
            wait_until(lambda: api.wait_desktop(0))
            QApplication.instance().processEvents()


def drain(api, stream):
    result = []
    while True:
        receipt = api.event_batch(stream)
        batch = receipt.result(5)
        stream.received(receipt)
        assert not batch.error
        result.append(batch)
        if batch.end == batch.committed:
            return result
