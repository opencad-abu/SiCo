"""Construct the workspace's explicit presentation and command collaborators."""

from .chat_scroll import ChatScroll
from .cad_objects import CADObjectActions
from .close_choice import CloseChoice
from .commands import CommandReceipts
from .conversation_widgets import ConversationWidgets
from .detail_navigation import DetailNavigation
from .page_presentation import PagePresentation
from .pending_commands import PendingCommands
from .object_mentions import ObjectMentionController
from .receipt_scope import ReceiptScope
from .reconcile_actions import ReconcileActions
from .reconcile_panel import ReconcilePanel
from .rewind_actions import RewindActions
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
        steering=lambda: window.steering_input,
        update=lambda state: window.renderer.update_session(state),
        show_panel=window.show_panel,
    )
    window.object_mentions = ObjectMentionController(window)
    window.cad_objects = CADObjectActions(window)
    window.detail.cad_objects = window.cad_objects
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
        update=lambda state: window.renderer.update_session(state),
        poll=lambda: window.binding.poll(),
    )
    window.rewind_actions = RewindActions(
        ui, api=api, page=page, presentation=presentation, lifecycle=lifecycle,
        run_command=window.actions.run_command,
    )
    window.renderer = WindowPresentation(
        ui, page=page, presentation=presentation, receipts=window.commands,
        centers=window.centers, navigation=window.navigation, bindings=window.binding_panel,
        steering=window.steering_input, activity_timer=window.activity_timer,
        refresh_receipt=window.router_actions.refresh_router_receipt,
        refresh_tasks=window.router_actions.refresh_task_panel,
        run_command=window.actions.run_command, poll=lambda: window.binding.poll(),
        rewind_actions=window.rewind_actions,
    )
    from .event_detail import show_event_detail
    from .turn_input_detail import show_input_detail

    window.reconcile_actions = ReconcileActions(
        ui, page=page, presentation=presentation, command=window.actions.run_command,
        poll=lambda: window.binding.poll(),
        token_for=lambda session_id: api.session(session_id),
        page_scope=window.actions.capture,
    )
    window.details = DetailNavigation(
        ui, detail=window.detail, centers=window.centers, page=page, presentation=presentation,
        show_panel=window.show_panel, input_detail=lambda value: show_input_detail(window, value),
        event_detail=lambda value: show_event_detail(window, value),
        reconcile=window.reconcile_actions.open, rewind=window.rewind_actions.open,
        cad_objects=window.cad_objects,
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
    window.reconcile_panel = ReconcilePanel(window)
    layout = window.display.parentWidget().layout()
    layout.insertWidget(layout.indexOf(window.display) + 1, window.reconcile_panel)
    from .recap import RecapPanel

    window.recap_panel = RecapPanel(window)
    layout.insertWidget(layout.indexOf(window.input), window.recap_panel)
    window.close_choice = CloseChoice(window)
    connect_signals(window)
    page.initializeRequested.connect(window.actions.initialize_context)
    window.destroyed.connect(lambda *_args: page.dispose())
    window.renderer._update_token_usage({})
    window.details.update_latest()
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
        # 服务窗口先就地恢复事件流：任务不暂停，窗口按捕获的起点重放。
        if connection is not None and connection.recover_stream():
            return
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


def connect_signals(window):
    """Wire widget signals only after their command owners have been constructed."""
    actions, renderer, details = window.actions, window.renderer, window.details
    window.activity_timer.timeout.connect(renderer._tick_activity)
    window.input.attachmentPasted.connect(actions._attachment_pasted)
    window.input.submitted.connect(actions.submit)
    window.send.clicked.connect(actions.submit)
    window.stop.clicked.connect(renderer.cancel_task)
    window.resume.clicked.connect(renderer.resume_queue)
    window.abandon.clicked.connect(renderer.abandon_interrupted)
    window.latest.clicked.connect(details.scroll_to_bottom)
    window.display.verticalScrollBar().valueChanged.connect(details.update_latest)
    window.display.verticalScrollBar().rangeChanged.connect(details.update_latest)
    window.detail.returnRequested.connect(details.return_from_detail)
    window.centers.objectRequested.connect(details.open_object)
    window.centers.answerRequested.connect(actions.answer_audit)
    window.display.anchorClicked.connect(details.open_object_link)
    window.display.linkDoubleClicked.connect(details.open_tool_reference)
    window.task_panel.cancelRequested.connect(window.router_actions.cancel_router_request)
    window.task_panel.nodeActivated.connect(details.open_task_node)
