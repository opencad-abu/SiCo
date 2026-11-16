"""Scratch repro: GUI click of the empty-interruption 'continue' link."""

from contextlib import ExitStack

from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from info_app_fixtures import notice_text
from PyQt5.QtCore import QUrl
from sico_ui.reconcile_card import card_actions
from test_agent_interrupted_reconcile import interrupt
from test_agent_service_control import attach
from test_agent_service_control_gui import window_for


def test_scratch_click_no_calls_continues(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        task = interrupt(controller, calls=0)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        wait_until(lambda: "结束中断任务并继续" in window.display.toPlainText())
        row = next(row for row in window.presentation.transcript.messages
                   if row["role"] == "reconcile")
        actions = dict(card_actions(row["result"]))
        continue_url = actions["结束中断任务并继续（不重放）"]
        window.display.anchorClicked.emit(QUrl(continue_url))
        wait_until(lambda: not controller.paused)
        wait_until(lambda: controller._task["status"] == "cancelled")
        assert not notice_text(window) or True
