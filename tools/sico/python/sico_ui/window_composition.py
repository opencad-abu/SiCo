"""Construct the workspace's explicit presentation and command collaborators."""

from .chat_scroll import ChatScroll
from .close_choice import CloseChoice
from .commands import CommandReceipts
from .conversation_widgets import ConversationWidgets
from .detail_navigation import DetailNavigation
from .page_presentation import PagePresentation
from .pending_commands import PendingCommands
from .receipt_scope import ReceiptScope
from .reconcile_actions import ReconcileActions
from .router_actions import RouterActions
from .session_binding import SessionBinding
from .steering import SteeringInput
from .window_actions import WindowActions
from .window_presentation import WindowPresentation


def compose(window):
    ui = ConversationWidgets(
        parent=window, notices=window.info_app, session_notices=window.session_notices,
        input=window.input, display=window.display, target=window.target,
        token_usage=window.token_usage, status=window.status, status_bar=window.statusBar(),
        send=window.send, stop=window.stop, resume=window.resume, abandon=window.abandon,
        latest=window.latest,
        resume_action=window.resume_action, return_action=window.return_action,
        host_notice_status=window.host_notice_status, tabs=window.tabs,
        conversation=window.pages["conversation"], task_panel=window.task_panel,
    )
    page, presentation = window.page, window.presentation
    lifecycle, api = window.lifecycle, window.api
    window.commands = CommandReceipts(
        window, capture=lambda: ReceiptScope.capture(page, presentation.displayed_context),
        current=lambda scope: scope.current(page, presentation.displayed_context, api,
                                             lifecycle.closing),
        closing=lambda: lifecycle.closing,
        refresh=lambda: (window.steering_input.refresh(),
                         window.router_actions.refresh_task_panel()),
        show_error=lambda text: window.info_app.error("操作未完成", text),
    )
    window.submissions = PendingCommands()
    window.actions = WindowActions(
        ui, api=api, page=page, presentation=presentation, lifecycle=lifecycle,
        receipts=window.commands, drafts=window.drafts, submissions=window.submissions,
        centers=window.centers,
        poll=lambda: window.binding.poll(), stream_failed=lambda: window.binding.failed,
        steering=lambda: window.steering_input, update=window.update_session,
        show_panel=window.show_panel,
    )
    window.router_actions = RouterActions(
        ui, page=page, presentation=presentation, lifecycle=lifecycle,
        stream_failed=lambda: window.binding.failed, run_command=window.actions.run_command,
        receipt_action=getattr(window, "receipt_action", None),
    )
    window.steering_input = SteeringInput(
        ui, window.input_buttons, page=page, presentation=presentation, lifecycle=lifecycle,
        receipts=window.commands, submissions=window.submissions, drafts=window.drafts,
        queue_input=window.actions.queue_input, stream_failed=lambda: window.binding.failed,
        open_audits=window.actions.open_audit_ids, run_command=window.actions.run_command,
        update=window.update_session, poll=lambda: window.binding.poll(),
    )
    window.renderer = WindowPresentation(
        ui, page=page, presentation=presentation, receipts=window.commands,
        centers=window.centers, navigation=window.navigation, bindings=window.binding_panel,
        steering=window.steering_input, activity_timer=window.activity_timer,
        refresh_receipt=window.router_actions.refresh_router_receipt,
        refresh_tasks=window.router_actions.refresh_task_panel,
        run_command=window.actions.run_command, poll=lambda: window.binding.poll(),
    )
    from .event_detail import show_event_detail
    from .turn_input_detail import show_input_detail

    window.reconcile_actions = ReconcileActions(
        ui, page=page, presentation=presentation, command=window.actions.run_command,
        poll=lambda: window.binding.poll(),
    )
    window.details = DetailNavigation(
        ui, detail=window.detail, centers=window.centers, page=page, presentation=presentation,
        show_panel=window.show_panel, input_detail=lambda value: show_input_detail(window, value),
        event_detail=lambda value: show_event_detail(window, value),
        reconcile=window.reconcile_actions.open,
    )
    window.page_presentation = PagePresentation(
        ui, api=api, page=page, presentation=presentation, drafts=window.drafts,
        receipts=window.commands, centers=window.centers, detail=window.detail,
        navigation=window.navigation, bindings=window.binding_panel, actions=window.actions,
        router=window.router_actions, renderer=window.renderer,
        replace_binding=window.replace_binding,
        stream_ready=lambda: window.binding.cursor is not None and not window.binding.failed,
        model_badge=window.refresh_model_badge,
    )
    window.binding = bind_events(window)
    window.chat_scroll = ChatScroll(window)
    window.close_choice = CloseChoice(window)
    page.initializeRequested.connect(window.initialize_context)
    window.destroyed.connect(lambda *_args: page.dispose())
    window.renderer._update_token_usage({})
    window.update_latest()
    page.activate()


