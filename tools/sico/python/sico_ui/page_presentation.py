"""Render navigation transitions using selected values and explicit widget ports."""

import time

from sico import PRODUCT_NAME
from PyQt5.QtCore import QTimer

from .event_batches import BatchDelivery
from .presentation import target_label
from .session_activation import PREVIEW_HINT


class PagePresentation:
    def __init__(self, widgets, *, api, page, presentation, drafts, receipts,
                 centers, detail, navigation, bindings, actions, router, renderer,
                 replace_binding, stream_ready, model_badge):
        self.ui = widgets
        self.api = api
        self.page = page
        self.presentation = presentation
        self.drafts = drafts
        self.receipts = receipts
        self.centers = centers
        self.detail = detail
        self.navigation = navigation
        self.bindings = bindings
        self.actions = actions
        self.router = router
        self.renderer = renderer
        self.replace_binding = replace_binding
        self.stream_ready = stream_ready
        self.model_badge = model_badge
        self.delivery = None
        page.invalidated.connect(self.invalidate)
        page.began.connect(self.begin)
        page.activated.connect(self.activate)
        page.previewReady.connect(self.preview)
        page.returned.connect(self.returned)
        page.failed.connect(self.failed)

    def invalidate(self):
        self.actions.invalidate()
        self.centers._invalidate()
        self.detail.invalidate()
        self.presentation.cancel_render()
        if self.delivery is not None:
            self.delivery.close()
            self.delivery = None

    def begin(self):
        self.centers.suspend()
        self.receipts.clear_error()
        self.ui.status_bar.clearMessage()
        for key in ("fault", "control", "connection"):
            self.ui.session_notices.clear(key)
        self.renderer.refresh_busy_cursor()
        self.ui.input.setEnabled(False)
        for control in (self.ui.send, self.ui.stop, self.ui.resume, self.ui.abandon,
                        self.ui.resume_action):
            control.setEnabled(False)
        self.ui.return_action.setEnabled(True)
        self.router.refresh_task_panel()
        self.ui.status.setText("正在打开会话…")
        self.ui.status.setToolTip("")

    def failed(self, message):
        self.renderer.refresh_busy_cursor()
        self.centers.resume(self.page.activation)
        self.detail.bind(self.centers.index)
        self.ui.input.setEnabled(self.page.reviewing is None and self.stream_ready()
                                 and not self.api.is_closing(self.page.session))
        if self.presentation.state:
            self.renderer.update_session(self.presentation.state)
        connection = getattr(self.ui.parent, "service_connection", None)
        if connection is None or not connection.replay_failed(message):
            self.receipts.report(message)

    def activate(self, previous, prepared):
        self.ui.status_bar.clearMessage()
        self.ui.task_panel.update_requests([])
        self.drafts.save(previous, self.ui.input.draft_revision, self.ui.input.draft())
        poll = self.replace_binding(prepared)
        self.bindings.update_state(None)
        self.presentation.reset()
        self.renderer.refresh_busy_cursor()
        self.router.refresh_router_receipt()
        self.centers.live_session_id = self.page.session.session_id
        self.centers.defer_snapshot()
        self.centers.reset(self.api.source(self.page.session, scope=prepared.stream),
                           activation=self.page.activation)
        self.ui.parent.chat_scroll.render(force=True)
        self.ui.input.restore_draft(self.drafts.load(self.page.session))
        self.ui.input.setEnabled(self.page.session.runtime_id is not None)
        if self.page.session.runtime_id is None:
            self.ui.status.setText("历史会话（只读）")
        self.ui.return_action.setEnabled(False)
        self.ui.parent.setWindowTitle(PRODUCT_NAME + " / " + self.page.view.label)
        self.model_badge()
        poll()
        self.navigation.catalog_state = None
        self.navigation.refresh()
        self.ui.tabs.setCurrentWidget(self.ui.conversation)
        self.ui.input.setFocus()

    def preview(self, prepared, scope):
        self.router.refresh_task_panel()
        self.ui.status.setText("正在读取历史会话…")
        self.ui.tabs.setCurrentWidget(self.ui.conversation)
        replay = self.presentation.preview()
        self.centers.defer_snapshot()
        self.centers.reset(self.api.preview_source(prepared),
                           activation=self.page.activation)
        self.ui.display.clear()
        delivery = BatchDelivery(self.api, prepared.stream)
        self.delivery = delivery

        def replay_batch():
            if not self.page.current(scope):
                return
            try:
                if not self.api.preview_matches(prepared, self.page.reviewing, scope[0]):
                    raise ValueError("历史会话运行实例已变化")
                frame = time.monotonic() + 0.012
                while True:
                    complete = delivery.apply(replay.receive, lambda *_args: None)
                    if complete and delivery.caught_up:
                        break
                    if delivery.future is not None and not delivery.future.done():
                        self.ui.parent.chat_scroll.render()
                        QTimer.singleShot(2, replay_batch)
                        return
                    if time.monotonic() >= frame:
                        self.ui.parent.chat_scroll.render()
                        QTimer.singleShot(0, replay_batch)
                        return
            except (ValueError, RuntimeError, KeyError, TypeError) as exc:
                self.page.return_from_preview()
                self.ui.notices.error("历史会话暂不可读", str(exc))
                return
            self.page.finish(scope)
            self.renderer.refresh_busy_cursor()
            self.centers.release_snapshot()
            self.centers.flush()
            self.ui.parent.chat_scroll.render(force=True)
            self.ui.target.setText("历史会话（只读） · "
                                   + target_label(prepared.context.get("snapshot", {})))
            self.ui.status.setText(PREVIEW_HINT)

        replay_batch()

    def returned(self):
        if not self.stream_ready():
            self.page.activate()
            return
        self.renderer.refresh_busy_cursor()
        self.receipts.clear_error()
        self.centers.resume(self.page.activation)
        self.detail.bind(self.centers.index)
        self.ui.input.setEnabled(not self.api.is_closing(self.page.session))
        self.ui.return_action.setEnabled(False)
        if self.presentation.state:
            self.renderer.update_session(self.presentation.state)
        self.router.refresh_router_receipt()
