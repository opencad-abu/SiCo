"""Session commands and their presentation, separated from navigation rendering."""

from PyQt5.QtCore import Qt

from .si_prompt import ask_text, confirm


class NavigationActions:
    def _activity(self, session_id):
        controller = self.window.api.session(session_id)
        return self.window.api.activity(controller) if controller is not None else "idle"

    def _other_usable_sessions(self, session_id):
        """Sessions other than ``session_id`` that the user could still open."""

        others = []
        for item in self._session_items():
            key = item.data(0, Qt.UserRole)
            if key == session_id or item.data(0, Qt.UserRole + 2):
                continue
            others.append(key)
        return others

    def _display_name(self, session_id):
        """Rename default from the latest published catalog metadata."""
        controller = self.window.api.session(session_id)
        if controller is not None and not self.window.api.is_closing(controller):
            try:
                live_name = self.window.api.display_name(controller)
            except (OSError, RuntimeError, ValueError):
                live_name = ""
            if isinstance(live_name, str) and live_name.strip():
                return live_name
        row = self._row(session_id)
        return row.get("name") or row.get("title") or session_id

    def _continue_session(self, session_id):
        """Open the conversation; the service prepares it without a recovery dialog."""

        from .service_connection import service_recovery

        presenter = service_recovery(self.window)
        if presenter is not None:
            self.selected_id = session_id
            presenter.recover(session_id)
            return

        try:
            self.window.activate_session(session_id)
        except (ValueError, OSError, RuntimeError) as exc:
            self._alert("会话未激活：" + str(exc))
            return
        self.selected_id = session_id
        self._progress("正在激活会话…")
        self.catalog_state = None
        self.refresh()

    def _live_session(self, session_id):
        """The live runtime this row names, or ``None`` for a record/ended session."""

        token = self.window.api.session(session_id)
        if token is None or token.runtime_id is None or self.window.api.is_closing(token):
            return None
        return token

    def _end_entry(self, menu, session_id):
        """「结束并归档」：活动会话的唯一去处，结束即归档到历史栏。"""

        from .service_connection import service_ending

        panel = service_ending(self.window)
        if panel is None or self._live_session(session_id) is None:
            return None
        return menu.addAction("结束并归档")

    def _ask_end(self, session_id):
        """The row already named the session; the confirmation belongs to it."""

        from .service_connection import service_ending

        panel = service_ending(self.window)
        if panel is not None:
            panel.confirm(session_id)

    def _rename(self, session_id):
        try:
            window = self.window
            if self._activity(session_id) == "active":
                self._alert(
                    "此会话正在执行任务，暂不能重命名。\n"
                    "请先点击“停止”结束当前任务，再重命名会话。",
                    "重命名会话",
                )
                return
            activation = window.page.activation
            name, ok = ask_text(self.window, "重命名会话", "名称：",
                                value=self._display_name(session_id))
            if not ok or not name.strip():
                return
            if activation != window.page.activation:
                raise ValueError("所选会话已切换，请重新选择")
            window.rename_session(
                session_id, name,
                success=lambda _result: self._operation_finished(),
                failure=lambda exc: self._failed(
                    "重命名失败", exc,
                    "此会话正在执行任务，暂不能重命名。\n请先点击“停止”结束当前任务，再重命名会话。",
                ),
            )
            self._progress("正在重命名会话…")
        except Exception as exc:
            self._failed("重命名失败", exc, "")

    def _delete(self, session_id):
        """Deleting removes a record; an active session must be ended and archived first."""

        try:
            window = self.window
            from .service_connection import service_ending

            if (self._live_session(session_id) is not None
                    and service_ending(window) is not None):
                self._alert(
                    "此会话仍是活动会话，只能先“结束并归档”。\n"
                    "结束并归档后它会成为历史记录，再删除。",
                    "删除会话",
                )
                return
            if self._activity(session_id) == "active":
                self._alert(
                    "此会话正在执行任务，暂不能删除。\n"
                    "请先点击“停止”结束当前任务，再删除会话。",
                    "删除会话",
                )
                return
            if (
                session_id == window.page.session.session_id
                and not self._other_usable_sessions(session_id)
                and service_ending(window) is None
            ):
                # 删掉最后一个可用会话会让窗口没有会话可用（也无法再新建）。
                self._alert(
                    "这是导航中唯一的可用会话，删除后窗口将没有可用会话。\n"
                    "请先新建会话，再删除此会话。",
                    "删除会话",
                )
                return
            if hasattr(window.api, "review_deletion"):
                window.actions.commands.watch(window.api.review_deletion(session_id),
                    success=lambda review: self._confirm_delete(session_id, review),
                    failure=lambda exc: self._failed("删除失败", exc, ""),
                    scope=window.actions.capture())
                self._progress("正在核对会话记录…")
            else:
                self._confirm_delete(session_id)
        except Exception as exc:
            self._failed("删除失败", exc, "")

    def _confirm_delete(self, session_id, review=None):
        window = self.window
        activation = window.page.activation
        reviewed = review is not None and review["requires_review"]
        damaged = review is not None and review.get("damaged", False)
        message = "确定删除此会话及其 Codex 线程？"
        if damaged:
            message = ("此会话内容已缺失或损坏，不能浏览或恢复。\n"
                       "确认删除历史栏中的残留记录？\n"
                       "剩余文件保留供查询；不会启动会话或重发任务。")
        elif reviewed:
            message = (
                "此会话在启动阶段失败，原任务处于待核对状态。\n"
                "记录显示尚未建立 Codex 连接、未派发工具调用，"
                f"有 {review['input_count']} 条未决输入。\n"
                "确认放弃这些输入并删除历史栏中的会话？\n"
                "原始记录与未确认结果保留供查询；不会重发输入或撤销外部操作。"
            )
        title = "删除残留记录" if damaged else "核对后删除" if reviewed else "删除会话"
        if not confirm(window, title, message,
                       choice="放弃并删除" if reviewed and not damaged else "删除", danger=True):
            return
        if activation != window.page.activation:
            raise ValueError("所选会话已切换，请重新选择")
        options = {"review_version": review["version"]} if review is not None else {}
        window.delete_session(session_id,
            success=lambda _result: self._deleted(session_id),
            failure=lambda exc: self._failed("删除失败", exc, ""), **options)
        self._progress("正在删除会话…")

    def _deleted(self, session_id):
        window = self.window
        target = window.api.session(session_id)
        live = target == window.page.session
        if window.page.reviewing == session_id or live:
            window.return_from_preview()
            if live:
                self._disable_input()
                if session_id != window.api.initial_id:
                    self.return_live()
        self._operation_finished()

    def _failed(self, title, exc, stop_first):
        """Explain a refused operation; running sessions ask to stop first."""

        message = str(exc)
        if stop_first and "运行" in message:
            self._alert(stop_first, title)
        else:
            self._alert(title + "：" + message, title)
        return False  # The navigation notice already presented the failure.

    def _operation_finished(self):
        self.catalog_state = None
        self.refresh()

    def return_live(self):
        try:
            window = self.window
            target = window.api.initial_id
            initial = window.api.session(target)
            retained = self.catalog_snapshot["retained"].get(target, {})
            if initial is None or window.api.is_closing(initial) or retained.get("unavailable"):
                if window.page.session.session_id == target:
                    self._disable_input()
                self._alert(
                    "初始会话已删除或不可用，请选择其他会话或重新打开 Silicon Copilot。"
                )
                return
            self.selected_id = ""
            self._sync_input_scope()
            if window.page.reviewing is not None:
                window.return_from_preview()
                self.catalog_state = None
                self.refresh()
                return
            if window.page.session.session_id == target:
                window.return_from_preview()
                return
            window.activate_session(target)
        except Exception as exc:  # A Qt signal/slot must never abort the worker.
            self._alert("当前会话暂不可用：" + str(exc))