def bind_events(window, prepared=None):
    renderer = window.renderer
    def failed():
        window.router_actions.refresh_router_receipt()
        connection = getattr(window, "service_connection", None)
        if window.close_choice.force.pending:
            return
        ending = connection.ending.operations.get(window.page.session) if connection else None
        if (window.api.is_closing(window.page.session)
                or ending is not None and ending.state in {"closing", "ended", "needs_reconcile"}):
            return  # The captured end operation owns its result, including stream retirement.
        window.info_app.error("会话事件无法同步",
            "事件身份或顺序不匹配，已暂停当前任务；请切换会话或重新打开核对")
        for control in (window.send, window.stop, window.resume, window.resume_action):
            control.setEnabled(False)

    def released(target_id):
        if hasattr(window, "quick_input"):
            window.quick_input.notice("released", target_id=target_id)

    return SessionBinding(
        window, window.page.session, api=window.api, page=window.page,
        presentation=window.presentation, lifecycle=window.lifecycle,
        receive=renderer.receive, update=renderer.update_session,
        caught_up=renderer.replay_caught_up, refresh_status=renderer.refresh_status,
        refresh_cursor=renderer.refresh_busy_cursor, flush=renderer.flush,
        finish_close=window.finish_close, stream_failed=failed, released=released,
        prepared=prepared,
    )


def command_slot(component, name):
    """Qt command compatibility only; component state has no window aliases.

    Legacy slots retire per component once window signal wiring, navigation,
    binding/dialog actions and host notices connect to that component directly.
    The migration inventory is in tools/ai/docs/T07_STATE_OWNERSHIP.md.
    """
    def call(window, *args, **kwargs):
        return getattr(getattr(window, component), name)(*args, **kwargs)
    return call


def connect_commands(window_type):
    for component, names in (
        ("router_actions", ("router_receipt_scope", "refresh_router_receipt",
            "query_router_receipt",
            "router_cancel_scope", "refresh_task_panel", "cancel_router_request")),
        ("actions", ("reconnect_instance", "update_host_notices", "run_command", "rename_session",
            "delete_session", "initialize_context", "queue_input", "submit", "_attachment_pasted",
            "show_pending_audit", "open_audit_ids", "target_family", "target_picker_provider",
            "pick_design_target", "resolve_target_choice", "answer_audit")),
        ("renderer", ("receive", "update_session", "_update_token_usage", "_fit_token_usage",
            "refresh_status", "refresh_busy_cursor", "cancel_task", "resume_queue",
            "abandon_interrupted", "_resume_after_reconcile", "queue_hold_reason", "_tick_activity",
            "sync_activity_animation", "flush", "replay_caught_up")),
        ("details", ("open_object", "open_object_link", "toggle_tool_details", "current_transcript",
            "open_tool_reference", "return_from_detail", "update_latest", "scroll_to_bottom",
            "show_latest")),
        ("page", ("activate_session", "preview_session", "return_from_preview")),
    ):
        for name in names:
            setattr(window_type, name, command_slot(component, name))
    return window_type
