"""Project stop is authoritative for every session in the workspace."""

from contextlib import ExitStack

from agent_gui_wait import wait_until
from agent_service_business_fixtures import open_session
from agent_service_gui_fixtures import remote_window
from sico_ui.service_connection import attach_service_connection
from test_agent_service_control import attach


def test_service_stop_stops_detached_and_unselected_sessions(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window), ExitStack() as stack:
        open_session(service, "old")
        old, token = attach(stack, service, tmp_path, "old")
        old.control.request(token, "acquire").result(5)
        old.close_desktop()
        wait_until(lambda: old.wait_desktop(0))
        attach_service_connection(window)
        window.close_choice.stop_everything()
        wait_until(lambda: window.lifecycle.closing, timeout=8)
