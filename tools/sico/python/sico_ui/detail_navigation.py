"""Conversation links and detail-page navigation with explicit view dependencies."""

from sico.core.links import parse_link


class DetailNavigation:
    def __init__(self, widgets, *, detail, centers, page, presentation, show_panel,
                 input_detail, event_detail, reconcile=lambda _value: None):
        self.ui = widgets
        self.detail = detail
        self.centers = centers
        self.page = page
        self.presentation = presentation
        self.show_panel = show_panel
        self.input_detail = input_detail
        self.event_detail = event_detail
        self.reconcile = reconcile
        self.origin = widgets.conversation

    def open_object(self, kind, key):
        if self.ui.tabs.currentWidget() is not self.detail:
            self.origin = self.ui.tabs.currentWidget()
            self.detail.history.clear()
            self.detail.current = None
        self.detail.open(kind, key)
        self.ui.tabs.setCurrentWidget(self.detail)

    def open_object_link(self, url):
        value = url.toString()
        if value.startswith("reconcile:"):
            self.reconcile(value)
            return
        if value.startswith("turn-input:"):
            self.input_detail(value[len("turn-input:"):])
            return
        if value.startswith("event-detail:"):
            self.event_detail(value[len("event-detail:"):])
            return
        if value.startswith("tools:"):
            self.toggle_tool_details(value[len("tools:"):])
            return
        try:
            kind, key = parse_link(value, self.centers.index.session_id)
        except ValueError:
            return
        if kind == "audit":
            self.centers.show_audit(key)
            self.show_panel("right")
            return
        if self.ui.tabs.currentWidget() is not self.detail:
            self.origin = self.ui.tabs.currentWidget()
            self.detail.history.clear()
            self.detail.current = None
        if self.detail.open_url(url.toString()):
            self.ui.tabs.setCurrentWidget(self.detail)

    def toggle_tool_details(self, message_id):
        """Expand or collapse a tool counter in whichever transcript is shown."""
        transcript = self.current_transcript()
        if transcript is None or not transcript.toggle_tools(message_id):
            return
        self.ui.parent.chat_scroll.render(force=True)
        self.update_latest()

    def current_transcript(self):
        """The transcript behind the conversation tab: live, or the replay."""
        return self.ui.parent.chat_scroll.shown()

    def open_tool_reference(self, href):
        """Double-click on a receipt opens the data entry recorded for that call."""
        kind, _, reference = href.partition(":")
        transcript = self.current_transcript()
        if transcript is None:
            return
        if kind == "tools":
            self.toggle_tool_details(reference)
            return
        if kind == "tools-head":
            key = transcript.head_tool_key(reference)
            if not key:
                self.toggle_tool_details(reference)
                return
        elif kind == "tools-data":
            key = reference
        else:
            return  # Report, data and audit links keep their single-click behavior.
        if self.centers.show_data(key):
            self.open_object("data", key)
            self.show_panel("right")
        else:
            self.ui.notices.warning("工具数据暂不可用", "该工具调用的数据条目暂不可用，请稍后再查询。")

    def return_from_detail(self):
        self.ui.tabs.setCurrentWidget(self.origin)

    def update_latest(self, *args):
        bar = self.ui.display.verticalScrollBar()
        scroll = getattr(self.ui.parent, 'chat_scroll', None)
        pinned = scroll is not None and scroll.history is not None
        self.ui.latest.setEnabled(pinned or bar.value() < bar.maximum())
        self.ui.latest.setToolTip('有新消息，回到最新' if pinned and scroll.has_updates()
                                 else '回到最新消息' if pinned else '滚动到会话底部')
        self.ui.latest.setAccessibleName(self.ui.latest.toolTip())

    def scroll_to_bottom(self, _checked=False):
        """Scroll the currently selected conversation to its newest message.

        This action deliberately does not touch session navigation. Returning
        from a history session to the launch session is handled by the
        separate ``navigation.return_live`` action.
        """

        self.ui.tabs.setCurrentWidget(self.ui.conversation)
        self.ui.parent.chat_scroll.latest()
        bar = self.ui.display.verticalScrollBar()
        bar.setValue(bar.maximum())

    # Legacy callable alias; retains the scroll-only behavior.
    def show_latest(self, _checked=False):
        self.scroll_to_bottom(_checked)
