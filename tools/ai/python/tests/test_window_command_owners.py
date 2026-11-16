"""Real Qt wiring and retirement boundaries for EQ-08 C4."""

from concurrent.futures import Future

import pytest
from PyQt5.QtCore import QUrl
from PyQt5.QtWidgets import QApplication

from agent_gui_wait import wait_until
from agent_worker_fixtures import window as live_window
from sico.service.frontend_session import FrontendSession
from sico_ui.detail_navigation import DetailNavigation
from sico_ui.router_actions import RouterActions
from sico_ui.session_binding import SessionBinding
from sico_ui.window import AssistantWindow
from sico_ui.window_actions import WindowActions
from sico_ui.window_presentation import WindowPresentation
from test_agent_frontend_fake import MinimalPort


class Port(MinimalPort):
    def released_targets(self, token):
        return ()


SLOTS = {'router_actions': ['router_receipt_scope',
                    'refresh_router_receipt',
                    'query_router_receipt',
                    'router_cancel_scope',
                    'refresh_task_panel',
                    'cancel_router_request'],
 'actions': ['reconnect_instance',
             'update_host_notices',
             'run_command',
             'rename_session',
             'delete_session',
             'initialize_context',
             'queue_input',
             'submit',
             '_attachment_pasted',
             'show_pending_audit',
             'open_audit_ids',
             'target_family',
             'target_picker_provider',
             'pick_design_target',
             'resolve_target_choice',
             'answer_audit'],
 'renderer': ['receive',
              'update_session',
              '_update_token_usage',
              '_fit_token_usage',
              'refresh_status',
              'refresh_busy_cursor',
              'cancel_task',
              'resume_queue',
              'abandon_interrupted',
              '_resume_after_reconcile',
              'queue_hold_reason',
              '_tick_activity',
              'sync_activity_animation',
              'flush',
              'replay_caught_up'],
 'details': ['open_object',
             'open_object_link',
             'toggle_tool_details',
             'current_transcript',
             'open_tool_reference',
             'return_from_detail',
             'update_latest',
             'scroll_to_bottom',
             'show_latest'],
 'page': ['activate_session', 'preview_session', 'return_from_preview']}


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app, "quit", lambda: None)
    api = Port(tmp_path)
    view = AssistantWindow(FrontendSession(api, api.session("first")), persistent=True)
    try:
        wait_until(lambda: not view.page.opening)
        yield view
    finally:
        view.lifecycle.request()
        view.close()
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("component", SLOTS)
def test_window_has_no_command_aliases(window, component):
    assert all(not hasattr(window, name) for name in SLOTS[component])
    owner = getattr(window, component)
    assert all(callable(getattr(owner, name)) for name in SLOTS[component]
               if name != "show_latest")
    if component != "page":
        assert owner.page is window.page
        assert owner.presentation is window.presentation
    if component in {"actions", "router_actions"}:
        assert owner.lifecycle is window.lifecycle


@pytest.mark.parametrize("owner,name", [
    (DetailNavigation, "show_latest"),
    (SessionBinding, "begin_close"),
    (SessionBinding, "close_grace_expired"),
])
def test_obsolete_adapter_commands_are_absent(owner, name):
    assert not hasattr(owner, name)


# Patch owners before construction: connected bound methods must refer to them,
# while poison methods on the window expose any surviving alias dispatch.
@pytest.mark.parametrize("owner,name,signal,args", [
    (WindowActions, "submit", "send.clicked", (True,)),
    (WindowActions, "submit", "input.submitted", ()),
    (WindowActions, "_attachment_pasted", "input.attachmentPasted", ()),
    (WindowActions, "answer_audit", "centers.answerRequested", ("audit", {"answer": "ok"})),
    (WindowPresentation, "cancel_task", "stop.clicked", (False,)),
    (WindowPresentation, "resume_queue", "resume.clicked", (False,)),
    (WindowPresentation, "abandon_interrupted", "abandon.clicked", (False,)),
    (WindowPresentation, "_tick_activity", "activity_timer.timeout", ()),
    (DetailNavigation, "scroll_to_bottom", "latest.clicked", (False,)),
    (DetailNavigation, "open_object", "centers.objectRequested", ("data", "key")),
    (DetailNavigation, "return_from_detail", "detail.returnRequested", ()),
    (DetailNavigation, "open_object_link", "display.anchorClicked", (QUrl("data:key"),)),
    (DetailNavigation, "open_tool_reference", "display.linkDoubleClicked", ("tools:key",)),
    (RouterActions, "cancel_router_request", "task_panel.cancelRequested", ("request", ("scope",))),
])
def test_signals_invoke_component_after_construction(tmp_path, monkeypatch, owner, name,
                                                    signal, args):
    calls, aliases = [], []
    monkeypatch.setattr(owner, name, lambda self, *values: calls.append((self, values)))
    monkeypatch.setattr(AssistantWindow, name, lambda *values: aliases.append(values), raising=False)
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app, "quit", lambda: None)
    api = Port(tmp_path)
    view = AssistantWindow(FrontendSession(api, api.session("first")))
    try:
        wait_until(lambda: not view.page.opening)
        calls.clear()
        target, attribute = signal.split(".")
        getattr(getattr(view, target), attribute).emit(*args)
        component = {WindowActions: view.actions, WindowPresentation: view.renderer,
                     DetailNavigation: view.details, RouterActions: view.router_actions}[owner]
        assert calls == [(component, args)]
        assert not aliases
    finally:
        view.lifecycle.request()
        view.close()
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("attribute,component,name", [
    ("resume_action", "renderer", "resume_queue"),
    ("receipt_action", "router_actions", "query_router_receipt"),
    ("reconnect_action", "actions", "reconnect_instance"),
])
def test_menu_callbacks_resolve_owners_created_after_menu(window, monkeypatch, attribute,
                                                       component, name):
    calls, aliases = [], []
    monkeypatch.setattr(getattr(window, component), name, lambda: calls.append(name))
    monkeypatch.setattr(window, name, lambda: aliases.append(name), raising=False)
    getattr(window, attribute).setEnabled(True)
    getattr(window, attribute).trigger()
    assert calls == [name] and not aliases


def test_late_command_from_signal_cannot_replace_new_page(live_window, monkeypatch):
    window = live_window
    pending = Future()
    monkeypatch.setattr(window.api, "command", lambda *args, **kwargs: pending)
    window.input.setPlainText("old draft")
    window.input.submitted.emit()
    assert window.submissions
    window.page.activate_session("history")
    wait_until(lambda: not window.page.opening)
    window.input.setPlainText("new draft")
    pending.set_result(True)
    window.commands.poll()
    assert not window.submissions
    assert window.page.session.session_id == "history"
    assert window.input.toPlainText() == "new draft"
