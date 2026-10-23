"""Conversation links and detail-page navigation with explicit view dependencies."""

from PyQt5.QtWidgets import QApplication

from sico.core.links import parse_link

from .local_links import local_file_path, open_local_file
from .preview_catalog import open_preview_link, preview_link


class DetailNavigation:
    def __init__(self, widgets, *, detail, centers, page, presentation, show_panel,
                 input_detail, event_detail, reconcile=lambda _value: None,
                 rewind=lambda _value: None, cad_objects=None):
        self.ui = widgets
        self.detail = detail
        self.centers = centers
        self.page = page
        self.presentation = presentation
        self.show_panel = show_panel
        self.input_detail = input_detail
        self.event_detail = event_detail
        self.reconcile = reconcile
        self.rewind = rewind
        self.cad_objects = cad_objects
        self.origin = widgets.conversation

    def open_object(self, kind, key):
        if self.ui.tabs.currentWidget() is not self.detail:
            self.origin = self.ui.tabs.currentWidget()
            self.detail.history.clear()
            self.detail.current = None
        self.detail.open(kind, key)
        self.ui.tabs.setCurrentWidget(self.detail)

    def open_task_node(self, kind, key):
        """Open a task-tree node without inventing a detail for queued input."""
        if kind == "queued":
            self.ui.notices.notice("任务尚未开始", "该执行尚未启动，暂无详情。")
            return
        if kind in {"work", "stage", "task"} and key:
            self.open_object(kind, key)

    def open_object_link(self, url):
        value = url.toString()
        if self.cad_objects is not None and value.startswith(("copilot://cad/", "studio://cad/")):
            self.cad_objects.open_link(value)
            return
        if value.startswith("reconcile:"):
            self.reconcile(value)
            return
        if value.startswith("turn-input:"):
            self.input_detail(value[len("turn-input:"):])
            return
        if value.startswith("event-detail:"):
            self.event_detail(value[len("event-detail:"):])
            return
        if value.startswith("rewind:"):
            self.rewind(value[len("rewind:"):])
            return
        if value.startswith("locate-input:"):
            self.locate_input(value[len("locate-input:"):])
            return
        if value.startswith("copy-request:"):
            execution = self.centers.index.executions.get(value[len("copy-request:"):], {})
            self.copy_text(execution.get("title", ""))
            return
        if value.startswith("copy-result:"):
            execution = self.centers.index.executions.get(value[len("copy-result:"):], {})
            self.copy_text(execution.get("text", ""))
            return
        if value.startswith("work:"):
            self.open_object("work", value[len("work:"):])
            return
        if value.startswith("stage:"):
            self.open_object("stage", value[len("stage:"):])
            return
        if value.startswith("tools:"):
            self.toggle_tool_details(value[len("tools:"):])
            return
        path = local_file_path(value)
        if path is not None:
            if not open_local_file(path):
                self.ui.notices.warning(
                    "文件链接不可用", "文件不存在或系统无法打开：" + str(path))
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

    def locate_input(self, input_id):
        """Select the first committed user message carrying this input ID."""
        transcript = self.current_transcript()
        if transcript is None:
            return False
        message = next((
            row for row in transcript.messages
            if row.get("role") == "user"
            and (row.get("input_id") == input_id or row.get("task_id") == input_id)
        ), None)
        if message is None:
            self.ui.notices.notice(
                "无法在对话中定位",
                "该任务不在当前已加载的对话窗口中；请先打开对应会话。",
            )
            return False
        if not self.ui.parent.chat_scroll.locate(message["message_id"]):
            self.ui.notices.notice(
                "无法在对话中定位",
                "对应消息尚未进入当前对话窗口；请滚动加载后再试。",
            )
            return False
        return True

    @staticmethod
    def copy_text(text):
        QApplication.clipboard().setText(str(text or ""))

    def open_tool_reference(self, href):
        """Double-click on a receipt opens the data entry recorded for that call."""
        name = preview_link(href)
        if name:
            self.open_preview(name)
            return
        self.open_receipt(href)

    def open_receipt(self, href):
        """A ``tools:`` receipt in the transcript opens its recorded data entry."""
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

    def open_preview(self, name):
        """Double-click on an image link opens that product in the image viewer."""
        if not open_preview_link(self.ui.parent, self.centers.source, name):
            self.ui.notices.warning("预览产物暂不可用", "当前会话还没有可浏览的预览产物目录。")
            return False
        self.show_panel("right")
        return True

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
